import pytest

from batcontrol.inverter.fronius_modbus.commands import (
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
)
from batcontrol.inverter.fronius_modbus.control import FroniusModbusControl
from batcontrol.inverter.fronius_modbus.grid_status import FroniusModbusGridStatus
from batcontrol.inverter.fronius_modbus.types import RegisterWrite


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


class StubGridStatusReader:
    def __init__(self, status=None, error=None):
        self.status = status
        self.error = error
        self.read_count = 0

    def read_grid_status(self):
        self.read_count += 1
        if self.error is not None:
            raise self.error
        return self.status


@pytest.mark.parametrize(
    "grid_status",
    [
        FroniusModbusGridStatus.ON_GRID,
        FroniusModbusGridStatus.ON_GRID_OPERATING,
    ],
)
def test_restrictive_modes_write_normally_when_grid_status_is_on_grid(grid_status):
    transport = RecordingModbusTransport()
    grid_status_reader = StubGridStatusReader(grid_status)
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
        grid_status_reader=grid_status_reader,
    )

    control.set_mode_avoid_discharge()

    assert transport.writes == [build_avoid_discharge_register_writes(revert_seconds=900)]
    assert grid_status_reader.read_count == 1


def test_restrictive_modes_fail_open_to_allow_discharge_when_off_grid():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
        grid_status_reader=StubGridStatusReader(FroniusModbusGridStatus.OFF_GRID_OPERATING),
    )

    control.set_mode_avoid_discharge()

    assert transport.writes == [build_allow_discharge_register_writes()]


def test_force_charge_fails_open_to_allow_discharge_when_grid_status_is_unknown():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
        grid_status_reader=StubGridStatusReader(FroniusModbusGridStatus.UNKNOWN),
    )

    control.set_mode_force_charge(3000)

    assert transport.writes == [build_allow_discharge_register_writes()]


def test_limit_battery_charge_fails_open_when_grid_status_reader_fails():
    transport = RecordingModbusTransport()
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
        grid_status_reader=StubGridStatusReader(error=RuntimeError("read failed")),
    )

    control.set_mode_limit_battery_charge(2000)

    assert transport.writes == [build_allow_discharge_register_writes()]


def test_allow_discharge_does_not_require_grid_status():
    transport = RecordingModbusTransport()
    grid_status_reader = StubGridStatusReader(error=RuntimeError("read failed"))
    control = FroniusModbusControl(
        transport,
        max_charge_rate=5000,
        grid_status_reader=grid_status_reader,
    )

    control.set_mode_allow_discharge()

    assert transport.writes == [build_allow_discharge_register_writes()]
    assert grid_status_reader.read_count == 0
