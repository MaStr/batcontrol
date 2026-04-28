"""Shared logging helpers for battery control decisions."""
import logging
from dataclasses import dataclass

from .logic_interface import CalculationInput, CalculationOutput


@dataclass(frozen=True)
class GridRechargeDecision:
    """Inputs and result for a grid recharge decision log entry."""
    recharge_energy: float
    allowed_charging_energy: float
    remaining_time: float
    charge_rate: int


def log_grid_recharge_decision(logger: logging.Logger,
                               calc_output: CalculationOutput,
                               calc_input: CalculationInput,
                               prices: dict,
                               decision: GridRechargeDecision):
    """Log a compact summary of a grid recharge decision."""
    logger.info(
        '[Rule] Grid recharge decision: current_price=%.3f, '
        'min_dynamic_price_difference=%.3f, stored_energy=%0.1f Wh, '
        'stored_usable_energy=%0.1f Wh, reserved_energy=%0.1f Wh, '
        'requested_recharge_energy=%0.1f Wh, recharge_energy=%0.1f Wh, '
        'available_grid_charge_capacity=%0.1f Wh, remaining_time=%0.2f h, '
        'charge_rate=%d W',
        prices[0],
        calc_output.min_dynamic_price_difference,
        calc_input.stored_energy,
        calc_input.stored_usable_energy,
        calc_output.reserved_energy,
        calc_output.required_recharge_energy,
        decision.recharge_energy,
        decision.allowed_charging_energy,
        decision.remaining_time,
        decision.charge_rate
    )
