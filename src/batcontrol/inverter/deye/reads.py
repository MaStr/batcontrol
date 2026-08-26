"""Pure Deye SUN-*K-SG04LP3/SG05LP3 Modbus telemetry decoding helpers.

Register addresses and scale factors follow the community-maintained register
map from Developer089/deye-modbus-ha (see commands.py docstring) - not yet
independently verified against real hardware.
"""

from dataclasses import dataclass

REG_BATTERY_TEMPERATURE = 586
REG_BATTERY_VOLTAGE = 587
REG_BATTERY_SOC = 588
REG_BATTERY_POWER = 590
REG_BATTERY_CURRENT = 591

TELEMETRY_START_REGISTER = REG_BATTERY_TEMPERATURE
TELEMETRY_REGISTER_COUNT = REG_BATTERY_CURRENT - REG_BATTERY_TEMPERATURE + 1

TEMPERATURE_OFFSET = 1000
TEMPERATURE_SCALE = 0.1
VOLTAGE_SCALE = 0.01
CURRENT_SCALE = 0.01


@dataclass(frozen=True)
class DeyeStorageStatus:
    battery_temperature_c: float
    battery_voltage_v: float
    soc_pct: float
    battery_power_w: int
    battery_current_a: float


def unsigned_to_signed_16(value: int) -> int:
    """Convert an unsigned 16-bit Modbus value to signed."""
    if value >= 32768:
        return value - 65536
    return value


def decode_storage_status(registers: dict[int, int]) -> DeyeStorageStatus:
    """Decode the known Deye battery telemetry registers."""
    required_registers = [
        REG_BATTERY_TEMPERATURE,
        REG_BATTERY_VOLTAGE,
        REG_BATTERY_SOC,
        REG_BATTERY_POWER,
        REG_BATTERY_CURRENT,
    ]

    for register in required_registers:
        if register not in registers:
            raise KeyError(f"Missing required register {register}")

    return DeyeStorageStatus(
        battery_temperature_c=(
            (registers[REG_BATTERY_TEMPERATURE] - TEMPERATURE_OFFSET)
            * TEMPERATURE_SCALE
        ),
        battery_voltage_v=registers[REG_BATTERY_VOLTAGE] * VOLTAGE_SCALE,
        soc_pct=registers[REG_BATTERY_SOC],
        battery_power_w=unsigned_to_signed_16(registers[REG_BATTERY_POWER]),
        battery_current_a=(
            unsigned_to_signed_16(registers[REG_BATTERY_CURRENT]) * CURRENT_SCALE
        ),
    )
