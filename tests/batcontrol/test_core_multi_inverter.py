"""Tests for running batcontrol with more than one inverter."""
import pytest

from batcontrol.core import (
    Batcontrol,
    MODE_FORCE_CHARGING,
    MODE_LIMIT_BATTERY_CHARGE_RATE,
)
from batcontrol.inverter.group import InverterGroup
from batcontrol.inverter.inverter import Inverter


@pytest.fixture(autouse=True)
def reset_inverter_counter():
    original_value = Inverter.num_inverters
    Inverter.num_inverters = 0

    yield

    Inverter.num_inverters = original_value


def make_config(inverter_config):
    """Minimal batcontrol config around a given inverter section."""
    return {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': inverter_config,
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'simple', 'value': 500},
        'battery_control': {
            'max_charging_from_grid_limit': 0.8,
            'min_price_difference': 0.05,
        },
        'mqtt': {'enabled': False},
    }


def two_dummies(first=None, second=None):
    """Two dummy inverters, optionally with extra config keys each."""
    entry_a = {
        'type': 'dummy',
        'max_grid_charge_rate': 5000,
        'enable_resilient_wrapper': False,
    }
    entry_b = {
        'type': 'dummy',
        'max_grid_charge_rate': 3000,
        'enable_resilient_wrapper': False,
    }
    entry_a.update(first or {})
    entry_b.update(second or {})
    return [entry_a, entry_b]


@pytest.fixture
def patched_providers(mocker):
    """Patch everything except the inverter factory."""
    for target in (
            'batcontrol.core.tariff_factory.create_tarif_provider',
            'batcontrol.core.solar_factory.create_solar_provider',
            'batcontrol.core.consumption_factory.create_consumption'):
        mocker.patch(target, autospec=True, return_value=mocker.MagicMock())


class TestTwoDummyInverters:
    """Two dummy inverters behave like one larger battery."""

    def test_core_builds_an_inverter_group(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        assert isinstance(batcontrol.inverter, InverterGroup)
        assert len(batcontrol.inverter) == 2

    def test_a_single_inverter_mapping_still_yields_no_group(
            self, patched_providers):
        """Regression guard for the existing single-inverter config."""
        batcontrol = Batcontrol(make_config({
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'enable_resilient_wrapper': False,
        }))

        assert not isinstance(batcontrol.inverter, InverterGroup)

    def test_capacities_are_summed(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        # Dummy: 10000 Wh installed, max_soc 95 -> 9500 Wh each
        assert batcontrol.get_max_capacity() == 19000

    def test_stored_energy_is_summed(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        # Dummy: SOC 65 % of 10000 Wh each
        assert batcontrol.get_stored_energy() == 13000

    def test_soc_is_the_weighted_average(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        assert batcontrol.get_SOC() == pytest.approx(65.0)

    def test_grid_charge_rate_limit_is_summed(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        assert batcontrol.inverter.max_grid_charge_rate == 8000

    def test_force_charge_splits_the_rate(self, patched_providers, mocker):
        batcontrol = Batcontrol(make_config(two_dummies()))
        charges = [
            mocker.patch.object(member, 'set_mode_force_charge')
            for member in batcontrol.inverter.inverters
        ]

        batcontrol.force_charge(4000)

        # Both dummies have the same free capacity -> 50/50
        charges[0].assert_called_once_with(2000)
        charges[1].assert_called_once_with(2000)
        assert batcontrol.last_mode == MODE_FORCE_CHARGING
        assert batcontrol.last_charge_rate == 4000

    def test_force_charge_respects_the_weaker_inverter_limit(
            self, patched_providers, mocker):
        batcontrol = Batcontrol(make_config(two_dummies()))
        charges = [
            mocker.patch.object(member, 'set_mode_force_charge')
            for member in batcontrol.inverter.inverters
        ]

        # 50/50 would be 4000 W each, but inverter 1 only takes 3000 W
        batcontrol.force_charge(8000)

        charges[0].assert_called_once_with(5000)
        charges[1].assert_called_once_with(3000)

    def test_force_charge_skips_a_full_inverter(
            self, patched_providers, mocker):
        batcontrol = Batcontrol(make_config(two_dummies()))
        full, empty = batcontrol.inverter.inverters
        full.SOC = full.max_soc  # no free capacity left
        charge = mocker.patch.object(full, 'set_mode_force_charge')
        avoid = mocker.patch.object(full, 'set_mode_avoid_discharge')
        other_charge = mocker.patch.object(empty, 'set_mode_force_charge')

        batcontrol.force_charge(3000)

        # The full battery must not discharge into the charging one
        charge.assert_not_called()
        avoid.assert_called_once()
        other_charge.assert_called_once_with(3000)

    def test_force_charge_is_clipped_to_the_summed_limit(
            self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        batcontrol.force_charge(20000)

        assert batcontrol.last_charge_rate == 8000

    def test_allow_discharging_reaches_both_inverters(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        batcontrol.allow_discharging()

        for member in batcontrol.inverter.inverters:
            assert member.mode == 'allow_discharge'

    def test_avoid_discharging_reaches_both_inverters(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        batcontrol.avoid_discharging()

        for member in batcontrol.inverter.inverters:
            assert member.mode == 'avoid_discharge'

    def test_limit_battery_charge_rate_reaches_both_inverters(
            self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies()))

        batcontrol.limit_battery_charge_rate(2000)

        for member in batcontrol.inverter.inverters:
            assert member.mode == 'limit_battery_charge'
        assert batcontrol.last_mode == MODE_LIMIT_BATTERY_CHARGE_RATE

    def test_shutdown_reaches_both_inverters(self, patched_providers, mocker):
        batcontrol = Batcontrol(make_config(two_dummies()))
        shutdowns = [
            mocker.patch.object(member, 'shutdown')
            for member in batcontrol.inverter.inverters
        ]

        batcontrol.shutdown()

        for shutdown in shutdowns:
            shutdown.assert_called_once()


class TestMultiInverterPvChargeRates:
    """PV charge rate limits are aggregated over the configured inverters."""

    def test_max_pv_charge_rates_are_summed(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies(
            first={'max_pv_charge_rate': 3000},
            second={'max_pv_charge_rate': 2000},
        )))

        assert batcontrol.max_pv_charge_rate == 5000

    def test_one_unlimited_inverter_makes_the_group_unlimited(
            self, patched_providers):
        """0 means 'no limit' for one inverter, so the group has none either."""
        batcontrol = Batcontrol(make_config(two_dummies(
            first={'max_pv_charge_rate': 3000},
        )))

        assert batcontrol.max_pv_charge_rate == 0

    def test_min_pv_charge_rates_are_summed(self, patched_providers):
        batcontrol = Batcontrol(make_config(two_dummies(
            first={'max_pv_charge_rate': 3000, 'min_pv_charge_rate': 100},
            second={'max_pv_charge_rate': 2000, 'min_pv_charge_rate': 150},
        )))

        assert batcontrol.min_pv_charge_rate == 250

    def test_summed_max_caps_the_requested_limit(
            self, patched_providers, mocker):
        batcontrol = Batcontrol(make_config(two_dummies(
            first={'max_pv_charge_rate': 3000},
            second={'max_pv_charge_rate': 2000},
        )))
        limits = [
            mocker.patch.object(member, 'set_mode_limit_battery_charge')
            for member in batcontrol.inverter.inverters
        ]

        batcontrol.limit_battery_charge_rate(9000)

        # Capped to the summed limit 3000 + 2000, then distributed without
        # exceeding either inverter's own max_pv_charge_rate
        limits[0].assert_called_once_with(3000)
        limits[1].assert_called_once_with(2000)
