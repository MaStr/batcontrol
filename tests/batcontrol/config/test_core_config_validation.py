"""Batcontrol startup with the battery_control validation (issue #144).

Uses the real dummy inverter behind the ResilientInverterWrapper (the
factory default), so the max_soc cross-check reads the value the way it
does in production. Only the network providers are mocked.
"""
import copy
import logging
from pathlib import Path

import pytest

from batcontrol.config import VALIDATION_ENV
from batcontrol.core import Batcontrol
from batcontrol.inverter.group import InverterGroup
from batcontrol.inverter.inverter import Inverter
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.logic.common import CommonLogic
from batcontrol.setup import load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
DUMMY_CONFIG = REPO_ROOT / 'config' / 'batcontrol_config_dummy.yaml'
CONFIG_LOGGER = 'batcontrol.config'
# Instances started by a test, shut down after it
_STARTED = []


@pytest.fixture(autouse=True)
def isolated_core(mocker, monkeypatch):
    """Mock the network providers, reset singletons and the env switch."""
    for target in (
            'batcontrol.core.tariff_factory.create_tarif_provider',
            'batcontrol.core.solar_factory.create_solar_provider',
            'batcontrol.core.consumption_factory.create_consumption'):
        mocker.patch(target, autospec=True, return_value=mocker.MagicMock())
    monkeypatch.delenv(VALIDATION_ENV, raising=False)
    original_num = Inverter.num_inverters
    Inverter.num_inverters = 0
    CommonLogic._instance = None  # pylint: disable=protected-access
    yield
    while _STARTED:
        _STARTED.pop().shutdown()
    CommonLogic._instance = None  # pylint: disable=protected-access
    Inverter.num_inverters = original_num


def base_config(**battery_control):
    """Minimal config with a dummy inverter."""
    section = {
        'max_charging_from_grid_limit': 0.8,
        'min_price_difference': 0.05,
    }
    section.update(battery_control)
    return {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {'type': 'dummy', 'max_grid_charge_rate': 5000,
                     'max_soc': 95},
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'csv'},
        'battery_control': section,
        'mqtt': {'enabled': False},
    }


def start(config):
    """Construct Batcontrol; it is shut down after the test."""
    bc = Batcontrol(config)
    _STARTED.append(bc)
    return bc


def config_messages(caplog, level=logging.WARNING):
    """Messages of the config validator at or above level."""
    return [record.getMessage() for record in caplog.records
            if record.name.startswith(CONFIG_LOGGER)
            and record.levelno >= level]


def test_dummy_config_starts_without_findings(caplog):
    """The first-run template starts with no warning and no error."""
    with caplog.at_level(logging.INFO):
        bc = start(load_config(str(DUMMY_CONFIG)))
    assert isinstance(bc.inverter, ResilientInverterWrapper)
    assert config_messages(caplog) == []


def test_new_limit_only_warns(caplog):
    """R-L22: a percentage instead of a ratio still starts, with a
    warning (the guard rail: every config that starts today keeps
    starting)."""
    with caplog.at_level(logging.WARNING):
        start(base_config(always_allow_discharge_limit=90))
    messages = config_messages(caplog)
    assert len(messages) == 1
    assert 'always_allow_discharge_limit' in messages[0]
    assert 'not a percentage' in messages[0]


def test_warn_mode_keeps_starting_with_unusable_value(caplog):
    """A non-numeric string the core reads unconverted is logged as an
    error, startup continues as it did before."""
    with caplog.at_level(logging.WARNING):
        bc = start(base_config(min_price_difference='abc'))
    assert bc.min_price_difference == 'abc'
    errors = config_messages(caplog, logging.ERROR)
    assert len(errors) == 1
    assert 'battery_control.min_price_difference' in errors[0]


def test_strict_mode_stops_on_unusable_value(monkeypatch):
    monkeypatch.setenv(VALIDATION_ENV, 'strict')
    with pytest.raises(ValueError,
                       match='battery_control.min_price_difference'):
        Batcontrol(base_config(min_price_difference='abc'))


def test_strict_mode_starts_with_plausibility_warning(monkeypatch, caplog):
    monkeypatch.setenv(VALIDATION_ENV, 'strict')
    with caplog.at_level(logging.WARNING):
        start(base_config(always_allow_discharge_limit=90))
    assert len(config_messages(caplog)) == 1


@pytest.mark.parametrize('mode', ['warn', 'strict'])
def test_numeric_strings_reach_the_core_as_numbers(monkeypatch, caplog,
                                                   mode):
    """Home Assistant add-on: numbers may arrive as strings. They are
    coerced in the dict the core reads, without any message."""
    monkeypatch.setenv(VALIDATION_ENV, mode)
    config = base_config(
        min_price_difference='0.05', min_price_difference_rel=' 0.1 ',
        always_allow_discharge_limit='0.9',
        max_charging_from_grid_limit='0.8', min_recharge_amount='100')
    config['battery_control_expert'] = {
        'round_price_digits': '4', 'production_offset_percent': '0.8'}
    with caplog.at_level(logging.WARNING):
        bc = start(config)
    assert config_messages(caplog) == []
    assert bc.batconfig is config['battery_control']
    assert bc.batconfig == {
        'min_price_difference': 0.05, 'min_price_difference_rel': 0.1,
        'always_allow_discharge_limit': 0.9,
        'max_charging_from_grid_limit': 0.8, 'min_recharge_amount': 100}
    assert bc.min_price_difference == 0.05
    assert bc.min_price_difference_rel == 0.1
    assert bc.max_charging_from_grid_limit == 0.8
    assert bc.general_logic.get_always_allow_discharge_limit() == 0.9
    assert bc.general_logic.min_charge_energy == 100
    assert bc.round_price_digits == 4
    assert bc.production_offset_percent == 0.8
    assert config['battery_control_expert'] == {
        'round_price_digits': 4, 'production_offset_percent': 0.8}


@pytest.mark.parametrize('mode', ['warn', 'strict'])
@pytest.mark.parametrize('value', [0, '0'])
def test_always_allow_zero_stops_startup(monkeypatch, mode, value):
    """0 allows discharging at any SoC, the control never decides."""
    monkeypatch.setenv(VALIDATION_ENV, mode)
    with pytest.raises(ValueError,
                       match='always_allow_discharge_limit is'):
        Batcontrol(base_config(always_allow_discharge_limit=value))


def test_existing_startup_errors_are_unchanged():
    """Values the core already rejects still raise its own error."""
    config = base_config()
    config['battery_control_expert'] = {'round_price_digits': 'four'}
    with pytest.raises(ValueError,
                       match='battery_control_expert.round_price_digits'):
        Batcontrol(config)


def test_unknown_expert_key_warns(caplog):
    """R-L26"""
    config = base_config()
    config['battery_control_expert'] = {'production_ofset_percent': 0.8}
    with caplog.at_level(logging.WARNING):
        start(config)
    assert config_messages(caplog) == [
        '[Config] Unknown key battery_control_expert.production_ofset_percent'
        ' is ignored (typo?)']


class TestMaxSocCrossCheck:
    """R-L29: inverter max_soc below always_allow_discharge_limit."""

    def test_wrapped_inverter_with_low_max_soc_warns(self, caplog):
        config = base_config(always_allow_discharge_limit=0.9)
        config['inverter']['max_soc'] = 80
        with caplog.at_level(logging.WARNING):
            bc = start(config)
        assert isinstance(bc.inverter, ResilientInverterWrapper)
        messages = config_messages(caplog)
        assert len(messages) == 1
        assert 'max_soc (80 %)' in messages[0]

    def test_default_limit_is_checked(self, caplog):
        """No always_allow_discharge_limit configured: the default 0.9."""
        config = base_config()
        config['inverter']['max_soc'] = 85
        with caplog.at_level(logging.WARNING):
            start(config)
        assert len(config_messages(caplog)) == 1

    @pytest.mark.parametrize('limit', [1, 1.0])
    def test_always_allow_off_is_quiet(self, caplog, limit):
        config = base_config(always_allow_discharge_limit=limit)
        config['inverter']['max_soc'] = 80
        with caplog.at_level(logging.WARNING):
            start(config)
        assert config_messages(caplog) == []

    def test_reachable_limit_is_quiet(self, caplog):
        config = base_config(always_allow_discharge_limit=0.9)
        config['inverter']['max_soc'] = 90
        with caplog.at_level(logging.WARNING):
            start(config)
        assert config_messages(caplog) == []

    def test_inverter_group_uses_the_weighted_max_soc(self, caplog):
        config = base_config(always_allow_discharge_limit=0.9)
        member = {'type': 'dummy', 'max_grid_charge_rate': 3000,
                  'max_soc': 80}
        config['inverter'] = [copy.deepcopy(member), copy.deepcopy(member)]
        with caplog.at_level(logging.WARNING):
            bc = start(config)
        assert isinstance(bc.inverter, InverterGroup)
        assert len(config_messages(caplog)) == 1

    def test_unreadable_max_soc_skips_the_check(self, mocker, caplog):
        """Startup never fails because the check cannot read max_soc."""
        broken = mocker.MagicMock()
        type(broken).max_soc = mocker.PropertyMock(
            side_effect=RuntimeError('device offline'))
        broken.get_max_capacity.return_value = 10000
        broken.max_pv_charge_rate = 0
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=broken)
        with caplog.at_level(logging.WARNING):
            start(base_config())
        assert config_messages(caplog) == []


class TestTimeResolution:
    """R-L25: time_resolution_minutes as a string."""

    @pytest.mark.parametrize('value, expected', [
        ('15', 15), ('60', 60), (' 15 ', 15), ('15.0', 15), (60, 60)])
    def test_numeric_strings_are_accepted(self, value, expected):
        config = base_config()
        config['time_resolution_minutes'] = value
        assert start(config).time_resolution == expected

    def test_invalid_string_names_the_key(self):
        config = base_config()
        config['time_resolution_minutes'] = 'quarter'
        with pytest.raises(ValueError, match='time_resolution_minutes'):
            Batcontrol(config)
