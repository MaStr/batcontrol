import pytest

from batcontrol.inverter.fronius_modbus.grid_status import (
    GRID_FREQUENCY_HZ,
    GRID_FREQUENCY_TOLERANCE_HZ,
    FroniusModbusGridStatus,
    FroniusModbusGridStatusReader,
    infer_grid_status,
)
from batcontrol.inverter.fronius_modbus.types import RegisterRead


class RecordingModbusTransport:
    def __init__(self, values):
        self.values = values
        self.read_requests = []

    def read_registers(self, register: int, count: int) -> RegisterRead:
        self.read_requests.append((register, count))
        return RegisterRead(start_register=register, values=self.values)


def common_model_frequency_registers(raw_frequency: int, scale_factor: int = 65534):
    values = [0] * 16
    values[14] = raw_frequency
    values[15] = scale_factor
    return values


def test_infers_on_grid_operating_from_meter_and_inverter_frequency():
    assert infer_grid_status(
        inverter_frequency_hz=49.91,
        meter_frequency_hz=49.90,
    ) == FroniusModbusGridStatus.ON_GRID_OPERATING


def test_infers_off_grid_operating_when_meter_is_offline_and_inverter_is_running():
    assert infer_grid_status(
        inverter_frequency_hz=53.0,
        meter_frequency_hz=0.0,
    ) == FroniusModbusGridStatus.OFF_GRID_OPERATING


def test_infers_on_grid_when_meter_is_online_and_inverter_is_sleeping():
    assert infer_grid_status(
        inverter_frequency_hz=0.0,
        meter_frequency_hz=49.90,
    ) == FroniusModbusGridStatus.ON_GRID


def test_infers_off_grid_when_meter_and_inverter_are_offline():
    assert infer_grid_status(
        inverter_frequency_hz=0.0,
        meter_frequency_hz=0.0,
    ) == FroniusModbusGridStatus.OFF_GRID


def test_infers_unknown_when_frequency_combination_is_not_understood():
    assert infer_grid_status(
        inverter_frequency_hz=60.0,
        meter_frequency_hz=49.90,
    ) == FroniusModbusGridStatus.UNKNOWN


@pytest.mark.parametrize(
    "boundary_frequency",
    [
        GRID_FREQUENCY_HZ - GRID_FREQUENCY_TOLERANCE_HZ,
        GRID_FREQUENCY_HZ + GRID_FREQUENCY_TOLERANCE_HZ,
    ],
)
def test_infers_on_grid_operating_at_grid_frequency_tolerance_boundaries(
    boundary_frequency,
):
    assert infer_grid_status(
        inverter_frequency_hz=boundary_frequency,
        meter_frequency_hz=boundary_frequency,
    ) == FroniusModbusGridStatus.ON_GRID_OPERATING


def test_grid_status_reader_reads_common_model_frequencies():
    inverter_transport = RecordingModbusTransport(
        common_model_frequency_registers(4991)
    )
    meter_transport = RecordingModbusTransport(
        common_model_frequency_registers(4990)
    )
    reader = FroniusModbusGridStatusReader(
        inverter_transport,
        meter_transport,
    )

    status = reader.read_grid_status()

    assert status.status == FroniusModbusGridStatus.ON_GRID_OPERATING
    assert status.inverter_frequency_hz == pytest.approx(49.91)
    assert status.meter_frequency_hz == pytest.approx(49.90)
    assert inverter_transport.read_requests == [(40071, 16)]
    assert meter_transport.read_requests == [(40071, 16)]
