import pytest

from batcontrol.inverter.deye.reads import (
    REG_BATTERY_CURRENT,
    REG_BATTERY_POWER,
    REG_BATTERY_SOC,
    REG_BATTERY_TEMPERATURE,
    REG_BATTERY_VOLTAGE,
    DeyeStorageStatus,
    decode_storage_status,
    unsigned_to_signed_16,
)


def test_unsigned_to_signed_16_passes_through_positive_values():
    assert unsigned_to_signed_16(1234) == 1234


def test_unsigned_to_signed_16_converts_negative_values():
    assert unsigned_to_signed_16(65526) == -10


def test_decode_storage_status_applies_scale_factors():
    registers = {
        REG_BATTERY_TEMPERATURE: 1250,
        REG_BATTERY_VOLTAGE: 5320,
        REG_BATTERY_SOC: 57,
        REG_BATTERY_POWER: 65036,  # -500 as unsigned 16-bit
        REG_BATTERY_CURRENT: 65486,  # -50 as unsigned 16-bit -> -0.50 A
    }

    status = decode_storage_status(registers)

    assert status == DeyeStorageStatus(
        battery_temperature_c=25.0,
        battery_voltage_v=53.2,
        soc_pct=57,
        battery_power_w=-500,
        battery_current_a=-0.5,
    )


def test_decode_storage_status_raises_on_missing_register():
    registers = {
        REG_BATTERY_TEMPERATURE: 1250,
        REG_BATTERY_VOLTAGE: 5320,
        REG_BATTERY_SOC: 57,
        REG_BATTERY_POWER: 0,
    }

    with pytest.raises(KeyError):
        decode_storage_status(registers)
