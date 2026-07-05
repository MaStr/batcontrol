"""Scenario tests for grid-charge targets."""
import datetime

import pytest

from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.grid_charge_target import GridChargeTargetConfig
from batcontrol.logic.next import NextLogic

from .helpers import (
    CAPACITY_WH,
    CHEAP_PRICE,
    EXPENSIVE_PRICE,
    MIN_SOC,
    make_calc_input,
    make_logic,
)


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
def test_forecast_strategy_adds_high_price_need_to_soc_floor(logic_cls):
    """Forecast target keeps the configured floor after the expensive window."""
    logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.55,
        max_charging_from_grid_limit=1.0,
        preserve_min_grid_charge_soc=False,
        grid_charge_target=GridChargeTargetConfig(strategy='forecast'),
    )
    calc_input = make_calc_input(
        production=[0, 0, 0, 0],
        consumption=[0, 2000, 2000, 0],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE, EXPENSIVE_PRICE, CHEAP_PRICE],
        soc=20.0,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))

    result = logic.get_inverter_control_settings()
    output = logic.get_calculation_output()

    high_price_required_energy = 4000.0
    target_energy = CAPACITY_WH * 0.55 + high_price_required_energy
    expected_recharge = target_energy - calc_input.stored_energy + 100

    assert result.charge_from_grid is True
    assert output.required_recharge_energy == pytest.approx(expected_recharge)
    assert output.effective_min_grid_charge_soc == pytest.approx(
        target_energy / CAPACITY_WH)


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_forecast_strategy_does_not_charge_floor_without_high_price_need(logic_cls):
    """Forecast strategy does not fill the reserve when no high-price need exists."""
    logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.55,
        preserve_min_grid_charge_soc=False,
        grid_charge_target=GridChargeTargetConfig(strategy='forecast'),
    )
    calc_input = make_calc_input(
        production=[0, 0, 0],
        consumption=[0, 0, 0],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE, CHEAP_PRICE],
        soc=20.0,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))

    result = logic.get_inverter_control_settings()
    output = logic.get_calculation_output()

    assert result.charge_from_grid is False
    assert output.required_recharge_energy == 0.0


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_forecast_strategy_preserve_adds_reserve_only_when_enabled(logic_cls):
    """Preserve mode protects floor plus high-price need; disabled mode does not."""
    calc_input = make_calc_input(
        production=[0, 0, 0, 0],
        consumption=[0, 1500, 1500, 0],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE, EXPENSIVE_PRICE, CHEAP_PRICE],
        soc=60.0,
    )
    disabled_logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.55,
        preserve_min_grid_charge_soc=False,
        grid_charge_target=GridChargeTargetConfig(strategy='forecast'),
    )
    enabled_logic = make_logic(
        logic_cls,
        min_grid_charge_soc=0.55,
        preserve_min_grid_charge_soc=True,
        grid_charge_target=GridChargeTargetConfig(strategy='forecast'),
    )

    assert disabled_logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))
    assert enabled_logic.calculate(calc_input, datetime.datetime(
        2026, 4, 30, 5, 0, 0, tzinfo=datetime.timezone.utc))

    disabled_result = disabled_logic.get_inverter_control_settings()
    disabled_output = disabled_logic.get_calculation_output()
    enabled_result = enabled_logic.get_inverter_control_settings()
    enabled_output = enabled_logic.get_calculation_output()

    high_price_required_energy = 3000.0
    floor_usable_energy = CAPACITY_WH * (0.55 - MIN_SOC)
    expected_reserve = floor_usable_energy + high_price_required_energy

    assert disabled_result.allow_discharge is True
    assert disabled_output.reserved_energy == pytest.approx(
        high_price_required_energy)
    assert enabled_result.allow_discharge is False
    assert enabled_output.reserved_energy == pytest.approx(expected_reserve)
