"""Tests for spreading grid charging over the charging window (issue #431).

With soften_price_difference_on_charging enabled, consecutive slots with a
price not higher than the current slot belong to the same charging window.
The charge rate is calculated over the whole window instead of the remaining
time of the current slot only.
"""
import datetime

import pytest

import logging

from batcontrol.logic.common import (
    count_grid_charge_window_slots,
    extend_to_grid_charge_window,
)
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic import Logic, parse_logic_expert_settings
from batcontrol.logic.next import NextLogic

from .helpers import make_calc_input, make_logic

CHEAP = 0.10
EXPENSIVE = 0.50
CHARGE_RATE_MULTIPLIER = 1.1

LOGIC_CLASSES = [DefaultLogic, NextLogic]


def _setup_logic(logic_cls, interval_minutes=15, soften=True, spread=None,
                 charge_rate_multiplier=CHARGE_RATE_MULTIPLIER,
                 max_grid_charge_rate=0.0):
    logic = make_logic(
        logic_cls,
        interval_minutes=interval_minutes,
        min_grid_charge_soc=None,
        preserve_min_grid_charge_soc=False,
        charge_rate_multiplier=charge_rate_multiplier,
        max_grid_charge_rate=max_grid_charge_rate,
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


def _expected_rate(logic, charge_time_hours,
                   multiplier=CHARGE_RATE_MULTIPLIER):
    recharge_energy = logic.get_calculation_output().required_recharge_energy
    return int(round(recharge_energy / charge_time_hours * multiplier))


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


class TestCountChargeWindowSlotsTolerance:
    """charge_window_price_tolerance: slots slightly above the current price."""

    PRICES = [0.326, 0.326, 0.329, 0.326, 0.340, 0.326]

    def test_zero_tolerance_identical_to_default(self):
        cases = [
            ([0.1, 0.1, 0.1, 0.5], 4),
            ([0.1, 0.099, 0.1, 0.5], 4),
            ([0.1, 0.1, 0.1001, 0.1, 0.5], 5),
            ([0.1, 0.1, 0.1, 0.1, 0.5], 2),
            ([0.1, 0.5], 2),
            (self.PRICES, 6),
        ]
        for prices, window_end in cases:
            assert (count_grid_charge_window_slots(prices, window_end, 0.0)
                    == count_grid_charge_window_slots(prices, window_end))
        assert count_grid_charge_window_slots(self.PRICES, 6, 0.0) == 1

    def test_slot_within_tolerance_extends_window(self):
        assert count_grid_charge_window_slots(
            [0.1, 0.1, 0.101, 0.1, 0.5], 5, price_tolerance=0.01) == 3

    def test_slot_above_tolerance_ends_window(self):
        # 0.329 is within 0.326 + 0.005, 0.340 is not
        assert count_grid_charge_window_slots(
            self.PRICES, 6, price_tolerance=0.005) == 3

    def test_slot_exactly_at_limit_is_part_of_window(self):
        assert count_grid_charge_window_slots(
            [0.25, 0.375, 0.5], 3, price_tolerance=0.125) == 1

    def test_window_end_still_respected(self):
        assert count_grid_charge_window_slots(
            [0.1, 0.101, 0.101, 0.101, 0.101], 3, price_tolerance=0.01) == 2
        assert count_grid_charge_window_slots(
            [0.1, 0.101], 1, price_tolerance=0.01) == 0

    def test_extend_passes_tolerance(self):
        prices = [0.326, 0.329, 0.327, 0.5]
        assert extend_to_grid_charge_window(0.25, prices, 4, 15) == 0.25
        assert extend_to_grid_charge_window(
            0.25, prices, 4, 15, price_tolerance=0.01) == pytest.approx(0.75)

    def test_debug_log_names_tolerance(self, caplog):
        with caplog.at_level(logging.DEBUG, logger='batcontrol.logic.common'):
            extend_to_grid_charge_window(
                0.25, [0.326, 0.329, 0.5], 3, 15, price_tolerance=0.01)
        assert 'tolerance 0.010' in caplog.text


# Case from 2026-11-15, 09:15: nearly flat curve (within 1 ct, 0.1-0.2 ct
# noise) until the daily minimum, then an expensive evening block.
FLAT_NOISY_PRICES = [
    0.322, 0.322, 0.322, 0.323, 0.321, 0.322, 0.320, 0.321,
    0.319, 0.320, 0.318, 0.317, 0.316, 0.317, 0.318, 0.319,
] + [0.42] * 20
TS_0915 = datetime.datetime(2026, 11, 15, 9, 15, 0, tzinfo=datetime.timezone.utc)
MAX_GRID_CHARGE_RATE = 6000
CASE_MULTIPLIER = 1.5


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestChargeWindowPriceTolerance:
    def _setup(self, logic_cls, tolerance=None, spread=None):
        logic = _setup_logic(logic_cls, spread=spread,
                             charge_rate_multiplier=CASE_MULTIPLIER,
                             max_grid_charge_rate=MAX_GRID_CHARGE_RATE)
        if tolerance is not None:
            logic.set_charge_window_price_tolerance(tolerance)
        return logic

    def test_default_is_zero(self, logic_cls):
        logic = self._setup(logic_cls)
        assert logic.charge_window_price_tolerance == 0.0

    def test_zero_tolerance_unchanged(self, logic_cls):
        """Regression: 0.0 cuts the window at the 0.1-ct more expensive slot."""
        logic = self._setup(logic_cls, tolerance=0.0)
        result = _run(logic, FLAT_NOISY_PRICES, 300, TS_0915, soc=20)

        assert result.charge_from_grid
        # current slot + slots 1..2, slot 3 (0.323) ends the window
        assert result.charge_rate == _expected_rate(logic, 0.75, CASE_MULTIPLIER)
        assert result.charge_rate > MAX_GRID_CHARGE_RATE

    def test_flat_noisy_curve_stays_below_max_rate(self, logic_cls):
        logic = self._setup(logic_cls, tolerance=0.01)
        result = _run(logic, FLAT_NOISY_PRICES, 300, TS_0915, soc=20)

        assert result.charge_from_grid
        # current slot + all 15 following slots of the flat block
        assert result.charge_rate == _expected_rate(logic, 4.0, CASE_MULTIPLIER)
        assert result.charge_rate < MAX_GRID_CHARGE_RATE / 2

    def test_slot_above_tolerance_ends_window(self, logic_cls):
        prices = list(FLAT_NOISY_PRICES)
        prices[6] = 0.322 + 0.011
        logic = self._setup(logic_cls, tolerance=0.01)
        result = _run(logic, prices, 300, TS_0915, soc=20)

        assert result.charge_from_grid
        # current slot + slots 1..5
        assert result.charge_rate == _expected_rate(logic, 1.5, CASE_MULTIPLIER)

    def test_no_effect_without_spread(self, logic_cls):
        logic = self._setup(logic_cls, tolerance=0.01, spread=False)
        result = _run(logic, FLAT_NOISY_PRICES, 300, TS_0915, soc=20)

        assert result.charge_from_grid
        assert result.charge_rate == _expected_rate(logic, 0.25, CASE_MULTIPLIER)

    def test_recharge_window_end_unchanged(self, logic_cls):
        """The tolerance only changes the slot counting, not the deferral."""
        ends = []
        for tolerance in (0.0, 0.01):
            logic = self._setup(logic_cls, tolerance=tolerance)
            _run(logic, FLAT_NOISY_PRICES, 300, TS_0915, soc=20)
            ends.append((logic.recharge_window_end,
                         logic.get_calculation_output().required_recharge_energy))
        assert ends[0] == ends[1]


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


@pytest.mark.parametrize('logic_type', ['default', 'next'])
@pytest.mark.parametrize('value, expected', [
    (0.01, 0.01), ('0.005', 0.005), (0, 0.0)])
def test_factory_sets_price_tolerance(logic_type, value, expected):
    config = {
        'battery_control': {'type': logic_type},
        'battery_control_expert': {'charge_window_price_tolerance': value},
    }
    logic = Logic.create_logic(15, config, datetime.timezone.utc)
    assert logic.charge_window_price_tolerance == pytest.approx(expected)


def test_factory_price_tolerance_default():
    logic = Logic.create_logic(
        15, {'battery_control': {'type': 'default'}}, datetime.timezone.utc)
    assert logic.charge_window_price_tolerance == 0.0


@pytest.mark.parametrize('value', [-0.01, 'abc', True, float('nan')])
def test_invalid_price_tolerance_warns_and_falls_back(value, caplog):
    with caplog.at_level(logging.WARNING, logger='batcontrol.logic.logic'):
        settings = parse_logic_expert_settings(
            {'charge_window_price_tolerance': value})
    assert settings['charge_window_price_tolerance'] == 0.0
    assert 'charge_window_price_tolerance' in caplog.text


@pytest.mark.parametrize('value', [None, ''])
def test_empty_price_tolerance_is_zero_without_warning(value, caplog):
    with caplog.at_level(logging.WARNING, logger='batcontrol.logic.logic'):
        settings = parse_logic_expert_settings(
            {'charge_window_price_tolerance': value})
    assert settings['charge_window_price_tolerance'] == 0.0
    assert caplog.text == ''
