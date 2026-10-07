"""Decision records shared by DefaultLogic and NextLogic.

Both logic classes run the same discharge and grid recharge rules; building
the records in one place keeps the input keys and reason codes identical.
"""
from typing import List, Optional

from .decision_trace import Decision, DecisionRecord, Outcome, Reason
from .logic_interface import CalculationInput, CalculationOutput


def discharge_always_allowed(calc_input: CalculationInput,
                             always_allow_discharge_limit: float
                             ) -> DecisionRecord:
    """Discharge is allowed because the battery is above the always allow
    discharge limit."""
    return DecisionRecord(
        decision=Decision.DISCHARGE,
        outcome=Outcome.ALLOWED,
        reason=Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT,
        inputs={
            'stored_energy': calc_input.stored_energy,
            'always_allow_discharge_limit': always_allow_discharge_limit,
        },
        decisive=True,
    )


def discharge_evaluated(allowed: bool, calc_input: CalculationInput,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                        calc_output: CalculationOutput, *,
                        evaluation_slots: int,
                        higher_price_slots: List[int],
                        cheaper_price_slot: Optional[int],
                        cheaper_price: Optional[float],
                        interval_minutes: int) -> DecisionRecord:
    """Result of the reserved energy rule: usable energy against the energy
    reserved for high price slots.

    Allowed is decisive, nothing more is evaluated then. Forbidden is not:
    the grid recharge step decides what happens next.

    ``higher_price_slots`` and ``cheaper_price_slot`` are relative slot
    indices (0 = the current interval), not clock times -- ``interval_minutes``
    is included so a consumer can turn a slot index into an actual time span
    (slot index * interval_minutes).
    """
    return DecisionRecord(
        decision=Decision.DISCHARGE,
        outcome=Outcome.ALLOWED if allowed else Outcome.FORBIDDEN,
        reason=(Reason.USABLE_ENERGY_EXCEEDS_RESERVE if allowed
                else Reason.RESERVE_REQUIRED),
        inputs={
            'current_price': calc_input.prices[0],
            'min_dynamic_price_difference':
                calc_output.min_dynamic_price_difference,
            'stored_usable_energy': calc_input.stored_usable_energy,
            'reserved_energy': calc_output.reserved_energy,
            'evaluation_slots': evaluation_slots,
            'higher_price_slots': higher_price_slots,
            'cheaper_price_slot': cheaper_price_slot,
            'cheaper_price': cheaper_price,
            'interval_minutes': interval_minutes,
        },
        decisive=allowed,
    )


def grid_recharge_charge(calc_input: CalculationInput,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                         calc_output: CalculationOutput, *,
                         recharge_energy: float,
                         allowed_charging_energy: float,
                         remaining_time: float,
                         charge_rate: int,
                         high_price_slots: List[int],
                         high_price_energy_demand: float,
                         recharge_window_end: int,
                         interval_minutes: int) -> DecisionRecord:
    """Battery is charged from the grid.

    ``high_price_slots``/``recharge_window_end`` are relative slot indices
    (returned by ``_get_required_recharge_energy`` alongside the energy
    itself) -- ``interval_minutes`` is included so a consumer can turn one
    into an actual time span, same as for the discharge rule.
    """
    return DecisionRecord(
        decision=Decision.GRID_RECHARGE,
        outcome=Outcome.CHARGE,
        reason=Reason.GRID_RECHARGE_REQUIRED,
        inputs={
            'current_price': calc_input.prices[0],
            'min_dynamic_price_difference':
                calc_output.min_dynamic_price_difference,
            'stored_energy': calc_input.stored_energy,
            'stored_usable_energy': calc_input.stored_usable_energy,
            'reserved_energy': calc_output.reserved_energy,
            'requested_recharge_energy': calc_output.required_recharge_energy,
            'recharge_energy': recharge_energy,
            'available_grid_charge_capacity': allowed_charging_energy,
            'remaining_time': remaining_time,
            'charge_rate': charge_rate,
            'high_price_slots': high_price_slots,
            'high_price_energy_demand': high_price_energy_demand,
            'recharge_window_end': recharge_window_end,
            'interval_minutes': interval_minutes,
        },
        decisive=True,
    )


def grid_recharge_idle(calc_input: CalculationInput, *,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                       is_charging_possible: bool,
                       charge_limit_capacity: float,
                       required_recharge_energy: float,
                       high_price_slots: List[int],
                       high_price_energy_demand: float,
                       recharge_energy_before_minimum: float,
                       interval_minutes: int) -> DecisionRecord:
    """Battery is kept as it is: no grid charging.

    Five distinct situations end up here, told apart purely from the
    numbers already computed by _get_required_recharge_energy -- no
    extra flag is needed:
    - GRID_CHARGE_LIMIT_REACHED: SoC is already above the grid-charging
      limit; _get_required_recharge_energy was never called, so
      high_price_slots etc. are at their defaults (empty/zero).
    - NO_HIGH_PRICE_SLOTS: no slot in the evaluation window is priced
      high enough to justify reserving/recharging for it at all.
    - HIGH_PRICE_DEMAND_COVERED_BY_PRODUCTION: there are high-price slots,
      but forecast PV production is expected to cover their demand.
    - NO_RECHARGE_REQUIRED: demand remains after production, but it is
      already covered by stored usable battery energy.
    - RECHARGE_BELOW_MINIMUM: a positive amount was needed after all of
      the above, but it is below the minimum charge amount, so no grid
      charging happens for it.
    high_price_slots is a relative slot index list, same as on
    discharge_evaluated/grid_recharge_charge -- interval_minutes is
    included for the same reason.
    """
    if not is_charging_possible:
        reason = Reason.GRID_CHARGE_LIMIT_REACHED
    elif high_price_energy_demand == 0:
        reason = (Reason.NO_HIGH_PRICE_SLOTS if not high_price_slots
                  else Reason.HIGH_PRICE_DEMAND_COVERED_BY_PRODUCTION)
    elif recharge_energy_before_minimum > 0:
        reason = Reason.RECHARGE_BELOW_MINIMUM
    else:
        reason = Reason.NO_RECHARGE_REQUIRED
    return DecisionRecord(
        decision=Decision.GRID_RECHARGE,
        outcome=Outcome.NO_CHARGE,
        reason=reason,
        inputs={
            'current_price': calc_input.prices[0],
            'stored_energy': calc_input.stored_energy,
            'charge_limit_capacity': charge_limit_capacity,
            'required_recharge_energy': required_recharge_energy,
            'high_price_slots': high_price_slots,
            'high_price_energy_demand': high_price_energy_demand,
            'recharge_energy_before_minimum': recharge_energy_before_minimum,
            'interval_minutes': interval_minutes,
        },
        decisive=True,
    )
