"""Decision records shared by DefaultLogic and NextLogic.

Both logic classes run the same discharge and grid recharge rules; building
the records in one place keeps the input keys and reason codes identical.
"""
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
                        evaluation_slots: int, higher_price_slots: int,
                        cheaper_price_slot) -> DecisionRecord:
    """Result of the reserved energy rule: usable energy against the energy
    reserved for high price slots.

    Allowed is decisive, nothing more is evaluated then. Forbidden is not:
    the grid recharge step decides what happens next.
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
        },
        decisive=allowed,
    )


def grid_recharge_charge(calc_input: CalculationInput,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                         calc_output: CalculationOutput, *,
                         recharge_energy: float,
                         allowed_charging_energy: float,
                         remaining_time: float,
                         charge_rate: int) -> DecisionRecord:
    """Battery is charged from the grid."""
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
        },
        decisive=True,
    )


def grid_recharge_idle(calc_input: CalculationInput, *,
                       is_charging_possible: bool,
                       charge_limit_capacity: float,
                       required_recharge_energy: float) -> DecisionRecord:
    """Battery is kept as it is: no grid charging."""
    return DecisionRecord(
        decision=Decision.GRID_RECHARGE,
        outcome=Outcome.NO_CHARGE,
        reason=(Reason.NO_RECHARGE_REQUIRED if is_charging_possible
                else Reason.GRID_CHARGE_LIMIT_REACHED),
        inputs={
            'current_price': calc_input.prices[0],
            'stored_energy': calc_input.stored_energy,
            'charge_limit_capacity': charge_limit_capacity,
            'required_recharge_energy': required_recharge_energy,
        },
        decisive=True,
    )
