"""Grid-charge target strategy helpers."""

from dataclasses import dataclass
from typing import Optional

GRID_CHARGE_TARGET_STRATEGY_FIXED = 'fixed'
GRID_CHARGE_TARGET_STRATEGY_FORECAST = 'forecast'
GRID_CHARGE_TARGET_STRATEGIES = (
    GRID_CHARGE_TARGET_STRATEGY_FIXED,
    GRID_CHARGE_TARGET_STRATEGY_FORECAST,
)


@dataclass(frozen=True)
class GridChargeTargetConfig:
    """Configuration for grid-charge target calculation."""

    strategy: str = GRID_CHARGE_TARGET_STRATEGY_FIXED

    @classmethod
    def from_battery_control_config(cls, config: dict) -> 'GridChargeTargetConfig':
        """Create target strategy configuration from battery_control config."""
        return cls(
            strategy=_parse_grid_charge_target_strategy(
                config.get(
                    'grid_charge_target_strategy',
                    GRID_CHARGE_TARGET_STRATEGY_FIXED,
                )
            ),
        )


@dataclass(frozen=True)
class GridChargeTargetResult:
    """Energy adjusted by a grid-charge target and the effective target SoC."""

    energy: float
    effective_soc: Optional[float]


def _parse_grid_charge_target_strategy(value) -> str:
    """Parse the grid-charge target strategy config value."""
    strategy = str(value).strip().lower()
    if strategy not in GRID_CHARGE_TARGET_STRATEGIES:
        raise ValueError(
            f"battery_control.grid_charge_target_strategy must be one of "
            f"{GRID_CHARGE_TARGET_STRATEGIES}, got {value!r}"
        )
    return strategy


def _validate_strategy(strategy: str) -> None:
    if strategy not in GRID_CHARGE_TARGET_STRATEGIES:
        raise ValueError(
            f"grid_charge_target_strategy must be one of "
            f"{GRID_CHARGE_TARGET_STRATEGIES}, got '{strategy}'"
        )


def _capped_forecast_soc(
        configured_min_grid_charge_soc: float,
        additional_reserve_energy: float,
        max_capacity: float,
        max_charging_from_grid_limit: float) -> float:
    """Return floor-plus-reserve target SoC capped by the grid-charge limit."""
    if max_capacity <= 0:
        raise ValueError("max_capacity must be greater than 0")
    target_soc = configured_min_grid_charge_soc + (
        additional_reserve_energy / max_capacity)
    return min(target_soc, max_charging_from_grid_limit)


def apply_grid_charge_target_to_recharge(
        config: GridChargeTargetConfig,
        recharge_energy: float,
        required_energy: float,
        stored_energy: float,
        configured_min_grid_charge_soc: Optional[float],
        max_capacity: float,
        max_charging_from_grid_limit: float) -> GridChargeTargetResult:
    """Apply the configured grid-charge target to recharge energy.

    ``fixed`` applies the configured SoC floor: when grid charging is already
    required, charge at least up to ``min_grid_charge_soc``.

    ``forecast`` uses the existing high-price-slot ``required_energy`` as the
    source of truth and treats ``min_grid_charge_soc`` as the reserve that
    should remain after those slots. The target is therefore the configured
    floor plus the required high-price energy.
    """
    if configured_min_grid_charge_soc is None:
        return GridChargeTargetResult(recharge_energy, None)

    _validate_strategy(config.strategy)
    effective_soc = configured_min_grid_charge_soc
    if required_energy <= 0.0:
        return GridChargeTargetResult(recharge_energy, effective_soc)

    if config.strategy == GRID_CHARGE_TARGET_STRATEGY_FORECAST:
        effective_soc = _capped_forecast_soc(
            configured_min_grid_charge_soc,
            required_energy,
            max_capacity,
            max_charging_from_grid_limit,
        )

    target_energy = max_capacity * effective_soc
    soc_recharge_energy = max(0.0, target_energy - stored_energy)
    adjusted_recharge_energy = max(recharge_energy, soc_recharge_energy)

    if config.strategy == GRID_CHARGE_TARGET_STRATEGY_FORECAST:
        max_grid_charge_energy = max(
            0.0,
            max_capacity * max_charging_from_grid_limit - stored_energy,
        )
        adjusted_recharge_energy = min(
            adjusted_recharge_energy,
            max_grid_charge_energy,
        )

    return GridChargeTargetResult(adjusted_recharge_energy, effective_soc)


def apply_grid_charge_target_to_reserve(
        config: GridChargeTargetConfig,
        reserved_energy: float,
        min_soc_energy: float,
        configured_min_grid_charge_soc: Optional[float],
        max_capacity: float,
        max_charging_from_grid_limit: float,
        active: bool) -> GridChargeTargetResult:
    """Apply the configured grid-charge target to protected reserve energy."""
    if configured_min_grid_charge_soc is None or not active:
        return GridChargeTargetResult(reserved_energy, configured_min_grid_charge_soc)

    _validate_strategy(config.strategy)
    effective_soc = configured_min_grid_charge_soc

    if config.strategy == GRID_CHARGE_TARGET_STRATEGY_FORECAST:
        effective_soc = _capped_forecast_soc(
            configured_min_grid_charge_soc,
            reserved_energy,
            max_capacity,
            max_charging_from_grid_limit,
        )

    target_energy = max_capacity * effective_soc
    target_usable_energy = max(0.0, target_energy - min_soc_energy)
    adjusted_reserved_energy = max(reserved_energy, target_usable_energy)
    return GridChargeTargetResult(adjusted_reserved_energy, effective_soc)
