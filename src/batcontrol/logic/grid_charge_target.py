"""Effective grid-charge SoC target calculation."""

from typing import Optional, Sequence

GRID_CHARGE_TARGET_STRATEGY_FIXED = 'fixed'
GRID_CHARGE_TARGET_STRATEGY_FORECAST = 'forecast'
GRID_CHARGE_TARGET_STRATEGIES = (
    GRID_CHARGE_TARGET_STRATEGY_FIXED,
    GRID_CHARGE_TARGET_STRATEGY_FORECAST,
)


def _ordered_values(values) -> Sequence[float]:
    if isinstance(values, dict):
        if not values:
            return []
        keys = set(values.keys())
        error_message = (
            "forecast dict values must use consecutive integer "
            "indices starting at 0"
        )
        if not all(isinstance(index, int) for index in keys):
            raise ValueError(error_message)
        expected_keys = set(range(max(keys) + 1))
        if keys != expected_keys:
            raise ValueError(error_message)
        return [values[index] for index in range(max(keys) + 1)]
    return list(values)


def _calculate_min_dynamic_price_difference(
        current_price: float,
        min_price_difference: float,
        min_price_difference_rel: float) -> float:
    return max(min_price_difference,
               min_price_difference_rel * abs(current_price))


def calculate_effective_grid_charge_soc(
        strategy: str,
        configured_min_grid_charge_soc: Optional[float],
        max_charging_from_grid_limit: float,
        max_capacity: float,
        min_soc_energy: float,
        production,
        consumption,
        prices,
        min_price_difference: float,
        min_price_difference_rel: float = 0.0,
        pv_forecast_factor: float = 1.0) -> Optional[float]:
    """Calculate the effective minimum grid-charge SoC for this evaluation.

    ``fixed`` returns the configured target unchanged. ``forecast`` treats the
    configured target as a floor and raises it when future expensive-slot net
    demand implies a higher target. Forecast PV can be discounted with
    ``pv_forecast_factor`` to account for uncertain PV ramps.
    """
    if configured_min_grid_charge_soc is None:
        return None
    if strategy not in GRID_CHARGE_TARGET_STRATEGIES:
        raise ValueError(
            f"grid_charge_target_strategy must be one of "
            f"{GRID_CHARGE_TARGET_STRATEGIES}, got '{strategy}'"
        )
    if strategy == GRID_CHARGE_TARGET_STRATEGY_FIXED:
        return configured_min_grid_charge_soc
    if max_capacity <= 0:
        raise ValueError("max_capacity must be greater than 0")
    if not 0 <= pv_forecast_factor <= 1:
        raise ValueError(
            "grid_charge_forecast_pv_factor must be between 0 and 1, "
            f"got {pv_forecast_factor}"
        )

    production_values = _ordered_values(production)
    consumption_values = _ordered_values(consumption)
    price_values = _ordered_values(prices)
    max_slot = min(len(production_values), len(consumption_values), len(price_values))
    if max_slot < 2:
        return configured_min_grid_charge_soc

    current_price = price_values[0]
    min_dynamic_price_difference = _calculate_min_dynamic_price_difference(
        current_price,
        min_price_difference,
        min_price_difference_rel,
    )

    # Evaluate until the next price slot that is no more expensive than the
    # current slot. This keeps the target tied to the current cheap/economical
    # charging window rather than charging for the whole forecast horizon.
    for slot in range(1, max_slot):
        if price_values[slot] <= current_price:
            max_slot = slot
            break

    forecast_need = 0.0
    for slot in range(1, max_slot):
        if price_values[slot] <= current_price + min_dynamic_price_difference:
            continue
        discounted_pv = production_values[slot] * pv_forecast_factor
        forecast_need += max(0.0, consumption_values[slot] - discounted_pv)

    forecast_target = (min_soc_energy + forecast_need) / max_capacity
    forecast_target = min(forecast_target, max_charging_from_grid_limit)
    return max(configured_min_grid_charge_soc, forecast_target)
