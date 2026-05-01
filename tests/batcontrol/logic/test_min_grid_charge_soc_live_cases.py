"""Regression-style tests based on a real overnight grid-charge scenario."""
import datetime

import numpy as np
import pytest
from pytest import approx

from batcontrol.logic.common import CommonLogic
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic_interface import CalculationInput, CalculationParameters
from batcontrol.logic.next import NextLogic


CAPACITY_WH = 10240
MIN_SOC = 0.10
MAX_CHARGING_FROM_GRID_LIMIT = 0.89
MIN_GRID_CHARGE_SOC = 0.55
CHEAP_PRICE = 0.4635
EXPENSIVE_PRICE = 0.7018


@pytest.fixture(autouse=True)
def reset_common_logic():
    """Keep the CommonLogic singleton from leaking settings between cases."""
    CommonLogic._instance = None
    yield
    CommonLogic._instance = None


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_preserves_battery_at_start_of_cheap_window(logic_cls):
    """A fixed target preserves battery instead of discharging through a cheap plateau."""
    logic = _make_logic(logic_cls)
    calc_input = _make_input(
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
    assert calc_output.reserved_energy == approx(_target_usable_energy())


@pytest.mark.parametrize("logic_cls", [DefaultLogic, NextLogic])
def test_min_grid_charge_soc_charges_at_last_cheap_hour_before_expensive_window(logic_cls):
    """At the last cheap hour, a fixed target causes grid charging before high prices."""
    logic = _make_logic(logic_cls)
    calc_input = _make_input(
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
    logic = _make_logic(logic_cls)
    calc_input = _make_input(
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
    logic = _make_logic(logic_cls)
    calc_input = _make_input(
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


def _make_logic(logic_cls):
    CommonLogic.get_instance(
        charge_rate_multiplier=1.1,
        always_allow_discharge_limit=0.90,
        max_capacity=CAPACITY_WH,
        min_charge_energy=100,
    )
    logic = logic_cls(timezone=datetime.timezone.utc, interval_minutes=60)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=MAX_CHARGING_FROM_GRID_LIMIT,
        min_price_difference=0.05,
        min_price_difference_rel=0.10,
        max_capacity=CAPACITY_WH,
        min_grid_charge_soc=MIN_GRID_CHARGE_SOC,
        preserve_min_grid_charge_soc=True,
    ))
    return logic


def _make_input(production, consumption, prices, soc):
    stored_energy = CAPACITY_WH * soc / 100
    min_soc_energy = CAPACITY_WH * MIN_SOC
    return CalculationInput(
        production=np.array(production, dtype=float),
        consumption=np.array(consumption, dtype=float),
        prices={slot: price for slot, price in enumerate(prices)},
        stored_energy=stored_energy,
        stored_usable_energy=max(0.0, stored_energy - min_soc_energy),
        free_capacity=CAPACITY_WH - stored_energy,
    )


def _target_usable_energy():
    return CAPACITY_WH * (MIN_GRID_CHARGE_SOC - MIN_SOC)
