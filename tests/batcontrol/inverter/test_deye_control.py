from batcontrol.inverter.deye.commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from batcontrol.inverter.deye.control import DeyeControl
from batcontrol.inverter.deye.reads import REG_BATTERY_TEMPERATURE
from batcontrol.inverter.deye.types import RegisterRead, RegisterWrite


class RecordingModbusTransport:
    def __init__(self, reads=None):
        self.reads = reads or {}
        self.writes = []

    def read_registers(self, register, count):
        return self.reads[(register, count)]

    def write_registers(self, writes: list[RegisterWrite]):
        self.writes.append(writes)


def _telemetry_read(soc: int) -> RegisterRead:
    return RegisterRead(
        start_register=REG_BATTERY_TEMPERATURE,
        values=[1250, 5320, soc, 0, 0, 0],
    )


def test_force_charge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = DeyeControl(transport, nominal_battery_voltage=48, min_soc=5, max_soc=95)

    control.set_mode_force_charge(960)

    assert transport.writes == [
        build_force_charge_register_writes(960, 48, max_soc=95)
    ]


def test_avoid_discharge_reads_current_soc_and_writes_command_builder_output():
    transport = RecordingModbusTransport(
        reads={(REG_BATTERY_TEMPERATURE, 6): _telemetry_read(57)}
    )
    control = DeyeControl(transport, nominal_battery_voltage=48, min_soc=5, max_soc=95)

    control.set_mode_avoid_discharge()

    assert transport.writes == [build_avoid_discharge_register_writes(57)]


def test_allow_discharge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = DeyeControl(transport, nominal_battery_voltage=48, min_soc=5, max_soc=95)

    control.set_mode_allow_discharge()

    assert transport.writes == [build_allow_discharge_register_writes(5)]


def test_limit_battery_charge_writes_command_builder_output():
    transport = RecordingModbusTransport()
    control = DeyeControl(transport, nominal_battery_voltage=48, min_soc=5, max_soc=95)

    control.set_mode_limit_battery_charge(480)

    assert transport.writes == [
        build_limit_battery_charge_register_writes(480, 48, min_soc=5)
    ]
