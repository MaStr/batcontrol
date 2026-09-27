"""Tests for spreading grid charging over the charging window (issue #431).

With soften_price_difference_on_charging enabled, consecutive slots with a
price not higher than the current slot belong to the same charging window.
The charge rate is calculated over the whole window instead of the remaining
time of the current slot only.
"""
import datetime

import pytest

from batcontrol.logic.common import count_grid_charge_window_slots
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic import Logic
from batcontrol.logic.next import NextLogic

from .helpers import make_calc_input, make_logic

CHEAP = 0.10
EXPENSIVE = 0.50
CHARGE_RATE_MULTIPLIER = 1.1

LOGIC_CLASSES = [DefaultLogic, NextLogic]


def _setup_logic(logic_cls, interval_minutes=15, soften=True, spread=None):
    logic = make_logic(
        logic_cls,
        interval_minutes=interval_minutes,
        min_grid_charge_soc=None,
        preserve_min_grid_charge_soc=False,
        charge_rate_multiplier=CHARGE_RATE_MULTIPLIER,
    )
    logic.set_soften_price_difference_on_charging(soften, 5)
    if spread is not None:
        logic.spread_grid_charge_over_charge_window = spread
    return logic


def _run(logic, prices, consumption_per_slot, calc_timestamp, soc=30):
    slots = len(prices)
    calc_input = make_calc_input(
        production=[0.0] * slots,
        consumption=[consumption_per_slot] * slots,
        prices=prices,
        soc=soc,
    )
    logic.calculate(calc_input, calc_timestamp)
    return logic.get_inverter_control_settings()


def _expected_rate(logic, charge_time_hours):
    recharge_energy = logic.get_calculation_output().required_recharge_energy
    return int(round(recharge_energy / charge_time_hours * CHARGE_RATE_MULTIPLIER))


# 12:15 -> 11 cheap slots until 15:00, then 6 hours expensive
PRICES_15MIN = [CHEAP] * 11 + [EXPENSIVE] * 24
TS_1215 = datetime.datetime(2026, 9, 26, 12, 15, 0, tzinfo=datetime.timezone.utc)


class TestCountChargeWindowSlots:
    def test_equal_prices_are_counted(self):
        assert count_grid_charge_window_slots([0.1, 0.1, 0.1, 0.5], 4) == 2

    def test_cheaper_prices_are_counted(self):
        assert count_grid_charge_window_slots([0.1, 0.099, 0.1, 0.5], 4) == 2

    def test_stops_at_first_more_expensive_slot(self):
        assert count_grid_charge_window_slots([0.1, 0.1, 0.1001, 0.1, 0.5], 5) == 1

    def test_limited_by_window_end(self):
        assert count_grid_charge_window_slots([0.1, 0.1, 0.1, 0.1, 0.5], 2) == 1

    def test_no_following_slot(self):
        assert count_grid_charge_window_slots([0.1, 0.5], 2) == 0
        assert count_grid_charge_window_slots({0: 0.1}, 1) == 0


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestChargeWindow:
    def test_default_enabled(self, logic_cls):
        logic = _setup_logic(logic_cls)
        assert logic.spread_grid_charge_over_charge_window is True

    def test_charge_rate_spread_over_equal_price_slots(self, logic_cls):
        """Issue #431: 12:15 with cheap price until 15:00 -> 2.75 h window."""
        logic = _setup_logic(logic_cls)
        result = _run(logic, PRICES_15MIN, 200, TS_1215)

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 2.75)

    def test_disabled_uses_current_slot_only(self, logic_cls):
        logic = _setup_logic(logic_cls, spread=False)
        result = _run(logic, PRICES_15MIN, 200, TS_1215)

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 0.25)

    def test_more_expensive_slot_ends_window(self, logic_cls):
        """A marginally more expensive slot is not part of the window."""
        prices = list(PRICES_15MIN)
        prices[4] = CHEAP + 0.001
        logic = _setup_logic(logic_cls)
        result = _run(logic, prices, 200, TS_1215)

        assert result.charge_from_grid
        # current slot (0.25 h) + slots 1..3
        assert result.charge_rate == _expected_rate(logic, 1.0)

    def test_cheaper_following_slot_is_part_of_window(self, logic_cls):
        """Slightly cheaper slots (within the soften margin) extend the window."""
        prices = list(PRICES_15MIN)
        prices[4] = CHEAP - 0.005  # above soften threshold 0.10 - 0.05/5
        logic = _setup_logic(logic_cls)
        result = _run(logic, prices, 200, TS_1215)

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 2.75)

    def test_partial_current_slot(self, logic_cls):
        """Remaining time of the current slot is added to the window."""
        logic = _setup_logic(logic_cls)
        calc_timestamp = TS_1215.replace(minute=21)
        result = _run(logic, PRICES_15MIN, 200, calc_timestamp)

        assert result.charge_from_grid
        # 9 minutes left in the current slot + 10 following slots
        assert result.charge_rate == _expected_rate(logic, 9 / 60 + 2.5)

    def test_last_slot_of_window_unchanged(self, logic_cls):
        """In the last cheap slot the behaviour equals the old calculation."""
        logic = _setup_logic(logic_cls)
        result = _run(logic, [CHEAP] + [EXPENSIVE] * 24, 200,
                      TS_1215.replace(hour=14, minute=45))

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 0.25)

    def test_without_soften_no_effect(self, logic_cls):
        """Without soften the evaluation window ends at the next equal price."""
        logic = _setup_logic(logic_cls, soften=False)
        result = _run(logic, PRICES_15MIN, 200, TS_1215)

        assert not result.charge_from_grid
        assert not result.allow_discharge

    def test_60_minute_interval(self, logic_cls):
        logic = _setup_logic(logic_cls, interval_minutes=60)
        prices = [CHEAP] * 3 + [EXPENSIVE] * 6
        result = _run(logic, prices, 800, TS_1215.replace(minute=0))

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 3.0)


@pytest.mark.parametrize('logic_type', ['default', 'next'])
@pytest.mark.parametrize('value', [True, False])
def test_factory_sets_expert_parameter(logic_type, value):
    config = {
        'battery_control': {'type': logic_type},
        'battery_control_expert': {'spread_grid_charge_over_charge_window': value},
    }
    logic = Logic.create_logic(15, config, datetime.timezone.utc)
    assert logic.spread_grid_charge_over_charge_window is value
