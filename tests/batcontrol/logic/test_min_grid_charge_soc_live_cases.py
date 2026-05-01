"""Regression-style tests based on a real overnight grid-charge scenario."""
import datetime

import pytest
from pytest import approx

from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.next import NextLogic

from .helpers import (
    CAPACITY_WH,
    CHEAP_PRICE,
    EXPENSIVE_PRICE,
    MIN_GRID_CHARGE_SOC,
    make_calc_input,
    make_logic,
    target_usable_energy,
)


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_preserves_battery_at_start_of_cheap_window(logic_cls):
    """A fixed target preserves battery instead of discharging through a cheap plateau."""
    logic = make_logic(logic_cls)
    calc_input = make_calc_input(
        # Forecast snapshot around 2026-04-28 22:00.
        production=[0, 0, 0, 0, 0, 0, 0, 129, 570, 1361, 2281, 2579, 2406],
        consumption=[854, 765, 635, 691, 571, 708, 912, 1208, 1221, 1469, 1237, 1106, 983],
        prices=[CHEAP_PRICE] * 8 + [EXPENSIVE_PRICE] * 5,
        soc=34.9,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 28, 22, 0, 0, tzinfo=datetime.timezone.utc))
    result = logic.get_inverter_control_settings()
    calc_output = logic.get_calculation_output()

    assert result.allow_discharge is False
    assert calc_output.reserved_energy == approx(target_usable_energy())


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_charges_at_last_cheap_hour_before_expensive_window(logic_cls):
    """At the last cheap hour, a fixed target causes grid charging before high prices."""
    logic = make_logic(logic_cls)
    calc_input = make_calc_input(
        # Forecast snapshot around 2026-04-29 05:00.
        production=[128, 541, 1196, 2022, 2615, 2728],
        consumption=[1208, 1221, 1469, 1237, 1106, 983],
        prices=[CHEAP_PRICE] + [EXPENSIVE_PRICE] * 5,
        soc=16.9,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 29, 5, 0, 0, tzinfo=datetime.timezone.utc))
    result = logic.get_inverter_control_settings()
    calc_output = logic.get_calculation_output()

    target_recharge_energy = CAPACITY_WH * MIN_GRID_CHARGE_SOC - calc_input.stored_energy
    assert result.allow_discharge is False
    assert result.charge_from_grid is True
    assert result.charge_rate > 0
    assert calc_output.required_recharge_energy >= target_recharge_energy


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_stops_grid_charging_when_gap_is_below_threshold(logic_cls):
    """When the fixed target is nearly reached, avoid a tiny final grid-charge burst."""
    logic = make_logic(logic_cls)
    calc_input = make_calc_input(
        # Similar to 2026-04-29 14:54: target gap was below min_recharge_amount.
        production=[0, 0],
        consumption=[1000, 1000],
        prices=[CHEAP_PRICE, EXPENSIVE_PRICE],
        soc=54.5,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 29, 14, 54, 0, tzinfo=datetime.timezone.utc))
    result = logic.get_inverter_control_settings()
    calc_output = logic.get_calculation_output()

    assert result.allow_discharge is False
    assert result.charge_from_grid is False
    assert calc_output.required_recharge_energy == 0.0


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_allows_discharge_after_target_when_price_is_high(logic_cls):
    """After reaching the target, high-price periods can use the preserved battery."""
    logic = make_logic(logic_cls)
    calc_input = make_calc_input(
        # Similar to 2026-04-29 15:00 after the battery reached the 55% target.
        production=[2491, 2118, 1946, 1687, 1234, 664, 179, 0, 0, 0],
        consumption=[746, 986, 989, 1026, 1361, 1316, 1047, 790, 564, 665],
        prices=[EXPENSIVE_PRICE] * 7 + [CHEAP_PRICE] * 3,
        soc=55.1,
    )

    assert logic.calculate(calc_input, datetime.datetime(
        2026, 4, 29, 15, 0, 0, tzinfo=datetime.timezone.utc))
    result = logic.get_inverter_control_settings()
    calc_output = logic.get_calculation_output()

    assert result.allow_discharge is True
    assert result.charge_from_grid is False
    assert calc_output.reserved_energy == 0.0
