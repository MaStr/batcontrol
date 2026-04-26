"""Read and infer Fronius grid connection status via SunSpec Modbus."""

from dataclasses import dataclass
from enum import Enum

from .reads import unsigned_to_signed_16
from .types import FroniusModbusTransport

COMMON_MODEL_START = 40071
COMMON_MODEL_FREQUENCY_COUNT = 16
FREQUENCY_REGISTER_OFFSET = 14
FREQUENCY_SCALE_FACTOR_OFFSET = 15

GRID_FREQUENCY_HZ = 50.0
GRID_FREQUENCY_TOLERANCE_HZ = 0.2
INVERTER_OPERATING_FREQUENCY_TOLERANCE_HZ = 5.0


class FroniusModbusGridStatus(Enum):
    """Condensed Fronius grid status inferred from meter/inverter frequency."""

    OFF_GRID = "off_grid"
    OFF_GRID_OPERATING = "off_grid_operating"
    ON_GRID = "on_grid"
    ON_GRID_OPERATING = "on_grid_operating"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FroniusModbusGridStatusRead:
    """Grid-status read result with the frequencies used for inference."""

    status: FroniusModbusGridStatus
    inverter_frequency_hz: float
    meter_frequency_hz: float


def _scaled_frequency(raw_value: int, raw_scale_factor: int) -> float:
    value = unsigned_to_signed_16(raw_value)
    scale_factor = unsigned_to_signed_16(raw_scale_factor)
    return value * (10 ** scale_factor)


def _is_near_grid_frequency(frequency_hz: float) -> bool:
    lower_bound = GRID_FREQUENCY_HZ - GRID_FREQUENCY_TOLERANCE_HZ
    upper_bound = GRID_FREQUENCY_HZ + GRID_FREQUENCY_TOLERANCE_HZ
    return lower_bound <= frequency_hz <= upper_bound


def _is_inverter_operating_frequency(frequency_hz: float) -> bool:
    lower_bound = GRID_FREQUENCY_HZ - INVERTER_OPERATING_FREQUENCY_TOLERANCE_HZ
    upper_bound = GRID_FREQUENCY_HZ + INVERTER_OPERATING_FREQUENCY_TOLERANCE_HZ
    return lower_bound <= frequency_hz <= upper_bound


def infer_grid_status(
    inverter_frequency_hz: float,
    meter_frequency_hz: float,
) -> FroniusModbusGridStatus:
    """Infer grid status from inverter and meter line frequency.

    This mirrors the approach used by the external Home Assistant
    ``fronius_modbus`` integration: the meter frequency indicates whether the
    public grid is present; inverter frequency indicates whether the inverter is
    operating while isolated from the grid.
    """
    meter_online = _is_near_grid_frequency(meter_frequency_hz)
    inverter_on_grid = _is_near_grid_frequency(inverter_frequency_hz)

    if meter_online and inverter_on_grid:
        return FroniusModbusGridStatus.ON_GRID_OPERATING
    if not meter_online and _is_inverter_operating_frequency(inverter_frequency_hz):
        return FroniusModbusGridStatus.OFF_GRID_OPERATING
    if inverter_frequency_hz < 1:
        if meter_online:
            return FroniusModbusGridStatus.ON_GRID
        if meter_frequency_hz < 1:
            return FroniusModbusGridStatus.OFF_GRID
    return FroniusModbusGridStatus.UNKNOWN


class FroniusModbusGridStatusReader:
    """Read inverter/meter frequency and infer Fronius grid status."""

    def __init__(
        self,
        inverter_transport: FroniusModbusTransport,
        meter_transport: FroniusModbusTransport,
    ):
        self.inverter_transport = inverter_transport
        self.meter_transport = meter_transport

    def read_grid_status(self) -> FroniusModbusGridStatusRead:
        inverter_frequency = self._read_frequency(self.inverter_transport)
        meter_frequency = self._read_frequency(self.meter_transport)
        return FroniusModbusGridStatusRead(
            status=infer_grid_status(inverter_frequency, meter_frequency),
            inverter_frequency_hz=inverter_frequency,
            meter_frequency_hz=meter_frequency,
        )

    def _read_frequency(self, transport: FroniusModbusTransport) -> float:
        register_read = transport.read_registers(
            COMMON_MODEL_START,
            COMMON_MODEL_FREQUENCY_COUNT,
        )
        values = register_read.values
        return _scaled_frequency(
            values[FREQUENCY_REGISTER_OFFSET],
            values[FREQUENCY_SCALE_FACTOR_OFFSET],
        )
