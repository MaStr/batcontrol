"""Exercise outage recovery through the real factory, driver and control loop."""

import pytest

from batcontrol.core import Batcontrol
from batcontrol.inverter import InverterCommunicationError, InverterOutageError
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper


@pytest.fixture
def modbus_loop(mocker):
    """Replace network endpoints and threads, not inverter/core behaviour."""
    clock = mocker.patch(
        'batcontrol.inverter.resilient_wrapper.time.monotonic',
        return_value=1000.0,
    )
    client = mocker.Mock()
    registers = [0] * 24
    registers[0] = 10240
    registers[6] = 5000
    registers[20] = 65534  # SOC scale factor -2: 50 percent
    client.read_holding_registers.return_value = registers
    mocker.patch(
        'batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient',
        return_value=client,
    )
    mocker.patch('batcontrol.core.SchedulerThread')
    for factory, getter, value in (
            ('tariff_factory.create_tarif_provider', 'get_prices', 0.7),
            ('solar_factory.create_solar_provider', 'get_forecast', 0),
            ('consumption_factory.create_consumption', 'get_forecast', 500)):
        provider = mocker.Mock()
        forecast = dict.fromkeys(range(24), value)
        if getter == 'get_prices':
            forecast[0] = 0.46
        getattr(provider, getter).return_value = forecast
        mocker.patch(f'batcontrol.core.{factory}', return_value=provider)

    config = {
        'timezone': 'Europe/Warsaw',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'fronius-modbus',
            'address': '192.0.2.1',
            'capacity': 10240,
            'max_grid_charge_rate': 5000,
            'min_soc': 10,
            'max_soc': 100,
        },
        'utility': {'type': 'awattar_at'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'csv'},
        'battery_control': {
            'type': 'default',
            'max_charging_from_grid_limit': 1.0,
            'always_allow_discharge_limit': 1.0,
            'min_grid_charge_soc': 0.6,
        },
        'battery_control_expert': {'preserve_min_grid_charge_soc': True},
        'mqtt': {'enabled': False},
    }
    controller = Batcontrol(config)
    try:
        controller.run()  # first acknowledged command arms the wrapper
        assert isinstance(controller.inverter, ResilientInverterWrapper)
        assert client.write_register.called
        client.write_register.reset_mock()
        yield controller, client, clock
    finally:
        client.read_holding_registers.side_effect = None
        client.write_register.side_effect = None
        client.connect.side_effect = None
        controller.shutdown()


@pytest.mark.parametrize('failure', [
    TimeoutError('timed out'),
    OSError(113, 'No route to host'),
])
def test_failed_read_skips_cycle_then_recovers_without_stale_decision(
        modbus_loop, failure):
    controller, client, clock = modbus_loop
    last_decision = controller.decision_journal.last_event()
    client.read_holding_registers.side_effect = failure
    client.connect.side_effect = OSError(113, 'No route to host')

    for elapsed in (0, 180, 360):
        clock.return_value = 1000 + elapsed
        controller.run()
        assert controller.decision_journal.last_event() is last_decision
        client.write_register.assert_not_called()

    # The inverter is back with a different SOC; do not reuse the old reading.
    client.read_holding_registers.side_effect = None
    client.read_holding_registers.return_value[6] = 4000
    client.connect.side_effect = None
    clock.return_value = 1540
    controller.run()

    assert controller.battery_state.soc == 40
    assert client.write_register.called
    assert controller.inverter._outage_start is None


def test_static_capacity_and_api_refresh_do_not_hide_read_outage(modbus_loop):
    controller, client, clock = modbus_loop
    client.read_holding_registers.side_effect = TimeoutError('timed out')
    controller.run()

    assert controller.inverter.get_max_capacity() == 10240
    controller.inverter.activate_mqtt(None)
    controller.inverter.refresh_api_values()  # best effort, still offline
    clock.return_value = 2441  # default tolerance is 24 minutes

    with pytest.raises(InverterOutageError) as error:
        controller.run()

    assert error.value.outage_duration_seconds == 1441
    client.write_register.assert_not_called()


def test_successful_reads_do_not_hide_persistent_write_failure(modbus_loop):
    controller, client, clock = modbus_loop
    last_decision = controller.decision_journal.last_event()
    client.write_register.side_effect = TimeoutError('write timed out')
    controller.run()
    assert controller.decision_journal.last_event() is last_decision
    clock.return_value = 2441

    with pytest.raises(InverterOutageError) as error:
        controller.run()

    assert error.value.outage_duration_seconds == 1441


def test_successful_different_mode_supersedes_failed_command(modbus_loop):
    controller, client, _clock = modbus_loop
    client.write_register.side_effect = TimeoutError('write timed out')
    with pytest.raises(InverterCommunicationError):
        controller.inverter.set_mode_force_charge(1000)

    client.write_register.side_effect = None
    controller.inverter.set_mode_allow_discharge()

    assert controller.inverter._outage_start is None


def test_best_effort_success_does_not_clear_failed_command(modbus_loop):
    controller, client, clock = modbus_loop
    client.write_register.side_effect = TimeoutError('write timed out')
    with pytest.raises(InverterCommunicationError):
        controller.inverter.set_mode_force_charge(1000)
    controller.inverter.refresh_api_values()  # telemetry works, writes do not
    clock.return_value = 2441

    with pytest.raises(InverterOutageError):
        controller.inverter.set_mode_force_charge(1000)


def test_read_recovery_does_not_forget_a_failed_command(modbus_loop):
    controller, client, clock = modbus_loop
    client.read_holding_registers.side_effect = TimeoutError('read timed out')
    with pytest.raises(InverterCommunicationError):
        controller.inverter.get_SOC()
    client.write_register.side_effect = TimeoutError('write timed out')
    with pytest.raises(InverterCommunicationError):
        controller.inverter.set_mode_force_charge(1000)

    client.read_holding_registers.side_effect = None
    assert controller.inverter.get_SOC() == 50
    clock.return_value = 2441
    with pytest.raises(InverterOutageError):
        controller.inverter.set_mode_force_charge(1000)

    client.write_register.side_effect = None
    controller.inverter.set_mode_allow_discharge()
    assert controller.inverter._outage_start is None


def test_wall_clock_jump_does_not_extend_outage_tolerance(modbus_loop, mocker):
    controller, client, clock = modbus_loop
    wall_clock = mocker.patch(
        'batcontrol.inverter.resilient_wrapper.time.time', return_value=5000)
    client.read_holding_registers.side_effect = TimeoutError('timed out')
    controller.run()
    wall_clock.return_value = 1  # NTP or manual wall-clock correction
    clock.return_value = 2441

    with pytest.raises(InverterOutageError) as error:
        controller.run()

    assert error.value.outage_duration_seconds == 1441


def test_tolerance_boundary_remains_inclusive(modbus_loop):
    controller, client, clock = modbus_loop
    client.read_holding_registers.side_effect = TimeoutError('timed out')
    controller.run()
    clock.return_value = 2440
    controller.run()  # equality still tolerated, as before
    clock.return_value = 2440.001

    with pytest.raises(InverterOutageError):
        controller.run()
