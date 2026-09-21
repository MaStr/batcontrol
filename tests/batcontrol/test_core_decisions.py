"""Decision journal integration of Batcontrol: every mode change ends up in the
journal with the trace of steps that led to it, and listeners are notified."""
import pytest

from batcontrol.core import (
    Batcontrol,
    CONTROL_SOURCE_API,
    CONTROL_SOURCE_OPTIMIZER,
    MODE_ALLOW_DISCHARGING,
    MODE_AVOID_DISCHARGING,
    MODE_FORCE_CHARGING,
)
from batcontrol.logic import PeakShavingConfig
from batcontrol.logic.common import CommonLogic
from batcontrol.logic.decision_trace import Decision, Outcome, Reason


class TestCoreDecisionJournal:
    """Run Batcontrol with the real default logic and mocked providers."""

    @pytest.fixture
    def mock_config(self):
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

    @pytest.fixture
    def setup(self, mock_config, mocker):
        bc, inverter, tariff, consumption = self._build(mock_config, mocker)
        yield bc, inverter, tariff, consumption
        bc.shutdown()
        CommonLogic._instance = None  # pylint: disable=protected-access

    @staticmethod
    def _build(mock_config, mocker):
        core_module = 'batcontrol.core'

        inverter = mocker.MagicMock()
        inverter.max_pv_charge_rate = 3000
        inverter.max_grid_charge_rate = 5000
        inverter.get_max_capacity.return_value = 10000
        inverter.get_SOC.return_value = 50
        inverter.get_stored_energy.return_value = 5000
        inverter.get_stored_usable_energy.return_value = 4500
        inverter.get_free_capacity.return_value = 5000

        tariff = mocker.MagicMock()
        tariff.get_prices.return_value = {0: 0.20, 1: 0.20, 2: 0.20}
        solar = mocker.MagicMock()
        solar.get_forecast.return_value = {0: 0, 1: 0, 2: 0}
        consumption = mocker.MagicMock()
        consumption.get_forecast.return_value = {0: 500, 1: 500, 2: 500}

        mocker.patch(f'{core_module}.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=tariff)
        mocker.patch(f'{core_module}.inverter_factory.create_inverter',
                     autospec=True, return_value=inverter)
        mocker.patch(f'{core_module}.solar_factory.create_solar_provider',
                     autospec=True, return_value=solar)
        mocker.patch(f'{core_module}.consumption_factory.create_consumption',
                     autospec=True, return_value=consumption)

        # CommonLogic is a singleton that keeps the limits of whoever created
        # it first; start from a clean one so the results do not depend on
        # the test order.
        CommonLogic._instance = None  # pylint: disable=protected-access
        return Batcontrol(mock_config), inverter, tariff, consumption

    @staticmethod
    def _need_grid_charge(inverter, consumption, tariff):
        inverter.get_stored_energy.return_value = 2000
        inverter.get_stored_usable_energy.return_value = 1500
        inverter.get_free_capacity.return_value = 8000
        tariff.get_prices.return_value = {0: 0.20, 1: 0.35, 2: 0.30}
        consumption.get_forecast.return_value = {0: 1000, 1: 2000, 2: 1500}

    @staticmethod
    def _mode_record(trace):
        return [r for r in trace.records if r.decision == Decision.MODE][-1]

    def test_run_records_trace_ending_with_the_final_mode(self, setup):
        bc, _inverter, _tariff, _consumption = setup

        bc.run()

        trace = bc.decision_journal.latest()
        assert [r.decision for r in trace.records] == [
            Decision.DISCHARGE, Decision.MODE]
        mode = self._mode_record(trace)
        assert mode.outcome == 'allow_discharging'
        assert mode.reason == Reason.USABLE_ENERGY_EXCEEDS_RESERVE
        assert mode.inputs['mode'] == MODE_ALLOW_DISCHARGING
        assert mode.inputs['control_source'] == CONTROL_SOURCE_OPTIMIZER
        assert mode.inputs['decided_by'] == Decision.DISCHARGE
        assert trace.decisive_record().decision == Decision.DISCHARGE
        assert trace.timestamp is not None
        assert bc._pending_trace is None  # pylint: disable=protected-access

    def test_grid_charge_decision_reaches_the_journal(self, setup):
        bc, inverter, tariff, consumption = setup
        self._need_grid_charge(inverter, consumption, tariff)

        bc.run()

        event = bc.decision_journal.last_status_change()
        assert event.mode == MODE_FORCE_CHARGING
        assert [(r.decision, r.outcome) for r in event.trace.records] == [
            (Decision.DISCHARGE, Outcome.FORBIDDEN),
            (Decision.GRID_RECHARGE, Outcome.CHARGE),
            (Decision.MODE, 'force_charge'),
        ]
        mode = self._mode_record(event.trace)
        assert mode.reason == Reason.GRID_RECHARGE_REQUIRED
        assert mode.inputs['charge_rate'] > 0
        inverter.set_mode_force_charge.assert_called_once_with(
            mode.inputs['charge_rate'])

    def test_listener_is_called_on_mode_change_only(self, setup):
        bc, inverter, tariff, consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.run()
        bc.run()
        self._need_grid_charge(inverter, consumption, tariff)
        bc.run()

        assert [(e.previous_mode, e.mode) for e in events] == [
            (None, MODE_ALLOW_DISCHARGING),
            (MODE_ALLOW_DISCHARGING, MODE_FORCE_CHARGING),
        ]
        assert len(bc.decision_journal.history()) == 3

    def test_force_charge_rate_change_by_25_percent_is_an_event(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.force_charge(1000)
        bc.force_charge(1100)    # +10 %: no event
        bc.force_charge(1250)    # +25 % of 1000: event

        assert [(e.kind, e.value, e.previous_value) for e in events] == [
            ('mode', 1000, None),
            ('value', 1250, 1000),
        ]
        mode = self._mode_record(events[-1].trace)
        assert mode.inputs['charge_rate'] == 1250

    def test_api_charge_rate_change_is_an_event(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.api_set_charge_rate(1000)
        bc.api_set_charge_rate(2000)

        assert [(e.kind, e.value) for e in events] == [
            ('mode', 1000), ('value', 2000)]
        assert events[-1].control_source == CONTROL_SOURCE_API

    def test_pv_limit_change_is_an_event(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.limit_battery_charge_rate(1000)
        bc.limit_battery_charge_rate(1200)
        bc.limit_battery_charge_rate(700)

        assert [(e.kind, e.value, e.previous_value) for e in events] == [
            ('mode', 1000, None),
            ('value', 700, 1000),
        ]

    def test_modes_without_value_have_none(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.run()

        assert events[0].mode == MODE_ALLOW_DISCHARGING
        assert events[0].value is None

    def test_decision_sensor_follows_the_status_changes(
            self, mock_config, mocker):
        """With MQTT enabled the Decision sensor is fed by the journal."""
        mqtt_api = mocker.MagicMock()
        mocker.patch('batcontrol.core.MqttApi', return_value=mqtt_api)
        mock_config['mqtt'] = {'enabled': True, 'broker': 'localhost',
                               'port': 1883, 'topic': 'house/batcontrol'}
        bc, inverter, tariff, consumption = self._build(mock_config, mocker)
        try:
            bc.run()
            bc.run()
            self._need_grid_charge(inverter, consumption, tariff)
            bc.run()
        finally:
            bc.shutdown()
            CommonLogic._instance = None  # pylint: disable=protected-access

        events = [c.args[0]
                  for c in mqtt_api.publish_status_change.call_args_list]
        assert [(e.kind, e.mode) for e in events] == [
            ('mode', MODE_ALLOW_DISCHARGING), ('mode', MODE_FORCE_CHARGING)]
        assert events[-1].trace.status_text().startswith('Charge from Grid ')

    def test_no_status_listener_without_mqtt(self, setup):
        bc, _inverter, _tariff, _consumption = setup

        assert bc.decision_journal._listeners == []  # pylint: disable=protected-access

    def test_failing_listener_does_not_break_the_control_cycle(self, setup):
        bc, inverter, _tariff, _consumption = setup

        def failing(_event):
            raise RuntimeError('listener broke')

        bc.decision_journal.add_listener(failing)

        bc.run()

        inverter.set_mode_allow_discharge.assert_called_once_with()
        assert bc.last_mode == MODE_ALLOW_DISCHARGING

    def test_idle_cycle_records_why_nothing_is_charged(self, setup):
        bc, inverter, _tariff, _consumption = setup
        inverter.get_stored_energy.return_value = 400
        inverter.get_stored_usable_energy.return_value = 0
        inverter.get_free_capacity.return_value = 9600

        bc.run()

        mode = self._mode_record(bc.decision_journal.latest())
        assert mode.outcome == 'avoid_discharging'
        assert mode.inputs['mode'] == MODE_AVOID_DISCHARGING
        assert mode.inputs['decided_by'] == Decision.GRID_RECHARGE
        assert mode.reason == Reason.NO_RECHARGE_REQUIRED

    def test_external_discharge_block_overrides_logic_result(self, setup):
        bc, inverter, _tariff, _consumption = setup
        bc.discharge_blocked = True

        bc.run()

        inverter.set_mode_avoid_discharge.assert_called_once_with()
        trace = bc.decision_journal.latest()
        assert [(r.decision, r.reason) for r in trace.records
                if r.decision == Decision.OVERRIDE] == [
            (Decision.OVERRIDE, Reason.EXTERNAL_DISCHARGE_BLOCK)]
        mode = self._mode_record(trace)
        assert mode.outcome == 'avoid_discharging'
        assert mode.reason == Reason.EXTERNAL_DISCHARGE_BLOCK
        assert mode.inputs['decided_by'] == Decision.OVERRIDE

    def test_set_discharge_blocked_is_traced(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.set_discharge_blocked(True)

        assert len(events) == 1
        assert events[0].mode == MODE_AVOID_DISCHARGING
        assert self._mode_record(events[0].trace).reason == \
            Reason.EXTERNAL_DISCHARGE_BLOCK

    def test_api_mode_request_is_traced(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.api_set_mode(MODE_FORCE_CHARGING)

        assert len(events) == 1
        assert events[0].control_source == CONTROL_SOURCE_API
        mode = self._mode_record(events[0].trace)
        assert mode.reason == Reason.API_REQUEST
        assert mode.inputs['control_source'] == CONTROL_SOURCE_API
        assert mode.inputs['decided_by'] == Decision.OVERRIDE

    def test_api_request_does_not_reuse_a_stale_trace(self, setup):
        """A trace staged for the optimizer must never leak into a later
        API request."""
        bc, _inverter, _tariff, _consumption = setup
        bc.run()

        bc.api_set_mode(MODE_FORCE_CHARGING)

        trace = bc.decision_journal.latest()
        assert [r.decision for r in trace.records] == [
            Decision.OVERRIDE, Decision.MODE]

    def test_forecast_error_fallback_is_traced(self, setup):
        bc, _inverter, _tariff, _consumption = setup
        bc.time_at_forecast_error = 1  # long ago
        events = []
        bc.decision_journal.add_listener(events.append)

        bc.handle_forecast_error()

        assert len(events) == 1
        assert events[0].mode == MODE_ALLOW_DISCHARGING
        assert self._mode_record(events[0].trace).reason == \
            Reason.FORECAST_ERROR_FALLBACK

    def test_calculation_failure_fallback_is_traced(self, setup, mocker):
        bc, inverter, _tariff, _consumption = setup
        failing_logic = mocker.MagicMock()
        failing_logic.calculate.return_value = False
        mocker.patch('batcontrol.core.LogicFactory.create_logic',
                     return_value=failing_logic)

        bc.run()

        inverter.set_mode_allow_discharge.assert_called_once_with()
        mode = self._mode_record(bc.decision_journal.latest())
        assert mode.reason == Reason.CALCULATION_FAILED
        assert bc._pending_trace is None  # pylint: disable=protected-access

    @pytest.mark.parametrize('charging, expects_pv, reason', [
        (True, False, Reason.EVCC_CHARGING),
        (False, True, Reason.EVCC_EV_EXPECTS_PV_SURPLUS),
    ])
    def test_evcc_peak_shaving_override_is_traced(
            self, setup, mocker, charging, expects_pv, reason):
        bc, _inverter, _tariff, _consumption = setup
        bc.peak_shaving_config = PeakShavingConfig(enabled=True)
        bc.evcc_api = mocker.MagicMock(evcc_is_charging=charging,
                                       evcc_ev_expects_pv_surplus=expects_pv)

        bc.run()

        trace = bc.decision_journal.latest()
        first = trace.records[0]
        assert (first.decision, first.outcome, first.reason) == (
            Decision.PEAK_SHAVING, Outcome.SKIPPED, reason)
        assert not first.decisive
