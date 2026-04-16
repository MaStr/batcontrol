"""Pure Fronius GEN24 Modbus storage-register decoding helpers."""

from dataclasses import dataclass


REG_WCHAMAX = 40345
REG_STORCTL_MOD = 40348
REG_MIN_RSV_PCT = 40350
REG_CHASTATE = 40351
REG_CHAST = 40354
REG_OUTWRTE = 40355
REG_INWRTE = 40356
REG_RVRT_TMS = 40358
REG_CHAGRISET = 40360
REG_CHASTATE_SF = 40365
REG_INOUTWRTE_SF = 40368

CHAGRISET_ENABLED = 1


@dataclass(frozen=True)
class FroniusStorageStatus:
    max_charge_rate_w: int
    storage_control_mode: int
    minimum_reserve_pct: float
    soc_pct: float
    charge_status: int
    discharge_rate_pct: float
    charge_rate_pct: float
    revert_seconds: int
    grid_charging_enabled: bool
    soc_scale_factor: int
    rate_scale_factor: int


def unsigned_to_signed_16(value: int) -> int:
    """Convert an unsigned 16-bit Modbus value to signed."""
    if value >= 32768:
        return value - 65536
    return value


def decode_scaled_percent(raw_value: int, scale_factor: int) -> float:
    """Decode a SunSpec-style scaled percent value."""
    return raw_value * (10 ** scale_factor)


def decode_storage_status(registers: dict[int, int]) -> FroniusStorageStatus:
    """Decode the known Fronius storage control/status registers.

    The register set and scale handling here match the behavior observed in:
    - local live read-only probing
    - `fronius-modbus-control`
    - `redpomodoro/fronius_modbus`

    Note: `ChaGriSet` semantics are somewhat inconsistently documented across
    ecosystem code. This module currently follows the live probe and
    `fronius-modbus-control` interpretation that `1` means grid charging is
    enabled.
    """
    required_registers = [
        REG_WCHAMAX,
        REG_STORCTL_MOD,
        REG_MIN_RSV_PCT,
        REG_CHASTATE,
        REG_CHAST,
        REG_OUTWRTE,
        REG_INWRTE,
        REG_RVRT_TMS,
        REG_CHAGRISET,
        REG_CHASTATE_SF,
        REG_INOUTWRTE_SF,
    ]

    for register in required_registers:
        if register not in registers:
            raise KeyError(f"Missing required register {register}")

    soc_scale_factor = unsigned_to_signed_16(registers[REG_CHASTATE_SF])
    rate_scale_factor = unsigned_to_signed_16(registers[REG_INOUTWRTE_SF])

    return FroniusStorageStatus(
        max_charge_rate_w=registers[REG_WCHAMAX],
        storage_control_mode=registers[REG_STORCTL_MOD],
        minimum_reserve_pct=decode_scaled_percent(
            registers[REG_MIN_RSV_PCT],
            soc_scale_factor,
        ),
        soc_pct=decode_scaled_percent(
            registers[REG_CHASTATE],
            soc_scale_factor,
        ),
        charge_status=registers[REG_CHAST],
        discharge_rate_pct=decode_scaled_percent(
            unsigned_to_signed_16(registers[REG_OUTWRTE]),
            rate_scale_factor,
        ),
        charge_rate_pct=decode_scaled_percent(
            unsigned_to_signed_16(registers[REG_INWRTE]),
            rate_scale_factor,
        ),
        revert_seconds=registers[REG_RVRT_TMS],
        grid_charging_enabled=registers[REG_CHAGRISET] == CHAGRISET_ENABLED,
        soc_scale_factor=soc_scale_factor,
        rate_scale_factor=rate_scale_factor,
    )
