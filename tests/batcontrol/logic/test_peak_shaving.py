"""Tests for the NextLogic peak shaving feature.

Tests cover:
- _calculate_peak_shaving_charge_limit algorithm
- _apply_peak_shaving decision logic
- Full calculate() integration with peak shaving
- Logic factory type selection
"""
import logging
import unittest
import datetime
import numpy as np

from batcontrol.logic.next import NextLogic
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic import Logic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
    InverterControlSettings,
    PeakShavingConfig,
)
from batcontrol.logic.common import CommonLogic

logging.basicConfig(level=logging.DEBUG)


class TestPeakShavingAlgorithm(unittest.TestCase):
    """Tests for _calculate_peak_shaving_charge_limit."""

    def setUp(self):
        self.max_capacity = 10000  # 10 kWh
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=self.max_capacity,
        )
        self.params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14),
        )
        self.logic.set_calculation_parameters(self.params)

    def _make_input(self, production, consumption, stored_energy,
                    free_capacity):
        """Helper to build a CalculationInput."""
        prices = np.zeros(len(production))
        return CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.array(consumption, dtype=float),
            prices=prices,
            stored_energy=stored_energy,
            stored_usable_energy=stored_energy - self.max_capacity * 0.05,
            free_capacity=free_capacity,
        )

    def test_high_surplus_small_free_capacity(self):
        """High PV surplus, small free capacity -> low charge limit (counter-linear ramp)."""
        # 8 hours until 14:00 starting from 06:00
        # 5000 W PV per slot, 500 W consumption -> 4500 W surplus per slot
        # 8 slots * 4500 Wh = 36000 Wh surplus total
        # free_capacity = 2000 Wh
        # counter-linear ramp: current slot weight=1, total weight=8*9/2=36
        # wh_current = 2 * 2000 / (8 * 9) = 55.5 Wh -> 55 W (60 min intervals)
        production = [5000] * 8 + [0] * 4
        consumption = [500] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=8000,
                                      free_capacity=2000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 55)

    def test_low_surplus_large_free_capacity(self):
        """Low PV surplus, large free capacity -> no limit (-1)."""
        # 1000 W PV, 800 W consumption -> 200 W surplus
        # 8 slots * 200 Wh = 1600 Wh surplus
        # free_capacity = 5000 Wh -> surplus < free -> no limit
        production = [1000] * 8 + [0] * 4
        consumption = [800] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=5000,
                                      free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, -1)

    def test_surplus_equals_free_capacity(self):
        """PV surplus exactly matches free capacity -> no limit (-1)."""
        production = [3000] * 8 + [0] * 4
        consumption = [1000] * 8 + [0] * 4
        # surplus per slot = 2000 W, 8 slots = 16000 Wh
        calc_input = self._make_input(production, consumption,
                                      stored_energy=0,
                                      free_capacity=16000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, -1)

    def test_battery_full(self):
        """Battery full (free_capacity = 0) -> charge limit = 0."""
        production = [5000] * 8 + [0] * 4
        consumption = [500] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=10000,
                                      free_capacity=0)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 0)

    def test_past_target_hour(self):
        """Past target hour -> no limit (-1)."""
        production = [5000] * 8
        consumption = [500] * 8
        calc_input = self._make_input(production, consumption,
                                      stored_energy=5000,
                                      free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 15, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, -1)

    def test_one_slot_remaining(self):
        """1 slot remaining -> rate for that single slot."""
        # Target is 14:00, current time is 13:00 -> 1 slot
        # PV surplus: 5000 - 500 = 4500 W -> 4500 Wh > free_cap 1000
        # limit = 1000 / 1 = 1000 Wh/slot -> 1000 W
        production = [5000] * 2
        consumption = [500] * 2
        calc_input = self._make_input(production, consumption,
                                      stored_energy=9000,
                                      free_capacity=1000)
        ts = datetime.datetime(2025, 6, 20, 13, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 1000)

    def test_consumption_reduces_surplus(self):
        """High consumption reduces effective PV surplus."""
        # 3000 W PV, 2000 W consumption -> 1000 W surplus
        # 8 slots * 1000 Wh = 8000 Wh surplus
        # free_capacity = 4000 Wh -> surplus > free
        # counter-linear ramp: wh_current = 2*4000/(8*9) = 111.1 Wh -> 111 W
        production = [3000] * 8 + [0] * 4
        consumption = [2000] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=6000,
                                      free_capacity=4000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 111)

    def test_15min_intervals(self):
        """Test counter-linear ramp with 15-minute intervals."""
        logic_15 = NextLogic(timezone=datetime.timezone.utc,
                             interval_minutes=15)
        logic_15.set_calculation_parameters(self.params)

        # Target 14:00, current 13:00 -> 4 slots of 15 min
        # surplus = 4000 W per slot, interval_hours = 0.25
        # surplus Wh per slot = 4000 * 0.25 = 1000 Wh
        # total surplus = 4 * 1000 = 4000 Wh
        # free_capacity = 1000 Wh -> surplus > free
        # counter-linear ramp (n=4, ih=0.25):
        #   wh_current = 2*1000/(4*5) = 100 Wh
        #   charge_rate = 100 / 0.25 = 400 W
        production = [4500] * 4
        consumption = [500] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=9000,
                                      free_capacity=1000)
        ts = datetime.datetime(2025, 6, 20, 13, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = logic_15._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 400)


class TestPeakShavingDecision(unittest.TestCase):
    """Tests for _apply_peak_shaving decision logic."""

    def setUp(self):
        self.max_capacity = 10000
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=self.max_capacity,
        )
        self.params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='combined',
                # required; tests use high prices so no cheap slots
                price_limit=0.05),
        )
        self.logic.set_calculation_parameters(self.params)

    def _make_settings(self, allow_discharge=True, charge_from_grid=False,
                       charge_rate=0, limit_battery_charge_rate=-1):
        return InverterControlSettings(
            allow_discharge=allow_discharge,
            charge_from_grid=charge_from_grid,
            charge_rate=charge_rate,
            limit_battery_charge_rate=limit_battery_charge_rate,
        )

    def _make_input(self, production, consumption, stored_energy,
                    free_capacity):
        # Use high prices (10.0) so no slot is "cheap" - only time-based limit applies.
        prices = np.ones(len(production)) * 10.0
        return CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.array(consumption, dtype=float),
            prices=prices,
            stored_energy=stored_energy,
            stored_usable_energy=stored_energy - self.max_capacity * 0.05,
            free_capacity=free_capacity,
        )

    def test_nighttime_no_production(self):
        """No production (nighttime) -> peak shaving skipped."""
        settings = self._make_settings()
        calc_input = self._make_input(
            [0, 0, 0, 0], [500, 500, 500, 500],
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 2, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_after_target_hour(self):
        """After target hour -> no change."""
        settings = self._make_settings()
        calc_input = self._make_input(
            [5000, 5000], [500, 500],
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 15, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_force_charge_takes_priority(self):
        """Force charge (MODE -1) -> peak shaving skipped."""
        settings = self._make_settings(
            allow_discharge=False, charge_from_grid=True, charge_rate=3000)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertTrue(result.charge_from_grid)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_always_allow_discharge_region(self):
        """Battery in always_allow_discharge region -> skip peak shaving."""
        settings = self._make_settings()
        # stored_energy=9500 > 10000 * 0.9 = 9000
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=9500, free_capacity=500)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_peak_shaving_applies_limit(self):
        """Before target hour, limit calculated -> limit set."""
        settings = self._make_settings()
        # 6 slots (08..14), 5000W PV, 500W consumption -> 4500W surplus
        # surplus Wh = 6 * 4500 = 27000 > free 3000
        # counter-linear ramp: wh_current = 2*3000/(6*7) = 142.8 Wh -> 142 W
        # 142 W < MIN_CHARGE_RATE (500 W) -> enforced to 500 W
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)
        self.assertTrue(result.allow_discharge)

    def test_existing_tighter_limit_kept(self):
        """Existing limit is tighter -> keep existing."""
        settings = self._make_settings(limit_battery_charge_rate=200)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 200)

    def test_peak_shaving_limit_tighter(self):
        """Peak shaving limit is tighter than existing -> peak shaving limit applied."""
        settings = self._make_settings(limit_battery_charge_rate=5000)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)

    def test_discharge_not_allowed_skips_peak_shaving(self):
        """Discharge not allowed (battery preserved for high-price hours) -> skip."""
        settings = self._make_settings(allow_discharge=False)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)
        self.assertFalse(result.allow_discharge)

    def test_price_limit_none_combined_falls_back_to_time_only(self):
        """price_limit=None with mode='combined' -> falls back to time-only.

        The time component does not need price_limit, so combined mode
        remains active with only the time-based limiter. At 08:00 with
        target hour 14 (6 slots remaining) and production 5000 W,
        consumption 500 W, free_capacity 5000 Wh, the counter-linear ramp
        yields 2*5000/(6*7) ~= 238 W, raised to the 500 W floor.
        """
        params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='combined', price_limit=None),
        )
        self.logic.set_calculation_parameters(params)
        settings = self._make_settings()
        calc_input = self._make_input([5000] * 8, [500] * 8,
                                      stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)

    def test_price_limit_none_price_mode_disables_peak_shaving(self):
        """price_limit=None with mode='price' -> peak shaving disabled.

        'price' mode has no fallback: without a price_limit, there is no
        component to apply, so the entire peak shaving is skipped.
        """
        params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='price', price_limit=None),
        )
        self.logic.set_calculation_parameters(params)
        settings = self._make_settings()
        calc_input = self._make_input([5000] * 8, [500] * 8,
                                      stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_currently_in_cheap_slot_no_limit(self):
        """In cheap slot, surplus fits in battery -> no limit applied."""
        settings = self._make_settings()
        prices = np.zeros(8)  # all slots cheap (price=0 <= 0.05)
        # production=200W, surplus=1600 Wh total < free=5000 Wh -> no limit
        calc_input = CalculationInput(
            production=np.array([200] * 8, dtype=float),
            consumption=np.zeros(8, dtype=float),
            prices=prices,
            stored_energy=5000,
            stored_usable_energy=4500,
            free_capacity=5000,
        )
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_currently_in_cheap_slot_surplus_overflow(self):
        """In cheap slot, surplus > free capacity -> spread evenly over cheap slots.

        prices all 0 (cheap), production=3000W, consumption=0, 8 slots.
        Total surplus = 8 * 3000 = 24000 Wh > free=5000 Wh.
        Price-based: spread 5000 / 8 slots = 625 W.
        Time-based (mode=combined): counter-linear ramp over 6 slots to
        target hour, current-slot weight 2*5000/(6*7) ~= 238 W, raised to
        the 500 W min_pv_charge_rate floor.
        min(625, 500) = 500.
        """
        settings = self._make_settings()
        prices = np.zeros(8)
        calc_input = CalculationInput(
            production=np.array([3000] * 8, dtype=float),
            consumption=np.zeros(8, dtype=float),
            prices=prices,
            stored_energy=5000,
            stored_usable_energy=4500,
            free_capacity=5000,
        )
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)

    def test_mode_time_only_ignores_price_limit(self):
        """Mode 'time': price_limit=None does not disable peak shaving."""
        params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                # price_limit not needed for 'time' mode
                mode='time', price_limit=None),
        )
        self.logic.set_calculation_parameters(params)
        settings = self._make_settings()
        calc_input = self._make_input([5000] * 8, [500] * 8,
                                      stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        # time-based (n=6): raw = 2*3000/(6*7) = 142 W < MIN_CHARGE_RATE -> enforced to 500 W
        self.assertEqual(result.limit_battery_charge_rate, 500)

    def test_mode_price_only_no_time_limit(self):
        """Mode 'price': only price-based component fires.

        With no cheap slots ahead (prices all 10 > 0.05), price-based
        returns -1 and no limit is applied even if time-based would fire.
        """
        params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='price', price_limit=0.05),
        )
        self.logic.set_calculation_parameters(params)
        settings = self._make_settings()
        # High prices -> no cheap slots -> price-based returns -1 -> no limit
        calc_input = self._make_input([5000] * 8, [500] * 8,
                                      stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)


class TestPeakShavingDisabled(unittest.TestCase):
    """Test that peak shaving does nothing when disabled."""

    def setUp(self):
        self.max_capacity = 10000
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=self.max_capacity,
        )
        self.params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=False, allow_full_battery_after=14),
        )
        self.logic.set_calculation_parameters(self.params)

    def test_disabled_no_limit(self):
        """peak_shaving_enabled=False -> no change to settings."""
        production = np.array([5000] * 8, dtype=float)
        consumption = np.array([500] * 8, dtype=float)
        prices = np.zeros(8)
        calc_input = CalculationInput(
            production=production,
            consumption=consumption,
            prices=prices,
            stored_energy=5000,
            stored_usable_energy=4500,
            free_capacity=5000,
        )
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        self.logic.calculate(calc_input, ts)
        result = self.logic.get_inverter_control_settings()
        # With disabled peak shaving, no limit should be applied
        self.assertEqual(result.limit_battery_charge_rate, -1)


class TestLogicFactory(unittest.TestCase):
    """Test logic factory type selection."""

    def setUp(self):
        CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=10000,
        )

    def test_default_type(self):
        """type: default -> DefaultLogic."""
        config = {'battery_control': {'type': 'default'}}
        logic = Logic.create_logic(60, config, datetime.timezone.utc)
        self.assertIsInstance(logic, DefaultLogic)

    def test_next_type(self):
        """type: next -> NextLogic."""
        config = {'battery_control': {'type': 'next'}}
        logic = Logic.create_logic(60, config, datetime.timezone.utc)
        self.assertIsInstance(logic, NextLogic)

    def test_missing_type_defaults_to_default(self):
        """No type key -> DefaultLogic."""
        config = {}
        logic = Logic.create_logic(60, config, datetime.timezone.utc)
        self.assertIsInstance(logic, DefaultLogic)

    def test_unknown_type_raises(self):
        """Unknown type -> RuntimeError."""
        config = {'battery_control': {'type': 'unknown'}}
        with self.assertRaises(RuntimeError):
            Logic.create_logic(60, config, datetime.timezone.utc)

    def test_expert_tuning_applied_to_next(self):
        """Expert tuning attributes applied to NextLogic."""
        config = {
            'battery_control': {'type': 'next'},
            'battery_control_expert': {
                'round_price_digits': 2,
                'charge_rate_multiplier': 1.5,
            },
        }
        logic = Logic.create_logic(60, config, datetime.timezone.utc)
        self.assertIsInstance(logic, NextLogic)
        self.assertEqual(logic.round_price_digits, 2)


class TestPeakShavingPriceBased(unittest.TestCase):
    """Tests for _calculate_peak_shaving_charge_limit_price_based."""

    def setUp(self):
        self.max_capacity = 10000
        self.interval_minutes = 60
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=self.interval_minutes)
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=self.max_capacity,
        )
        self.params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='price', price_limit=0.05),
        )
        self.logic.set_calculation_parameters(self.params)

    def _make_input(self, production, prices, free_capacity, consumption=None):
        """Helper to build CalculationInput for price-based tests."""
        n = len(production)
        if consumption is None:
            consumption = [0.0] * n
        return CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.array(consumption, dtype=float),
            prices=np.array(prices, dtype=float),
            stored_energy=self.max_capacity - free_capacity,
            stored_usable_energy=(self.max_capacity - free_capacity) * 0.95,
            free_capacity=free_capacity,
        )

    def test_surplus_exceeds_free_capacity_blocks_charging(self):
        """
        Cheap slots at index 5 and 6 (price=0 <= 0.05).
        Surplus in cheap slots: 3000+3000 = 6000 Wh (interval=1h, consumption=0).
        target_reserve = min(6000, 10000) = 6000 Wh.
        free_capacity = 4000 Wh.
        additional_allowed = 4000 - 6000 = -2000 -> block charging (return 0).
        """
        prices = [10, 10, 10, 8, 3, 0, 0, 1]
        production = [500, 500, 500, 500, 500, 3000, 3000, 500]
        calc_input = self._make_input(production, prices, free_capacity=4000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, 0)

    def test_partial_reserve_spread_over_slots(self):
        """
        2 cheap slots with 3000 Wh PV surplus each = 6000 Wh total.
        Battery has 8000 Wh free, target_reserve = min(6000, 10000) = 6000 Wh.
        additional_allowed = 8000 - 6000 = 2000 Wh.
        first_cheap_slot = 4 (4 slots before cheap window).
        wh_per_slot = 2000 / 4 = 500 Wh -> rate = 500 W (60 min intervals).
        """
        prices = [10, 10, 10, 10, 0, 0, 1, 2]
        # cheap surplus slots 4,5: 3000W each, interval=1h -> 3000 Wh each
        production = [500, 500, 500, 500, 3000, 3000, 500, 500]
        calc_input = self._make_input(production, prices, free_capacity=8000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, 500)

    def test_no_cheap_slots_returns_minus_one(self):
        """No cheap slots in prices -> -1."""
        prices = [10, 10, 10, 10, 10, 10]
        production = [3000] * 6
        calc_input = self._make_input(production, prices, free_capacity=5000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, -1)

    def test_currently_in_cheap_slot_fits_no_limit(self):
        """first_cheap_slot = 0, surplus fits in battery -> -1 (no limit)."""
        prices = [0, 0, 10, 10]
        # surplus per cheap slot = 200W * 1h = 200 Wh each; total = 400 Wh < free 5000
        production = [200, 200, 500, 500]
        calc_input = self._make_input(production, prices, free_capacity=5000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, -1)

    def test_currently_in_cheap_slot_surplus_overflow_spreads(self):
        """first_cheap_slot = 0, surplus > free capacity -> spread over cheap slots.

        cheap slots: [0, 1], production=4000W each, consumption=0, interval=1h.
        total_surplus = 2 * 4000 = 8000 Wh > free = 5000 Wh.
        charge_rate = 5000 / 2 / 1h = 2500 W.
        """
        prices = [0, 0, 10, 10]
        production = [4000, 4000, 500, 500]
        calc_input = self._make_input(production, prices, free_capacity=5000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, 2500)

    def test_zero_pv_surplus_in_cheap_slots_returns_minus_one(self):
        """Cheap slots have no PV surplus (consumption >= production) -> -1."""
        prices = [10, 10, 0, 0]
        production = [500, 500, 200, 200]
        consumption = [500, 500, 300, 300]  # net = 0 or negative in cheap slots
        calc_input = self._make_input(production, prices, free_capacity=5000,
                                      consumption=consumption)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, -1)

    def test_free_capacity_well_above_reserve_gives_rate(self):
        """
        cheap surplus = 1000 Wh, target_reserve = 1000 Wh.
        free_capacity = 6000 Wh -> additional_allowed = 5000 Wh.
        first_cheap_slot = 5 -> wh_per_slot = 1000 W.
        """
        prices = [10, 10, 10, 10, 10, 0, 10]
        production = [500, 500, 500, 500, 500, 1000, 500]
        calc_input = self._make_input(production, prices, free_capacity=6000)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, 1000)

    def test_consumption_reduces_cheap_surplus(self):
        """
        Cheap slot: production=5000W, consumption=3000W -> surplus=2000 Wh.
        target_reserve = 2000, free=5000 -> additional=3000 Wh.
        first_cheap_slot=2 -> rate = 3000/2 = 1500 W.
        """
        prices = [10, 10, 0, 10]
        production = [500, 500, 5000, 500]
        consumption = [200, 200, 3000, 200]
        calc_input = self._make_input(production, prices, free_capacity=5000,
                                      consumption=consumption)
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, 1500)

    def test_combine_price_and_time_limits_stricter_wins(self):
        """
        Both limits active (mode='combined'): stricter limit wins.
        Setup at 08:00, target 14:00 (6 slots remaining).
        High prices except slot 4 (cheap).
        """
        params_combined = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14,
                mode='combined', price_limit=0.05),
        )
        logic = NextLogic(timezone=datetime.timezone.utc, interval_minutes=60)
        logic.set_calculation_parameters(params_combined)

        ts = datetime.datetime(2025, 6, 20, 8, 0, 0, tzinfo=datetime.timezone.utc)
        prices = np.array([10, 10, 10, 10, 0, 10, 10, 10], dtype=float)
        production = np.array([500, 500, 500, 500, 5000, 5000, 500, 500], dtype=float)
        calc_input = CalculationInput(
            production=production,
            consumption=np.ones(8) * 500,
            prices=prices,
            stored_energy=7000,
            stored_usable_energy=6500,
            free_capacity=3000,
        )
        settings = InverterControlSettings(
            allow_discharge=True, charge_from_grid=False,
            charge_rate=0, limit_battery_charge_rate=-1)
        result = logic._apply_peak_shaving(settings, calc_input, ts)

        # Both limits should be considered; combined must be <= each individually
        price_lim = logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        time_lim = logic._calculate_peak_shaving_charge_limit(calc_input, ts)
        expected = min(x for x in [price_lim, time_lim] if x >= 0)
        self.assertEqual(result.limit_battery_charge_rate, expected)

    def test_cheap_slot_beyond_production_window_ignored(self):
        """Regression: cheap slots with zero production must NOT trigger a reserve.

        Scenario mirrors the real-world bug message:
          [PeakShaving] Price-based: cheap window at slot 27, reserve=4376 Wh ...

        Slot 27 is nighttime (after the production window ends).  The prices
        array has a cheap slot there, but production is 0 from slot 8 onwards.
        The algorithm must ignore all slots at or beyond the first zero-production
        slot and therefore find no cheap window -> return -1 (no limit).
        """
        # 8 slots with production, then 24 night slots with production=0
        n_day = 8
        n_night = 24
        production = [2000.0] * n_day + [0.0] * n_night
        # Make ONLY the night slots cheap; day slots have high prices
        prices = [10.0] * n_day + [0.0] * n_night
        n = n_day + n_night
        calc_input = CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.zeros(n, dtype=float),
            prices=np.array(prices, dtype=float),
            stored_energy=self.max_capacity - 5000,
            stored_usable_energy=(self.max_capacity - 5000) * 0.95,
            free_capacity=5000,
        )
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        self.assertEqual(result, -1,
                         "Cheap slots after production window end must be ignored")

    def test_cheap_slot_within_production_window_still_triggers_reserve(self):
        """After the bug-fix the production-window limit must not suppress legitimate
        cheap windows that fall inside the production window.

        Cheap slot 4 has production=3000 W (within the window that ends at slot 8).
        The algorithm must still compute a reserve for that slot.
        """
        n_day = 8
        n_night = 16
        production = [500.0] * n_day + [0.0] * n_night
        prices = [10.0] * 4 + [0.0] + [10.0] * (n_day - 5) + [10.0] * n_night
        production[4] = 3000.0  # cheap slot inside production window
        n = n_day + n_night
        calc_input = CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.zeros(n, dtype=float),
            prices=np.array(prices, dtype=float),
            stored_energy=self.max_capacity - 8000,
            stored_usable_energy=(self.max_capacity - 8000) * 0.95,
            free_capacity=8000,
        )
        result = self.logic._calculate_peak_shaving_charge_limit_price_based(calc_input)
        # A limit > 0 must be produced (not -1 and not some invalid value)
        self.assertGreater(result, 0,
                           "Cheap slot inside production window must still trigger a limit")


class TestPeakShavingMinChargeRate(unittest.TestCase):
    """Tests for the minimum charge rate enforcement in _apply_peak_shaving.

    The enforcement uses CommonLogic.enforce_min_pv_charge_rate() which applies
    the module-level MIN_CHARGE_RATE constant from common.py.
    A computed limit of 0 (block charging entirely) must never be raised.
    """

    _MAX_CAPACITY = 10000  # Wh

    def _make_logic(self):
        CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.90,
            max_capacity=self._MAX_CAPACITY,
        )
        logic = NextLogic(timezone=datetime.timezone.utc, interval_minutes=60)
        params = CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self._MAX_CAPACITY,
            peak_shaving=PeakShavingConfig(
                enabled=True, allow_full_battery_after=14, mode='time'),
        )
        logic.set_calculation_parameters(params)
        return logic

    def _make_input(self, production, consumption, free_capacity,
                    stored_energy=None):
        """Helper - prices unused for time-mode tests.

        stored_energy defaults to half of max_capacity so it stays well below
        the always_allow_discharge threshold (90% * 10 000 = 9 000 Wh),
        allowing _apply_peak_shaving to proceed without being skipped.
        free_capacity and stored_energy are intentionally decoupled here
        to isolate the charge-limit computation from the guard check.
        """
        if stored_energy is None:
            stored_energy = self._MAX_CAPACITY * 0.5  # 5 000 Wh – below gate
        n = len(production)
        return CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.array(consumption, dtype=float),
            prices=np.zeros(n),
            stored_energy=float(stored_energy),
            stored_usable_energy=float(stored_energy),
            free_capacity=float(free_capacity),
        )

    def test_low_positive_limit_raised_to_min_charge_rate(self):
        """A computed limit below MIN_CHARGE_RATE must be raised to MIN_CHARGE_RATE."""
        from batcontrol.logic.common import MIN_CHARGE_RATE
        logic = self._make_logic()
        # 8 slots until 14:00 from 06:00, small free capacity (200 Wh)
        # -> raw limit = 200 / 8 = 25 W, well below MIN_CHARGE_RATE (500 W)
        production = [5000] * 8 + [0] * 4
        consumption = [500] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption, free_capacity=200)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0, tzinfo=datetime.timezone.utc)
        settings = InverterControlSettings(
            allow_discharge=True, charge_from_grid=False,
            charge_rate=0, limit_battery_charge_rate=-1)

        result = logic._apply_peak_shaving(settings, calc_input, ts)

        self.assertEqual(result.limit_battery_charge_rate, MIN_CHARGE_RATE)

    def test_limit_above_min_charge_rate_kept_unchanged(self):
        """A computed limit already above MIN_CHARGE_RATE must not be altered.

        Use n=2 (12:00->14:00) with 2000 Wh free:
          raw = 2*2000/(2*3) / 1h = 4000/6 = 666.6 -> 666 W > MIN_CHARGE_RATE (500 W).
        """
        from batcontrol.logic.common import MIN_CHARGE_RATE
        logic = self._make_logic()
        # 2 slots remaining (12:00 -> 14:00), free_capacity=2000 Wh
        # raw counter-linear limit = 2*2000/(2*3) = 666 W > MIN_CHARGE_RATE
        production = [5000] * 2 + [0] * 4
        consumption = [500] * 2 + [0] * 4
        calc_input = self._make_input(production, consumption, free_capacity=2000)
        ts = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        settings = InverterControlSettings(
            allow_discharge=True, charge_from_grid=False,
            charge_rate=0, limit_battery_charge_rate=-1)

        result = logic._apply_peak_shaving(settings, calc_input, ts)

        self.assertGreater(result.limit_battery_charge_rate, MIN_CHARGE_RATE)
        self.assertEqual(result.limit_battery_charge_rate, 666)

    def test_zero_limit_not_raised(self):
        """A computed limit of 0 (block charging) must stay 0."""
        logic = self._make_logic()
        production = [5000] * 8 + [0] * 4
        consumption = [500] * 8 + [0] * 4
        # free_capacity = 0 -> battery full -> raw limit = 0
        calc_input = self._make_input(production, consumption, free_capacity=0)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0, tzinfo=datetime.timezone.utc)
        settings = InverterControlSettings(
            allow_discharge=True, charge_from_grid=False,
            charge_rate=0, limit_battery_charge_rate=-1)

        result = logic._apply_peak_shaving(settings, calc_input, ts)

        self.assertEqual(result.limit_battery_charge_rate, 0)



class TestNextLogicGridRechargeLogging(unittest.TestCase):
    """Tests recharge decision logging for NextLogic."""

    def setUp(self):
        self.max_capacity = 10000
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)
        CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.80,
            max_capacity=self.max_capacity,
        )
        self.logic.set_calculation_parameters(CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            peak_shaving=PeakShavingConfig(enabled=False),
        ))

    def _make_grid_charge_input(self):
        """Build an input that triggers grid charging."""
        stored_energy = 2000
        stored_usable_energy = stored_energy - self.max_capacity * 0.05
        return CalculationInput(
            consumption=np.array([1000, 2000, 1500]),
            production=np.array([0, 0, 0]),
            prices={0: 0.20, 1: 0.35, 2: 0.30},
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=self.max_capacity - stored_energy,
        )

    def test_grid_recharge_decision_is_logged(self):
        """NextLogic logs the shared grid recharge decision summary."""
        calc_input = self._make_grid_charge_input()

        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 30, 0,
                                           tzinfo=datetime.timezone.utc)
        with self.assertLogs('batcontrol.logic.next', level='INFO') as logs:
            self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))

        result = self.logic.get_inverter_control_settings()
        self.assertTrue(result.charge_from_grid)
        log_output = '\n'.join(logs.output)
        expected_stored_usable_energy = (
            f'stored_usable_energy={calc_input.stored_usable_energy:.1f} Wh'
        )
        self.assertIn('[Rule] Grid recharge decision:', log_output)
        self.assertIn('current_price=0.200', log_output)
        self.assertIn(expected_stored_usable_energy, log_output)
        self.assertIn('charge_rate=', log_output)

    def test_min_grid_charge_soc_increases_recharge_energy(self):
        """NextLogic applies the optional minimum grid-charge SoC target."""
        self.logic.set_calculation_parameters(CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            min_grid_charge_soc=0.55,
            peak_shaving=PeakShavingConfig(enabled=False),
        ))
        calc_input = self._make_grid_charge_input()
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0,
                                           tzinfo=datetime.timezone.utc)

        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        calc_output = self.logic.get_calculation_output()

        self.assertAlmostEqual(calc_output.required_recharge_energy, 3600.0)

    def test_min_grid_charge_soc_blocks_discharge_during_cheap_window(self):
        """NextLogic expert option preserves battery before expensive demand."""
        self.logic.set_calculation_parameters(CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            min_grid_charge_soc=0.55,
            preserve_min_grid_charge_soc=True,
            peak_shaving=PeakShavingConfig(enabled=False),
        ))
        stored_energy = 5000
        stored_usable_energy = stored_energy - self.max_capacity * 0.05
        calc_input = CalculationInput(
            consumption=np.array([1000, 1000, 2000]),
            production=np.array([0, 0, 0]),
            prices={0: 0.10, 1: 0.11, 2: 0.30},
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=self.max_capacity - stored_energy,
        )
        calc_timestamp = datetime.datetime(2025, 6, 20, 22, 0, 0,
                                           tzinfo=datetime.timezone.utc)

        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        calc_output = self.logic.get_calculation_output()

        self.assertFalse(result.allow_discharge)
        self.assertTrue(result.charge_from_grid)
        self.assertGreater(calc_output.required_recharge_energy, 0)
        self.assertGreater(calc_output.reserved_energy, 2000)

    def test_min_grid_charge_soc_does_not_block_discharge_at_high_price(self):
        """NextLogic does not preserve battery when current price is high."""
        self.logic.set_calculation_parameters(CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            min_grid_charge_soc=0.55,
            preserve_min_grid_charge_soc=True,
            peak_shaving=PeakShavingConfig(enabled=False),
        ))
        stored_energy = 5000
        stored_usable_energy = stored_energy - self.max_capacity * 0.05
        calc_input = CalculationInput(
            consumption=np.array([1000, 1000, 2000]),
            production=np.array([0, 0, 0]),
            prices={0: 0.30, 1: 0.10, 2: 0.10},
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=self.max_capacity - stored_energy,
        )
        calc_timestamp = datetime.datetime(2025, 6, 20, 6, 0, 0,
                                           tzinfo=datetime.timezone.utc)

        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()

        self.assertTrue(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)
