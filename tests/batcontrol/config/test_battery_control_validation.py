"""Tests for the Pydantic validation of battery_control and
battery_control_expert (issue #144 stage 1, TODO R-L22..R-L29).

Guard rail: every configuration that starts today must still start. The
models only warn about new limits; errors are values the code cannot use.
"""
import copy
import logging
import math
import re
from pathlib import Path

import pytest
import yaml

from batcontrol.config import (
    MODE_STRICT,
    MODE_WARN,
    VALIDATION_ENV,
    check_battery_control,
    validate_battery_control,
    validation_mode,
)
from batcontrol.config.battery_control import check_max_soc
from batcontrol.setup import load_config

REPO_ROOT = Path(__file__).resolve().parents[3]
DUMMY_CONFIG = REPO_ROOT / 'config' / 'batcontrol_config_dummy.yaml'
DOCS_ROOT = REPO_ROOT / 'docs'

VALID_BATTERY_CONTROL = {
    'type': 'default',
    'min_price_difference': 0.05,
    'min_price_difference_rel': 0.10,
    'always_allow_discharge_limit': 0.90,
    'max_charging_from_grid_limit': 0.80,
    'min_recharge_amount': 100,
}


def make_config(battery_control=None, expert=None):
    """A config with only the two sections of interest."""
    config = {'battery_control': copy.deepcopy(
        VALID_BATTERY_CONTROL if battery_control is None else battery_control)}
    if expert is not None:
        config['battery_control_expert'] = expert
    return config


def with_value(key, value):
    """VALID_BATTERY_CONTROL with one value replaced."""
    section = copy.deepcopy(VALID_BATTERY_CONTROL)
    section[key] = value
    return make_config(section)


def fenced_blocks(text):
    """(line number, body) of every fenced code block in a Markdown text."""
    blocks = []
    body = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith('```'):
            if body is None and '```' in stripped[3:]:
                continue  # inline code written with a fence
            if body is None:
                body, start, indent = [], number, line.index('`')
            else:
                blocks.append((start, '\n'.join(body)))
                body = None
        elif body is not None:
            body.append(line[indent:])
    return blocks


def doc_yaml_blocks():
    """Fenced code blocks in docs/ that contain one of the two sections."""
    section = re.compile(r'^battery_control(_expert)?:', re.MULTILINE)
    return [
        pytest.param(body, id=f'{page.relative_to(DOCS_ROOT)}:{start}')
        for page in sorted(DOCS_ROOT.rglob('*.md'))
        for start, body in fenced_blocks(page.read_text(encoding='utf-8'))
        if section.search(body)
    ]


class TestExampleConfigs:
    """The shipped configurations validate without any finding."""

    def test_dummy_config_is_clean(self):
        report = check_battery_control(
            load_config(str(DUMMY_CONFIG)), inverter_max_soc=95)
        assert report.errors == []
        assert report.warnings == []

    def test_doc_examples_were_found(self):
        assert len(doc_yaml_blocks()) >= 8

    @pytest.mark.parametrize('block', doc_yaml_blocks())
    def test_doc_example_is_clean(self, block):
        fragment = yaml.safe_load(block)
        # Fragments without battery_control (expert only examples) are
        # completed with the valid default section.
        fragment.setdefault('battery_control', VALID_BATTERY_CONTROL)
        report = check_battery_control(fragment, inverter_max_soc=100)
        assert report.errors == []
        assert report.warnings == []

    def test_empty_sections_use_the_defaults(self):
        report = check_battery_control(make_config({}, {}))
        assert report.errors == []
        assert report.warnings == []


class TestLimitWarnings:
    """One test per new limit: a warning, never an error."""

    @pytest.mark.parametrize('key, value', [
        ('always_allow_discharge_limit', 90),
        ('always_allow_discharge_limit', -0.1),
        ('always_allow_discharge_limit', math.nan),
        ('max_charging_from_grid_limit', 80),
        ('max_charging_from_grid_limit', -0.1),
        ('min_price_difference', -0.01),
        ('min_price_difference_rel', -0.1),
        ('min_recharge_amount', -100),
    ])
    def test_battery_control_limit(self, key, value):
        report = check_battery_control(with_value(key, value))
        assert report.errors == []
        assert len(report.warnings) == 1
        assert f'battery_control.{key}' in report.warnings[0]

    @pytest.mark.parametrize('key', [
        'always_allow_discharge_limit', 'max_charging_from_grid_limit'])
    def test_percentage_instead_of_ratio_gets_a_hint(self, key):
        report = check_battery_control(with_value(key, 90))
        assert 'not a percentage' in report.warnings[0]

    @pytest.mark.parametrize('key, value', [
        ('always_allow_discharge_limit', 0),
        ('always_allow_discharge_limit', 1),
        ('max_charging_from_grid_limit', 0),
        ('max_charging_from_grid_limit', 1.0),
        ('min_price_difference', 0),
        ('min_price_difference_rel', 0),
        ('min_recharge_amount', 0),
    ])
    def test_limits_are_inclusive(self, key, value):
        """The same inclusive ranges as the MQTT/API setters."""
        report = check_battery_control(with_value(key, value))
        assert report.warnings == []
        assert report.errors == []

    def test_negative_round_price_digits(self):
        """R-L23: round(x, -2) turns every price into 0."""
        report = check_battery_control(
            make_config(expert={'round_price_digits': -2}))
        assert report.errors == []
        assert len(report.warnings) == 1
        assert 'round_price_digits' in report.warnings[0]

    def test_round_price_digits_zero_is_fine(self):
        report = check_battery_control(
            make_config(expert={'round_price_digits': 0}))
        assert report.warnings == []

    @pytest.mark.parametrize('value, warns', [
        (2.5, True), (2.0, False), (0.0, False), (0.8, False), (100, True)])
    def test_production_offset_above_api_limit(self, value, warns):
        """R-L24: the API accepts 0..2, the config any value >= 0."""
        report = check_battery_control(
            make_config(expert={'production_offset_percent': value}))
        assert report.errors == []
        assert bool(report.warnings) is warns


class TestUnknownKeys:
    """R-L26: typos are reported, not silently ignored."""

    def test_unknown_expert_key(self):
        report = check_battery_control(make_config(
            expert={'round_price_digit': 4, 'production_offset_percent': 1.0}))
        assert report.errors == []
        assert report.warnings == [
            'Unknown key battery_control_expert.round_price_digit is '
            'ignored (typo?)']

    def test_unknown_battery_control_key(self):
        report = check_battery_control(
            with_value('always_allow_discharge', 0.9))
        assert report.warnings == [
            'Unknown key battery_control.always_allow_discharge is '
            'ignored (typo?)']

    def test_non_string_key_is_only_a_warning(self):
        """YAML allows `1: x`; the code ignores it, so no error."""
        config = make_config(expert={1: 'x', 'round_price_digits': 4})
        report = check_battery_control(config)
        assert report.errors == []
        assert report.warnings == [
            'Unknown key battery_control_expert.1 is ignored (typo?)']

    def test_deprecated_charge_rate_multiplier_is_known(self):
        """The core warns about the deprecation itself."""
        report = check_battery_control(
            with_value('charge_rate_multiplier', 1.1))
        assert report.warnings == []
        assert report.errors == []


class TestErrors:
    """Values the code cannot use are errors."""

    @pytest.mark.parametrize('value', ['0.05', 'abc', None, [0.05]])
    @pytest.mark.parametrize('key', [
        'min_price_difference', 'min_price_difference_rel',
        'always_allow_discharge_limit', 'max_charging_from_grid_limit',
        'min_recharge_amount'])
    def test_unparsed_numeric_value_must_be_a_number(self, key, value):
        """R-L22: the core reads these unconverted; a string fails later
        with a TypeError in the control cycle."""
        report = check_battery_control(with_value(key, value))
        assert len(report.errors) == 1
        assert report.errors[0].startswith(f'battery_control.{key} ')

    @pytest.mark.parametrize('value', [True, False])
    @pytest.mark.parametrize('key', [
        'min_price_difference', 'min_price_difference_rel',
        'always_allow_discharge_limit', 'max_charging_from_grid_limit',
        'min_recharge_amount'])
    def test_bool_is_only_a_warning(self, key, value):
        """A bool is an int subclass; the core computes with it today,
        so it must not stop startup, not even in strict mode."""
        report = check_battery_control(with_value(key, value))
        assert report.errors == []
        assert len(report.warnings) == 1
        assert f'battery_control.{key} is {value!r}' in report.warnings[0]
        validate_battery_control(with_value(key, value), mode=MODE_STRICT)

    @pytest.mark.parametrize('value', ['DEFAULT', 'Next', 'default'])
    def test_logic_type_is_case_insensitive(self, value):
        report = check_battery_control(with_value('type', value))
        assert report.errors == []

    def test_unknown_logic_type(self):
        report = check_battery_control(with_value('type', 'fancy'))
        assert report.errors == [
            "battery_control.type must be one of ('default', 'next'), "
            "got 'fancy'"]

    def test_existing_parser_message_is_kept(self):
        """Values the core already rejects carry the core's message."""
        report = check_battery_control(make_config(
            expert={'soften_price_difference_on_charging_factor': 0}))
        assert report.errors == [
            'battery_control_expert.soften_price_difference_on_charging_factor'
            ' must be a positive number, got 0']

    @pytest.mark.parametrize('expert', [
        {'charge_rate_multiplier': 0},
        {'soften_price_difference_on_charging': 'maybe'},
        {'spread_grid_charge_over_charge_window': 2},
        {'round_price_digits': 2.5},
        {'production_offset_percent': -1},
        {'preserve_min_grid_charge_soc': 'sometimes'},
        {'market_price_refresh_time': '25:00'},
        {'max_forecast_hours': -1},
    ])
    def test_expert_values_the_core_rejects(self, expert):
        report = check_battery_control(make_config(expert=expert))
        assert len(report.errors) == 1
        assert f'battery_control_expert.{next(iter(expert))}' \
            in report.errors[0]

    @pytest.mark.parametrize('key, value', [
        ('min_grid_charge_soc', 1.5),
        ('grid_charge_target_strategy', 'sometimes'),
    ])
    def test_battery_control_values_the_core_rejects(self, key, value):
        report = check_battery_control(with_value(key, value))
        assert len(report.errors) == 1
        assert f'battery_control.{key}' in report.errors[0]

    def test_expert_section_must_be_a_mapping(self):
        report = check_battery_control(make_config(expert=['a']))
        assert report.errors == [
            'battery_control_expert must be a mapping of key: value '
            'settings, got list']

    def test_missing_battery_control(self):
        report = check_battery_control({})
        assert report.errors == ['Section battery_control is missing']

    def test_field_warnings_survive_an_error_elsewhere(self):
        section = copy.deepcopy(VALID_BATTERY_CONTROL)
        section['always_allow_discharge_limit'] = 90
        section['min_price_difference'] = 'abc'
        report = check_battery_control(make_config(section))
        assert len(report.errors) == 1
        assert len(report.warnings) == 1


class TestMaxSocCrossCheck:
    """R-L29: inverter max_soc below always_allow_discharge_limit."""

    def test_unreachable_limit_warns(self):
        warning = check_max_soc(0.9, 80)
        assert warning is not None
        assert 'max_soc (80 %)' in warning

    @pytest.mark.parametrize('limit, max_soc', [
        (0.9, 90), (0.9, 95), (0.9, 100), (0.5, 80)])
    def test_reachable_limit_is_fine(self, limit, max_soc):
        assert check_max_soc(limit, max_soc) is None

    @pytest.mark.parametrize('limit, max_soc', [
        (0.9, None), (0.9, -1), (0.9, 'x'), (0.9, True), ('0.9', 80),
        (90, 80), (0.9, math.nan)])
    def test_unknown_or_invalid_values_are_skipped(self, limit, max_soc):
        """Unknown max_soc or a limit with its own warning: no second
        message."""
        assert check_max_soc(limit, max_soc) is None

    @pytest.mark.parametrize('percent', range(1, 101))
    def test_limit_equal_to_max_soc_is_reachable(self, percent):
        """Same comparison as the runtime check: 0.55 * 100 is
        55.00000000000001, but 55 / 100 >= 0.55 holds."""
        assert check_max_soc(percent / 100, percent) is None

    def test_check_uses_the_default_limit(self):
        config = make_config({})
        report = check_battery_control(config, inverter_max_soc=85)
        assert len(report.warnings) == 1


class TestModes:
    """BATCONTROL_CONFIG_VALIDATION: warn (default) or strict."""

    def test_default_is_warn(self, monkeypatch):
        monkeypatch.delenv(VALIDATION_ENV, raising=False)
        assert validation_mode() == MODE_WARN

    @pytest.mark.parametrize('raw, expected', [
        ('strict', MODE_STRICT), (' STRICT ', MODE_STRICT),
        ('warn', MODE_WARN), ('', MODE_WARN)])
    def test_known_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv(VALIDATION_ENV, raw)
        assert validation_mode() == expected

    def test_unknown_value_falls_back_to_warn(self, monkeypatch, caplog):
        monkeypatch.setenv(VALIDATION_ENV, 'off')
        with caplog.at_level(logging.WARNING):
            assert validation_mode() == MODE_WARN
        assert VALIDATION_ENV in caplog.text

    def test_warn_logs_errors_and_does_not_raise(self, caplog):
        with caplog.at_level(logging.WARNING):
            report = validate_battery_control(
                with_value('min_price_difference', 'abc'), mode=MODE_WARN)
        assert len(report.errors) == 1
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert 'battery_control.min_price_difference' in errors[0].getMessage()

    def test_strict_raises_on_errors(self):
        with pytest.raises(ValueError,
                           match='battery_control.min_price_difference'):
            validate_battery_control(
                with_value('min_price_difference', 'abc'), mode=MODE_STRICT)

    def test_strict_keeps_plausibility_findings_as_warnings(self, caplog):
        with caplog.at_level(logging.WARNING):
            report = validate_battery_control(
                with_value('always_allow_discharge_limit', 90),
                mode=MODE_STRICT)
        assert report.errors == []
        assert 'always_allow_discharge_limit' in caplog.text

    def test_internal_failure_never_stops_warn_mode(self, mocker, caplog):
        mocker.patch('batcontrol.config.validation.check_battery_control',
                     side_effect=RuntimeError('bug'))
        with caplog.at_level(logging.ERROR):
            report = validate_battery_control(make_config(), mode=MODE_WARN)
        assert report.errors == []
        assert 'failed unexpectedly' in caplog.text

    def test_internal_failure_raises_in_strict_mode(self, mocker):
        mocker.patch('batcontrol.config.validation.check_battery_control',
                     side_effect=RuntimeError('bug'))
        with pytest.raises(RuntimeError):
            validate_battery_control(make_config(), mode=MODE_STRICT)

    def test_mode_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(VALIDATION_ENV, 'strict')
        with pytest.raises(ValueError):
            validate_battery_control(with_value('min_recharge_amount', '1'))

    def test_validation_never_changes_the_config(self):
        config = make_config(
            with_value('min_price_difference', 1)['battery_control'],
            {'round_price_digits': '4', 'production_offset_percent': '0.8'})
        before = copy.deepcopy(config)
        validate_battery_control(config, mode=MODE_WARN)
        assert config == before
        assert config['battery_control']['min_price_difference'] == 1
        assert isinstance(
            config['battery_control_expert']['round_price_digits'], str)
