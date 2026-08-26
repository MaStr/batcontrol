"""Pure Deye SUN-*K-SG04LP3/SG05LP3 Modbus command building helpers.

Register addresses below follow the community-maintained register map from
Developer089/deye-modbus-ha (custom_components/deye_modbus/maps/sun_3ph_hybrid.yaml),
which states it derives from Deye's own Modbus protocol document for the
SUN-*K-SG04LP3/SG05LP3 family. These addresses have NOT been independently
verified against real hardware or Deye's official PDF in this environment -
verify against your own inverter (e.g. with a read-only Modbus probe) before
relying on this backend for unattended production control.

There are 6 time-of-use (TOU) slots. Rather than assume how slot time
boundaries divide the day, every mode below writes the same SOC-target and
charge-source value to all 6 slots, so the result is correct regardless of
which slot the inverter considers "active" at any given moment.
"""

from .types import RegisterWrite

REG_BATTERY_MAX_CHARGE_CURRENT = 108
REG_GRID_CHARGE_CURRENT = 128
REG_GRID_CHARGE_ENABLE = 130

REG_TOU_SLOT0_TIME = 148
REG_TOU_SOC_TARGETS = list(range(166, 172))
REG_TOU_CHARGE_SOURCES = list(range(172, 178))

TOU_CHARGE_SOURCE_OFF = 0
TOU_CHARGE_SOURCE_GRID = 1

FULL_DAY_SLOT_START = 0  # 00:00 in the inverter's HHMM slot-time encoding

MAX_CHARGE_CURRENT_A = 185  # Upper bound for registers 108/128 per the register map


def watts_to_amps_register_value(
    watts: float,
    nominal_battery_voltage: float,
    max_current_a: int = MAX_CHARGE_CURRENT_A,
) -> int:
    """Convert a watt charge rate to a clamped whole-amp register value.

    Raises:
        ValueError: If ``nominal_battery_voltage`` is zero or negative.
    """
    if nominal_battery_voltage <= 0:
        raise ValueError(
            "nominal_battery_voltage must be greater than 0, "
            f"got {nominal_battery_voltage}"
        )

    amps = max(0.0, watts) / nominal_battery_voltage
    return int(min(amps, max_current_a))


def build_force_charge_register_writes(
    rate_watts: float,
    nominal_battery_voltage: float,
    max_soc: int,
) -> list[RegisterWrite]:
    """Build register writes to charge from grid up to rate_watts."""
    current = watts_to_amps_register_value(rate_watts, nominal_battery_voltage)

    writes = [
        RegisterWrite(REG_BATTERY_MAX_CHARGE_CURRENT, current),
        RegisterWrite(REG_GRID_CHARGE_CURRENT, current),
        RegisterWrite(REG_GRID_CHARGE_ENABLE, 1),
        RegisterWrite(REG_TOU_SLOT0_TIME, FULL_DAY_SLOT_START),
    ]
    writes += [RegisterWrite(reg, max_soc) for reg in REG_TOU_SOC_TARGETS]
    writes += [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_GRID) for reg in REG_TOU_CHARGE_SOURCES
    ]
    return writes


def build_avoid_discharge_register_writes(current_soc: int) -> list[RegisterWrite]:
    """Build register writes to hold the battery at its current SoC.

    Mirrors the mechanism confirmed working in evcc (evcc-io/evcc#12333): a
    time-of-use slot with its SoC target set to the current SoC, with grid
    charging enabled for that slot, stops the battery discharging below that
    level without capping the charge rate.
    """
    writes = [
        RegisterWrite(REG_GRID_CHARGE_ENABLE, 1),
        RegisterWrite(REG_TOU_SLOT0_TIME, FULL_DAY_SLOT_START),
    ]
    writes += [RegisterWrite(reg, current_soc) for reg in REG_TOU_SOC_TARGETS]
    writes += [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_GRID) for reg in REG_TOU_CHARGE_SOURCES
    ]
    return writes


def build_allow_discharge_register_writes(min_soc: int) -> list[RegisterWrite]:
    """Build register writes restoring normal automatic operation."""
    writes = [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_OFF) for reg in REG_TOU_CHARGE_SOURCES
    ]
    writes += [RegisterWrite(reg, min_soc) for reg in REG_TOU_SOC_TARGETS]
    writes.append(RegisterWrite(REG_GRID_CHARGE_ENABLE, 0))
    return writes


def build_limit_battery_charge_register_writes(
    limit_charge_rate_watts: float,
    nominal_battery_voltage: float,
    min_soc: int,
) -> list[RegisterWrite]:
    """Build register writes capping total battery charge current, PV only.

    Caps the overall battery charge current (register 108, which applies
    regardless of source) while disabling grid charging, so at most
    limit_charge_rate_watts of PV power can charge the battery and discharge
    remains unrestricted. A limit of 0 blocks charging entirely.
    """
    current = watts_to_amps_register_value(
        limit_charge_rate_watts,
        nominal_battery_voltage,
    )

    writes = [
        RegisterWrite(REG_BATTERY_MAX_CHARGE_CURRENT, current),
        RegisterWrite(REG_GRID_CHARGE_ENABLE, 0),
    ]
    writes += [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_OFF) for reg in REG_TOU_CHARGE_SOURCES
    ]
    writes += [RegisterWrite(reg, min_soc) for reg in REG_TOU_SOC_TARGETS]
    return writes
