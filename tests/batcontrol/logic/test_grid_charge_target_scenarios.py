"""Scenario tests for grid-charge targets."""
import datetime

import pytest

from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.grid_charge_target import GridChargeTargetConfig
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
    """A higher target can prepare for a larger expensive window.

    When the current cheap slot receives a higher min_grid_charge_soc, both
    logic implementations request more recharge before the future high-price block.
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


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_forecast_grid_charge_strategy_raises_recharge_inside_logic(logic_cls):
    """Forecast strategy raises recharge need even when normal net forecast is sunny."""
    logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.10,
        preserve_min_grid_charge_soc=False,
        grid_charge_target=GridChargeTargetConfig(
            strategy='forecast',
            pv_forecast_factor=0.5,
        ),
    )
    calc_input = make_calc_input(
        production=[0, 5000, 5000, 0],
        consumption=[0, 4000, 4000, 0],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE, EXPENSIVE_PRICE, CHEAP_PRICE],
        soc=10.0,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))

    result = logic.get_inverter_control_settings()
    output = logic.get_calculation_output()

    assert output.effective_min_grid_charge_soc > 0.10
    assert output.required_recharge_energy > 0
    assert result.charge_from_grid is True


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_forecast_grid_charge_strategy_preserves_raised_target(logic_cls):
    """Preserve mode uses the forecast-raised target, not only normal net demand."""
    logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.10,
        preserve_min_grid_charge_soc=True,
        grid_charge_target=GridChargeTargetConfig(
            strategy='forecast',
            pv_forecast_factor=0.5,
        ),
    )
    calc_input = make_calc_input(
        production=[0, 5000, 5000, 0],
        consumption=[0, 4000, 4000, 0],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE, EXPENSIVE_PRICE, CHEAP_PRICE],
        soc=30.0,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))

    result = logic.get_inverter_control_settings()
    output = logic.get_calculation_output()

    assert output.effective_min_grid_charge_soc > 0.10
    assert output.reserved_energy > calc_input.stored_usable_energy
    assert result.allow_discharge is False
