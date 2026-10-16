"""Tests for the read-only service layer."""
import dataclasses
import datetime
import time

import pytest

from batcontrol.decision_journal import DecisionJournal

from batcontrol.web.service import (
    BatcontrolService, NoDataAvailable, NotFound,
)


def test_health_answers_before_the_first_cycle(empty_bc):
    """A container health check must work during startup."""
    health = BatcontrolService(empty_bc).health()
    assert health['status'] == 'ok'
    assert health['has_data'] is False
    assert health['last_evaluation'] is None
    assert health['cycle_age_seconds'] is None
    assert health['stale'] is False


def test_a_loop_that_never_completes_a_cycle_goes_stale(empty_bc):
    """Regression: with last_run_time 0 the API reported online forever."""
    service = BatcontrolService(empty_bc)
    service._started -= 3600  # pylint: disable=protected-access
    assert service.health()['stale'] is True
    assert service.status()['online'] is False


def test_forecasts_failing_from_startup_make_the_api_stale(empty_bc):
    """Regression: cycles run (last_run_time advances) but every forecast
    fails, so no snapshot is ever published."""
    empty_bc.last_run_time = time.time() - 10
    service = BatcontrolService(empty_bc)
    assert service.status()['online'] is True  # just started
    service._started -= 3600  # pylint: disable=protected-access
    assert service.status()['online'] is False


def test_failing_forecasts_make_the_api_stale(fake_bc):
    """Regression: a cycle that aborts on a forecast error advances
    last_run_time but publishes no snapshot, so the old snapshot decides."""
    fake_bc.forecast_snapshot = dataclasses.replace(
        fake_bc.forecast_snapshot, run_time=time.time() - 3600)
    assert BatcontrolService(fake_bc).status()['online'] is False


def test_current_follows_the_clock_when_the_snapshot_ages(fake_bc):
    """Regression: an old snapshot served its slot 0 as the current price."""
    fake_bc.forecast_snapshot = dataclasses.replace(
        fake_bc.forecast_snapshot, run_time=time.time() - 2 * 3600)
    service = BatcontrolService(fake_bc)
    assert service.current()['price']['current'] == 0.201
    forecast = service.forecast('price')
    assert forecast['total_count'] == 2
    assert [s['slot'] for s in forecast['slots']] == [0, 1]
    assert forecast['slots'][0]['value'] == 0.201


def test_an_expired_forecast_is_no_data(fake_bc):
    fake_bc.forecast_snapshot = dataclasses.replace(
        fake_bc.forecast_snapshot, run_time=time.time() - 10 * 3600)
    with pytest.raises(NoDataAvailable) as excinfo:
        BatcontrolService(fake_bc).current()
    assert excinfo.value.code == 'forecast_expired'


def test_forecast_all_reads_the_snapshot_once(fake_bc, monkeypatch):
    """Regression: four reads could mix two cycles in one response."""
    service = BatcontrolService(fake_bc)
    reads = []
    original = BatcontrolService.require_snapshot

    def counting(self):
        reads.append(1)
        return original(self)
    monkeypatch.setattr(BatcontrolService, 'require_snapshot', counting)
    service.forecast_all()
    assert len(reads) == 1


def test_mode_since_ignores_value_changes(fake_bc):
    """Regression: a charge rate change reset "mode since"."""
    first = fake_bc.record_decision()
    later = fake_bc.record_decision()
    later.timestamp = first.timestamp + datetime.timedelta(minutes=5)
    journal = fake_bc.decision_journal = DecisionJournal()
    journal.commit(first, fake_bc.last_mode, 'optimizer', 1000)
    journal.commit(later, fake_bc.last_mode, 'optimizer', 5000)
    assert journal.last_status_change().trace is later
    since = BatcontrolService(fake_bc).status()['mode_changed_at']
    assert since == int(first.timestamp.timestamp() * 1000)


def test_health_reports_a_stalled_loop(fake_bc):
    fake_bc.last_run_time = 1.0  # far in the past
    health = BatcontrolService(fake_bc).health()
    assert health['stale'] is True


def test_version_exposes_the_interval(fake_bc):
    version = BatcontrolService(fake_bc).version()
    assert version['interval_minutes'] == 60
    assert version['timezone'] == 'Europe/Berlin'
    assert version['version']


def test_status_labels_the_mode(fake_bc):
    status = BatcontrolService(fake_bc).status()
    assert status['mode'] == 10
    assert status['mode_code'] == 'allow_discharging'
    assert status['mode_label'] == 'ALLOW DISCHARGE'
    assert status['control_source'] == 'optimizer'


def test_status_derives_the_next_evaluation(fake_bc):
    status = BatcontrolService(fake_bc).status()
    assert status['next_evaluation'] - status['last_evaluation'] == 180 * 1000


def test_status_without_a_cycle_has_no_timestamps(empty_bc):
    status = BatcontrolService(empty_bc).status()
    assert status['last_evaluation'] is None
    assert status['next_evaluation'] is None


def test_battery_reports_capacities_and_limits(fake_bc):
    battery = BatcontrolService(fake_bc).battery()
    assert battery['soc'] == 87.4
    assert battery['max_energy_capacity'] == 11000.0
    assert battery['always_allow_discharge_limit_percent'] == 90.0
    assert battery['soc_limits'] == {'min_soc': 7.0, 'max_soc': 100.0}


def test_unread_inverter_values_are_unknown_not_negative(fake_bc):
    """The core initialises its caches to -1 until the inverter was read."""
    fake_bc.last_SOC = -1
    fake_bc.last_max_capacity = -1
    fake_bc.last_min_soc = -1
    battery = BatcontrolService(fake_bc).battery()
    assert battery['soc'] is None
    assert battery['max_energy_capacity'] is None
    assert battery['soc_limits']['min_soc'] is None


def test_current_takes_the_price_from_slot_zero(fake_bc):
    """Independent of which decision path ran - that was the prototype's bug."""
    current = BatcontrolService(fake_bc).current()
    assert current['price']['current'] == 0.308
    assert current['price']['unit'] == 'EUR/kWh'
    assert current['price']['interval_minutes'] == 60
    assert current['price']['min_dynamic_price_difference'] == 0.0612
    assert current['soc_limits']['min_soc'] == 7.0


def test_current_without_data_raises(empty_bc):
    with pytest.raises(NoDataAvailable):
        BatcontrolService(empty_bc).current()


@pytest.mark.parametrize('name', [
    'price', 'consumption', 'production', 'net_consumption', None,
])
def test_forecasts_require_a_completed_cycle(empty_bc, name):
    service = BatcontrolService(empty_bc)
    with pytest.raises(NoDataAvailable):
        if name is None:
            service.forecast_all()
        else:
            service.forecast(name)


def test_price_forecast_has_no_power_field(fake_bc):
    forecast = BatcontrolService(fake_bc).forecast('price')
    assert forecast['unit'] == 'EUR/kWh'
    assert forecast['total_count'] == 4
    assert 'power_w' not in forecast['slots'][0]


@pytest.mark.parametrize('name', [
    'consumption', 'production', 'net_consumption',
])
def test_energy_forecasts_carry_power(fake_bc, name):
    forecast = BatcontrolService(fake_bc).forecast(name)
    assert forecast['unit'] == 'Wh'
    assert 'power_w' in forecast['slots'][0]


def test_net_consumption_keeps_the_surplus_sign(fake_bc):
    """Negative net consumption means surplus solar energy."""
    slots = BatcontrolService(fake_bc).forecast('net_consumption')['slots']
    assert slots[0]['value'] == 450.0
    assert slots[2]['value'] == -500.0


def test_forecast_all_returns_every_series(fake_bc):
    forecast = BatcontrolService(fake_bc).forecast_all()
    for key in ('price', 'consumption', 'production', 'net_consumption'):
        assert len(forecast[key]) == 4
    assert forecast['interval_minutes'] == 60


def test_decision_requires_a_recorded_trace(fake_bc):
    with pytest.raises(NotFound):
        BatcontrolService(fake_bc).decision()


def test_decision_exposes_trace_and_decisive_record(fake_bc):
    fake_bc.record_decision()
    decision = BatcontrolService(fake_bc).decision()
    assert decision['decisive']['reason'] == 'ALWAYS_ALLOW_DISCHARGE_LIMIT'
    assert decision['trace']['records']
    assert decision['status_text']
    assert decision['event']['mode'] == 10


def test_decision_history_is_limited(fake_bc):
    for _ in range(5):
        fake_bc.record_decision()
    history = BatcontrolService(fake_bc).decision_history(limit=3)
    assert history['count'] == 3
    assert history['limit'] == 3


def test_configuration_exposes_runtime_parameters(fake_bc):
    config = BatcontrolService(fake_bc).configuration()
    assert config['battery_control']['min_price_difference'] == 0.05
    assert config['battery_control']['type'] == 'default'
    assert config['peak_shaving']['enabled'] is False
    assert config['interval_minutes'] == 60


def test_configuration_never_reads_the_config_dict(fake_bc):
    """The whitelist is built from core attributes, so secrets cannot leak.

    The stand-in has no `config` attribute at all: if the service ever starts
    reading the raw configuration dictionary, this test fails immediately.
    """
    assert not hasattr(fake_bc, 'config')
    assert BatcontrolService(fake_bc).configuration()


def test_service_never_calls_inverter_accessors(fake_bc):
    """A request must not trigger Modbus or HTTP traffic to the inverter."""
    def explode(*_args, **_kwargs):
        raise AssertionError('the API must not read the inverter')

    for name in ('get_SOC', 'get_max_capacity', 'get_stored_energy',
                 'get_stored_usable_energy', 'get_free_capacity'):
        setattr(fake_bc, name, explode)

    class _Untouchable:
        """An InverterGroup derives min_soc/max_soc from every member's
        capacity, which can mean inverter traffic."""
        def __getattr__(self, name):
            raise AssertionError(f'the API must not read inverter.{name}')
    fake_bc.inverter = _Untouchable()

    service = BatcontrolService(fake_bc)
    fake_bc.record_decision()
    service.health()
    service.status()
    service.battery()
    service.current()
    service.forecast_all()
    service.decision()
    service.configuration()


def test_decision_separates_the_latest_trace_from_the_last_change(fake_bc):
    """The two have different ages and must not be presented as one.

    `trace` is refreshed every control cycle; `event` only moves on a mode or
    value change, or the journal's 15-minute refresh. Rendering the event's
    records next to the current mode shows a "why" that can be a quarter of
    an hour older than what it sits beside.
    """
    fake_bc.record_decision()
    decision = BatcontrolService(fake_bc).decision()
    assert decision['decided_at'] is not None
    assert decision['event_at'] is not None
    # both ages are exposed, so a consumer can tell them apart
    assert 'records' in decision['trace']
