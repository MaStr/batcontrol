"""Pure Fronius GEN24 Modbus command building helpers."""

from .fronius_modbus_types import RegisterWrite

REG_STORCTL_MOD = 40348
REG_OUTWRTE = 40355
REG_INWRTE = 40356
REG_RVRT_TMS = 40358
REG_CHAGRISET = 40360

STORCTL_CHARGE_LIMIT = 1
STORCTL_DISCHARGE_LIMIT = 2
DEFAULT_RATE_SCALE_FACTOR = -2
FULL_RATE_PERCENT = 10000


def signed_to_unsigned_16(value: int) -> int:
    """Convert any integer to unsigned 16-bit (two's complement)."""
    return value & 0xFFFF


def watts_to_pct_register_value(
    watts: float,
    max_charge_rate: float,
    scale_factor: int = DEFAULT_RATE_SCALE_FACTOR,
) -> int:
    """Convert watts to a scaled percentage register value.

    Raises:
        ValueError: If ``max_charge_rate`` is zero or negative.
    """
    if max_charge_rate <= 0:
        raise ValueError(
            f"max_charge_rate must be greater than 0, got {max_charge_rate}"
        )

    pct = max(0.0, min(100.0, (watts / max_charge_rate) * 100.0))
    return int(pct * (10 ** (-scale_factor)))



def validate_revert_seconds(revert_seconds: int) -> int:
    """Validate that revert_seconds fits into an unsigned 16-bit register."""
    if not 0 <= revert_seconds <= 65535:
        raise ValueError("revert_seconds must be between 0 and 65535")
    return revert_seconds



def build_force_charge_register_writes(
    rate_watts: float,
    max_charge_rate: float,
    revert_seconds: int = 0,
) -> list[RegisterWrite]:
    """Build register writes for force-charge mode."""
    rate_value = watts_to_pct_register_value(rate_watts, max_charge_rate)
    revert_seconds = validate_revert_seconds(revert_seconds)

    return [
        RegisterWrite(REG_CHAGRISET, 1),
        RegisterWrite(REG_RVRT_TMS, revert_seconds),
        RegisterWrite(REG_OUTWRTE, signed_to_unsigned_16(-rate_value)),
        RegisterWrite(REG_INWRTE, FULL_RATE_PERCENT),
        RegisterWrite(REG_STORCTL_MOD, STORCTL_DISCHARGE_LIMIT),
    ]



def build_avoid_discharge_register_writes(
    revert_seconds: int = 0,
) -> list[RegisterWrite]:
    """Build register writes for hold/avoid-discharge mode."""
    revert_seconds = validate_revert_seconds(revert_seconds)

    return [
        RegisterWrite(REG_RVRT_TMS, revert_seconds),
        RegisterWrite(REG_OUTWRTE, 0),
        RegisterWrite(REG_INWRTE, FULL_RATE_PERCENT),
        RegisterWrite(REG_STORCTL_MOD, STORCTL_DISCHARGE_LIMIT),
    ]



def build_allow_discharge_register_writes() -> list[RegisterWrite]:
    """Build register writes for returning to automatic mode."""
    return [
        RegisterWrite(REG_STORCTL_MOD, 0),
        RegisterWrite(REG_OUTWRTE, FULL_RATE_PERCENT),
        RegisterWrite(REG_INWRTE, FULL_RATE_PERCENT),
        RegisterWrite(REG_RVRT_TMS, 0),
    ]



def build_limit_battery_charge_register_writes(
    limit_charge_rate_watts: float,
    max_charge_rate: float,
    revert_seconds: int = 0,
) -> list[RegisterWrite]:
    """Build register writes for limiting battery charge while allowing discharge."""
    rate_value = watts_to_pct_register_value(
        limit_charge_rate_watts,
        max_charge_rate,
    )
    revert_seconds = validate_revert_seconds(revert_seconds)

    return [
        RegisterWrite(REG_RVRT_TMS, revert_seconds),
        RegisterWrite(REG_OUTWRTE, FULL_RATE_PERCENT),
        RegisterWrite(REG_INWRTE, rate_value),
        RegisterWrite(REG_STORCTL_MOD, STORCTL_CHARGE_LIMIT),
    ]
