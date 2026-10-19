"""Runtime state groups of the control loop (runtime_state.py).

The values used to be computed inside the MQTT branch of core.py and did not
exist with MQTT disabled. These tests pin that every group is filled by a
control cycle without MQTT, and that MQTT still gets the same numbers.
"""
import pytest

from batcontrol.core import Batcontrol
from batcontrol.logic import PeakShavingConfig
from batcontrol.logic.common import CommonLogic
from batcontrol.logic.decision_trace import Outcome, Reason
from batcontrol.runtime_state import (
    UNKNOWN, BatteryState, as_number, cap_reserved_energy,
)


# -- pure helpers -----------------------------------------------------------

def test_gross_capacity_undoes_the_max_soc_reduction():
    state = BatteryState(max_capacity=9500.0, max_soc=95.0)
    assert state.gross_capacity == pytest.approx(10000.0)


def test_gross_capacity_is_unknown_until_read():
    assert BatteryState().gross_capacity == UNKNOWN


def test_gross_capacity_without_a_max_soc_is_the_max_capacity():
    assert BatteryState(max_capacity=9500.0).gross_capacity == 9500.0


@pytest.mark.parametrize('reserved, expected', [
    (0.0, 0.0),
    (3000.0, 3000.0),
    # window: 10 % min_soc .. 90 % always_allow_discharge of 10 kWh = 8 kWh
    (20000.0, 8000.0),
])
def test_reservation_is_capped_to_the_battery_window(reserved, expected):
    state = BatteryState(max_capacity=9500.0, min_soc=10.0, max_soc=95.0)
    assert cap_reserved_energy(reserved, state, 0.9) == pytest.approx(expected)


def test_reservation_cap_uses_max_soc_below_always_allow_discharge():
    state = BatteryState(max_capacity=8000.0, min_soc=10.0, max_soc=80.0)
    # top is max_soc 80 %, not always_allow_discharge 90 %: 7 kWh
    assert cap_reserved_energy(20000.0, state, 0.9) == pytest.approx(7000.0)


def test_reservation_cap_is_unknown_without_a_capacity():
    assert cap_reserved_energy(1000.0, BatteryState(), 0.9) == UNKNOWN


@pytest.mark.parametrize('value, expected', [
    (5, 5.0), ('7.5', 7.5), (None, UNKNOWN), (True, UNKNOWN), (object(), UNKNOWN)])
def test_as_number(value, expected):
    assert as_number(value) == expected


# -- control loop -----------------------------------------------------------

@pytest.fixture(name='config')
def fixture_config():
    return {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 0,
        },
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'simple', 'value': 500},
        'battery_control': {
            'type': 'default',
            'max_charging_from_grid_limit': 0.8,
            'min_price_difference': 0.05,
        },
        'mqtt': {'enabled': False},
    }


@pytest.fixture(name='core')
def fixture_core(config, mocker):
    """A real Batcontrol with the default logic and mocked providers."""
    module = 'batcontrol.core'
    inverter = mocker.MagicMock()
    inverter.max_pv_charge_rate = 3000
    inverter.max_grid_charge_rate = 5000
    inverter.min_soc = 10
    inverter.max_soc = 95
    inverter.get_max_capacity.return_value = 9500
    inverter.get_SOC.return_value = 50
    inverter.get_stored_energy.return_value = 5000
    inverter.get_stored_usable_energy.return_value = 4000
    inverter.get_free_capacity.return_value = 4500

    tariff = mocker.MagicMock()
    tariff.get_prices.return_value = {0: 0.20, 1: 0.20, 2: 0.20}
    solar = mocker.MagicMock()
    solar.get_forecast.return_value = {0: 800, 1: 1500, 2: 0}
    consumption = mocker.MagicMock()
    consumption.get_forecast.return_value = {0: 500, 1: 500, 2: 500}
    mocker.patch(f'{module}.tariff_factory.create_tarif_provider',
                 autospec=True, return_value=tariff)
    mocker.patch(f'{module}.inverter_factory.create_inverter',
                 autospec=True, return_value=inverter)
    mocker.patch(f'{module}.solar_factory.create_solar_provider',
                 autospec=True, return_value=solar)
    mocker.patch(f'{module}.consumption_factory.create_consumption',
                 autospec=True, return_value=consumption)
    CommonLogic._instance = None  # pylint: disable=protected-access
    batcontrol = Batcontrol(config)
    yield batcontrol
    batcontrol.shutdown()
    CommonLogic._instance = None  # pylint: disable=protected-access


def test_a_cycle_fills_every_group_without_mqtt(core):
    assert core.mqtt_api is None
    assert core.calculation_result is None
    assert core.solar_metrics is None

    core.run()

    battery = core.battery_state
    assert battery.soc == 50
    assert battery.min_soc == 10.0
    assert battery.max_soc == 95.0
    assert battery.max_capacity == 9500
    assert battery.gross_capacity == pytest.approx(10000.0)
    assert battery.always_allow_discharge_limit_capacity == pytest.approx(
        9500 * 0.9)
    assert core.calculation_result is not None
    assert core.calculation_result.min_dynamic_price_difference is not None
    assert core.solar_metrics.solar_active is True
    assert core.peak_shaving_state is not None
    assert core.peak_shaving_state.enabled is False


def test_the_reservation_is_capped_in_the_core(core):
    core.run()
    result = core.calculation_result
    assert result.reserved_energy_effective == cap_reserved_energy(
        result.reserved_energy, core.battery_state, 0.9)


def test_an_evcc_suspension_is_recorded(core, mocker):
    core.peak_shaving_config = PeakShavingConfig(enabled=True)
    core.evcc_api = mocker.MagicMock(evcc_is_charging=True,
                                     evcc_ev_expects_pv_surplus=False)
    core.run()
    state = core.peak_shaving_state
    assert state.enabled is True
    assert state.active is False
    assert state.suspended_by == Reason.EVCC_CHARGING
    assert state.outcome == Outcome.SKIPPED
    assert state.reason == Reason.EVCC_CHARGING


def test_mqtt_gets_the_stored_values(core, mocker):
    core.mqtt_api = mocker.MagicMock()
    core.run()
    mqtt = core.mqtt_api
    metrics = core.solar_metrics
    mqtt.publish_solar_active.assert_called_with(metrics.solar_active)
    mqtt.publish_solar_surplus.assert_called_with(metrics.solar_surplus_wh)
    mqtt.publish_forecast_min_battery.assert_called_with(
        metrics.forecast_min_battery_wh)
    result = core.calculation_result
    mqtt.publish_reserved_energy_capacity.assert_called_with(
        result.reserved_energy)
    mqtt.publish_reserved_energy_capacity_effective.assert_called_with(
        result.reserved_energy_effective)
    mqtt.publish_gross_energy_capacity.assert_called_with(
        pytest.approx(10000.0))


def test_configured_parameters_are_the_startup_values(core):
    core.api_set_min_price_difference(0.2)
    core.api_set_peak_shaving_enabled('true')
    configured = core.configured_parameters
    assert configured.min_price_difference == 0.05
    assert configured.max_charging_from_grid_limit == 0.8
    assert configured.peak_shaving_enabled is False


def test_inverter_values_are_refreshed_for_the_web_api(core, mocker):
    """Without MQTT the inverters are read for the web API only."""
    core.run()
    core.inverter.refresh_api_values.assert_not_called()
    core.web_server = mocker.MagicMock()
    core.run()
    core.inverter.refresh_api_values.assert_called()
