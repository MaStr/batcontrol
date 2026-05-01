"""Helpers for logic scenario tests."""
import datetime

import numpy as np

from batcontrol.logic.common import CommonLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
    PeakShavingConfig,
)


CAPACITY_WH = 10240
MIN_SOC = 0.10
MAX_CHARGING_FROM_GRID_LIMIT = 0.89
MIN_GRID_CHARGE_SOC = 0.55
CHEAP_PRICE = 0.4635
EXPENSIVE_PRICE = 0.7018


def make_logic(logic_cls, *,
               timezone=datetime.timezone.utc,
               capacity_wh=CAPACITY_WH,
               max_charging_from_grid_limit=MAX_CHARGING_FROM_GRID_LIMIT,
               min_grid_charge_soc=MIN_GRID_CHARGE_SOC,
               preserve_min_grid_charge_soc=True,
               min_price_difference=0.05,
               min_price_difference_rel=0.10,
               charge_rate_multiplier=1.1,
               always_allow_discharge_limit=0.90,
               min_charge_energy=100,
               peak_shaving_enabled=False):
    """Create a logic instance with common scenario defaults.

    The CommonLogic singleton is reset so each helper call applies the
    requested singleton-backed tuning values independently.
    """
    CommonLogic._instance = None
    CommonLogic.get_instance(
        charge_rate_multiplier=charge_rate_multiplier,
        always_allow_discharge_limit=always_allow_discharge_limit,
        max_capacity=capacity_wh,
        min_charge_energy=min_charge_energy,
    )
    logic = logic_cls(timezone=timezone, interval_minutes=60)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=max_charging_from_grid_limit,
        min_price_difference=min_price_difference,
        min_price_difference_rel=min_price_difference_rel,
        max_capacity=capacity_wh,
        min_grid_charge_soc=min_grid_charge_soc,
        preserve_min_grid_charge_soc=preserve_min_grid_charge_soc,
        peak_shaving=PeakShavingConfig(enabled=peak_shaving_enabled),
    ))
    return logic


def make_calc_input(production, consumption, prices, soc, *,
                    capacity_wh=CAPACITY_WH,
                    min_soc=MIN_SOC):
    """Build CalculationInput from forecast arrays and state of charge.

    Args:
        production: Forecast production values in Wh for each time slot.
        consumption: Forecast consumption values in Wh for each time slot.
        prices: Energy prices for each time slot.
        soc: Current battery state of charge as a percentage, 0-100.
        capacity_wh: Battery capacity in Wh.
        min_soc: Minimum battery state of charge as a ratio, 0-1.
    """
    stored_energy = capacity_wh * soc / 100
    min_soc_energy = capacity_wh * min_soc
    return CalculationInput(
        production=np.array(production, dtype=float),
        consumption=np.array(consumption, dtype=float),
        prices={slot: price for slot, price in enumerate(prices)},
        stored_energy=stored_energy,
        stored_usable_energy=max(0.0, stored_energy - min_soc_energy),
        free_capacity=capacity_wh - stored_energy,
    )


def target_usable_energy(*,
                         capacity_wh=CAPACITY_WH,
                         min_soc=MIN_SOC,
                         min_grid_charge_soc=MIN_GRID_CHARGE_SOC):
    """Return usable energy reserved by the minimum grid-charge target."""
    return capacity_wh * (min_grid_charge_soc - min_soc)
