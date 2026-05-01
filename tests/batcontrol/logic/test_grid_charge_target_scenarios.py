"""Scenario tests for externally supplied grid-charge targets."""
import datetime

import pytest

from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.next import NextLogic

from .helpers import CHEAP_PRICE, EXPENSIVE_PRICE, make_calc_input, make_logic


def _calculate(logic_cls, min_grid_charge_soc):
    logic = make_logic(logic_cls, min_grid_charge_soc=min_grid_charge_soc)
    calc_input = make_calc_input(
        # Cheap current slot, then a moderate expensive block. Stored usable
        # energy covers most forecast need, so changing the target is visible.
        production=[0, 0, 0, 0, 0, 0, 0, 0],
        consumption=[1000, 600, 700, 700, 700, 700, 600, 0],
        prices=[CHEAP_PRICE] + [EXPENSIVE_PRICE] * 6 + [CHEAP_PRICE],
        soc=47.3,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 14, 0, 0, tzinfo=datetime.timezone.utc))
    return logic.get_inverter_control_settings(), logic.get_calculation_output()


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_higher_grid_charge_target_requests_more_recharge(logic_cls):
    """A caller-provided higher target can prepare for a larger expensive window.

    Dynamic target calculation can stay outside the logic layer: when the
    current cheap slot receives a higher min_grid_charge_soc, both logic
    implementations request more recharge before the future high-price block.
    """
    low_target = _calculate(logic_cls, min_grid_charge_soc=0.55)
    high_target = _calculate(logic_cls, min_grid_charge_soc=0.84)

    low_result, low_output = low_target
    high_result, high_output = high_target

    assert low_result.charge_from_grid is True
    assert high_result.charge_from_grid is True
    assert high_output.reserved_energy > low_output.reserved_energy
    assert high_output.required_recharge_energy > low_output.required_recharge_energy
    assert high_result.charge_rate > low_result.charge_rate
