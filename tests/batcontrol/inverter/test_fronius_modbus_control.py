from batcontrol.inverter.fronius_modbus_commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from batcontrol.inverter.fronius_modbus_control import FroniusModbusControl
from batcontrol.inverter.fronius_modbus_types import RegisterWrite


class RecordingModbusTransport:
    def __init__(self):
        self.writes = []

    def write_registers(self, writes: list[RegisterWrite]):
        self.writes.append(writes)


def test_force_charge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
    )

    control.set_mode_force_charge(3000)

    assert transport.writes == [
        build_force_charge_register_writes(3000, 5000, revert_seconds=900)
    ]


def test_avoid_discharge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
    )

    control.set_mode_avoid_discharge()

    assert transport.writes == [build_avoid_discharge_register_writes(revert_seconds=900)]


def test_allow_discharge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
    )

    control.set_mode_allow_discharge()

    assert transport.writes == [build_allow_discharge_register_writes()]


def test_limit_battery_charge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
    )

    control.set_mode_limit_battery_charge(2000)

    assert transport.writes == [
        build_limit_battery_charge_register_writes(2000, 5000, revert_seconds=900)
    ]


def test_revert_seconds_defaults_to_zero():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(transport, max_charge_rate=5000)

    control.set_mode_force_charge(3000)

    assert transport.writes == [
        build_force_charge_register_writes(3000, 5000, revert_seconds=0)
    ]
