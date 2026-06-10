"""
Forecast-derived battery metrics for state estimation and load-control decisions.

ForecastMetrics computes indicators from production/consumption forecast arrays
and current battery state. All methods are stateless with respect to object
state; they emit debug log messages but do not mutate any shared state.

Metrics:
  solar_active_and_surplus  -- solar-active flag + expected PV overflow (Wh)
  pv_start_battery          -- battery level (Wh) at next net-charging point
  forecast_min_battery      -- minimum battery level (Wh) over forecast horizon
"""
import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ForecastMetrics:
    """Pure-function metrics derived from forecast arrays and battery state."""

    @staticmethod
    def solar_active_and_surplus(
            production: np.ndarray,
            consumption: np.ndarray,
            free_capacity: float) -> Tuple[bool, float]:
        """Compute solar-active flag and expected surplus energy.

        Returns:
            solar_active (bool): True iff solar is producing in slot 0
            surplus_wh (float): Expected solar overflow in Wh (>0 = WP can run)

        When solar is active, surplus is the overflow in the current production
        window. Otherwise, surplus is the expected overflow at the end of the
        next production window after the battery has bridged consumption until
        solar restarts.
        """
        net_consumption = consumption - production

        production_start: Optional[int] = None
        production_end_current: Optional[int] = None
        for i, p in enumerate(production):
            if p > 0:
                if production_start is None:
                    production_start = i
                production_end_current = i
            elif production_start is not None:
                break

        solar_active = production_start == 0

        if production_start is None:
            surplus_wh = 0.0
        else:
            bridge_wh = max(0.0, float(np.sum(net_consumption[:production_start])))
            end_idx = (production_end_current + 1) if production_end_current is not None \
                else production_start + 1
            solar_net_wh = float(-np.sum(net_consumption[production_start:end_idx]))
            surplus_wh = max(0.0, solar_net_wh - free_capacity - bridge_wh)

        logger.debug(
            'Solar active: %s, surplus: %.1f Wh (free_cap=%.1f Wh)',
            solar_active, surplus_wh, free_capacity
        )
        return solar_active, surplus_wh

    @staticmethod
    def pv_start_battery(
            net_consumption: np.ndarray,
            stored_usable_energy: float,
            free_capacity: float) -> float:
        """Battery level (Wh above MIN_SOC) at the start of the next net-charging window.

        Simulates slot-by-slot discharge until the first slot where
        net_consumption < 0 (solar production exceeds household consumption).
        That crossing point is when the battery transitions from discharging to
        charging and is the most meaningful reference for overnight planning.

        Returns 0.0 if the battery reaches MIN_SOC before that point, or if no
        net-charging slot exists in the forecast at all.
        """
        battery = stored_usable_energy
        max_battery = stored_usable_energy + free_capacity
        for net in net_consumption:
            if net < 0:
                return battery
            battery = max(0.0, min(max_battery, battery - net))
        return 0.0

    @staticmethod
    def forecast_min_battery(
            net_consumption: np.ndarray,
            stored_usable_energy: float,
            free_capacity: float) -> float:
        """Minimum battery level (Wh above MIN_SOC) over the entire forecast horizon.

        Simulates slot-by-slot with proper floor (MIN_SOC = 0 usable) and ceiling
        (MAX_SOC = stored_usable + free_capacity) clamping at each step.
        Returns the lowest point reached during the simulation.

        A value of 0 means the battery is expected to hit MIN_SOC at some point
        in the forecast -- a signal to be conservative with flexible loads.
        """
        battery = stored_usable_energy
        max_battery = stored_usable_energy + free_capacity
        min_battery = stored_usable_energy
        for net in net_consumption:
            battery = max(0.0, min(max_battery, battery - net))
            if battery < min_battery:
                min_battery = battery
        logger.debug(
            'Forecast min battery: %.1f Wh (stored=%.1f Wh, slots=%d)',
            min_battery, stored_usable_energy, len(net_consumption)
        )
        return min_battery
