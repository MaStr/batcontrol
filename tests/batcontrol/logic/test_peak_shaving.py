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
            peak_shaving_enabled=True,
            peak_shaving_allow_full_after=14,
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
        """High PV surplus, small free capacity → low charge limit."""
        # 8 hours until 14:00 starting from 06:00
        # 5000 W PV per slot, 500 W consumption → 4500 W surplus per slot
        # 8 slots * 4500 Wh = 36000 Wh surplus total
        # free_capacity = 2000 Wh
        # charge limit = 2000 / 8 = 250 Wh/slot → 250 W (60 min intervals)
        production = [5000] * 8 + [0] * 4
        consumption = [500] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=8000,
                                      free_capacity=2000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 250)

    def test_low_surplus_large_free_capacity(self):
        """Low PV surplus, large free capacity → no limit (-1)."""
        # 1000 W PV, 800 W consumption → 200 W surplus
        # 8 slots * 200 Wh = 1600 Wh surplus
        # free_capacity = 5000 Wh → surplus < free → no limit
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
        """PV surplus exactly matches free capacity → no limit (-1)."""
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
        """Battery full (free_capacity = 0) → charge limit = 0."""
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
        """Past target hour → no limit (-1)."""
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
        """1 slot remaining → rate for that single slot."""
        # Target is 14:00, current time is 13:00 → 1 slot
        # PV surplus: 5000 - 500 = 4500 W → 4500 Wh > free_cap 1000
        # limit = 1000 / 1 = 1000 Wh/slot → 1000 W
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
        # 3000 W PV, 2000 W consumption → 1000 W surplus
        # 8 slots * 1000 Wh = 8000 Wh surplus
        # free_capacity = 4000 Wh → surplus > free
        # limit = 4000 / 8 = 500 Wh/slot → 500 W
        production = [3000] * 8 + [0] * 4
        consumption = [2000] * 8 + [0] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=6000,
                                      free_capacity=4000)
        ts = datetime.datetime(2025, 6, 20, 6, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = self.logic._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 500)

    def test_15min_intervals(self):
        """Test with 15-minute intervals."""
        logic_15 = NextLogic(timezone=datetime.timezone.utc,
                             interval_minutes=15)
        logic_15.set_calculation_parameters(self.params)

        # Target 14:00, current 13:00 → 4 slots of 15 min
        # surplus = 4000 W per slot, interval_hours = 0.25
        # surplus Wh per slot = 4000 * 0.25 = 1000 Wh
        # total surplus = 4 * 1000 = 4000 Wh
        # free_capacity = 1000 Wh → surplus > free
        # wh_per_slot = 1000 / 4 = 250 Wh
        # charge_rate_w = 250 / 0.25 = 1000 W
        production = [4500] * 4
        consumption = [500] * 4
        calc_input = self._make_input(production, consumption,
                                      stored_energy=9000,
                                      free_capacity=1000)
        ts = datetime.datetime(2025, 6, 20, 13, 0, 0,
                               tzinfo=datetime.timezone.utc)
        limit = logic_15._calculate_peak_shaving_charge_limit(
            calc_input, ts)
        self.assertEqual(limit, 1000)


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
            peak_shaving_enabled=True,
            peak_shaving_allow_full_after=14,
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
        prices = np.zeros(len(production))
        return CalculationInput(
            production=np.array(production, dtype=float),
            consumption=np.array(consumption, dtype=float),
            prices=prices,
            stored_energy=stored_energy,
            stored_usable_energy=stored_energy - self.max_capacity * 0.05,
            free_capacity=free_capacity,
        )

    def test_nighttime_no_production(self):
        """No production (nighttime) → peak shaving skipped."""
        settings = self._make_settings()
        calc_input = self._make_input(
            [0, 0, 0, 0], [500, 500, 500, 500],
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 2, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_after_target_hour(self):
        """After target hour → no change."""
        settings = self._make_settings()
        calc_input = self._make_input(
            [5000, 5000], [500, 500],
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 15, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_force_charge_takes_priority(self):
        """Force charge (MODE -1) → peak shaving skipped."""
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
        """Battery in always_allow_discharge region → skip peak shaving."""
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
        """Before target hour, limit calculated → limit set."""
        settings = self._make_settings()
        # 6 slots (6..14), 5000W PV, 500W consumption → 4500W surplus
        # surplus Wh = 6 * 4500 = 27000 > free 3000
        # limit = 3000 / 6 = 500 W
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)
        self.assertTrue(result.allow_discharge)

    def test_existing_tighter_limit_kept(self):
        """Existing limit is tighter → keep existing."""
        settings = self._make_settings(limit_battery_charge_rate=200)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 200)

    def test_peak_shaving_limit_tighter(self):
        """Peak shaving limit is tighter than existing → peak shaving limit applied."""
        settings = self._make_settings(limit_battery_charge_rate=5000)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=7000, free_capacity=3000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, 500)

    def test_discharge_not_allowed_skips_peak_shaving(self):
        """Discharge not allowed (battery preserved for high-price hours) → skip."""
        settings = self._make_settings(allow_discharge=False)
        calc_input = self._make_input(
            [5000] * 8, [500] * 8,
            stored_energy=5000, free_capacity=5000)
        ts = datetime.datetime(2025, 6, 20, 8, 0, 0,
                               tzinfo=datetime.timezone.utc)
        result = self.logic._apply_peak_shaving(settings, calc_input, ts)
        self.assertEqual(result.limit_battery_charge_rate, -1)
        self.assertFalse(result.allow_discharge)


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
            peak_shaving_enabled=False,
            peak_shaving_allow_full_after=14,
        )
        self.logic.set_calculation_parameters(self.params)

    def test_disabled_no_limit(self):
        """peak_shaving_enabled=False → no change to settings."""
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
        """type: default → DefaultLogic."""
        config = {'battery_control': {'type': 'default'}}
        logic = Logic.create_logic(config, datetime.timezone.utc)
        self.assertIsInstance(logic, DefaultLogic)

    def test_next_type(self):
        """type: next → NextLogic."""
        config = {'battery_control': {'type': 'next'}}
        logic = Logic.create_logic(config, datetime.timezone.utc)
        self.assertIsInstance(logic, NextLogic)

    def test_missing_type_defaults_to_default(self):
        """No type key → DefaultLogic."""
        config = {}
        logic = Logic.create_logic(config, datetime.timezone.utc)
        self.assertIsInstance(logic, DefaultLogic)

    def test_unknown_type_raises(self):
        """Unknown type → RuntimeError."""
        config = {'battery_control': {'type': 'unknown'}}
        with self.assertRaises(RuntimeError):
            Logic.create_logic(config, datetime.timezone.utc)

    def test_expert_tuning_applied_to_next(self):
        """Expert tuning attributes applied to NextLogic."""
        config = {
            'battery_control': {'type': 'next'},
            'battery_control_expert': {
                'round_price_digits': 2,
                'charge_rate_multiplier': 1.5,
            },
        }
        logic = Logic.create_logic(config, datetime.timezone.utc)
        self.assertIsInstance(logic, NextLogic)
        self.assertEqual(logic.round_price_digits, 2)


class TestCalculationParametersPeakShaving(unittest.TestCase):
    """Test CalculationParameters peak shaving fields."""

    def test_defaults(self):
        """Without peak shaving args → defaults."""
        params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.1,
            max_capacity=10000,
        )
        self.assertFalse(params.peak_shaving_enabled)
        self.assertEqual(params.peak_shaving_allow_full_after, 14)

    def test_explicit_values(self):
        """With explicit peak shaving args → stored."""
        params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.1,
            max_capacity=10000,
            peak_shaving_enabled=True,
            peak_shaving_allow_full_after=16,
        )
        self.assertTrue(params.peak_shaving_enabled)
        self.assertEqual(params.peak_shaving_allow_full_after, 16)

    def test_invalid_allow_full_after_too_high(self):
        """allow_full_battery_after > 23 raises ValueError."""
        with self.assertRaises(ValueError):
            CalculationParameters(
                max_charging_from_grid_limit=0.8,
                min_price_difference=0.05,
                min_price_difference_rel=0.1,
                max_capacity=10000,
                peak_shaving_allow_full_after=25,
            )

    def test_invalid_allow_full_after_negative(self):
        """allow_full_battery_after < 0 raises ValueError."""
        with self.assertRaises(ValueError):
            CalculationParameters(
                max_charging_from_grid_limit=0.8,
                min_price_difference=0.05,
                min_price_difference_rel=0.1,
                max_capacity=10000,
                peak_shaving_allow_full_after=-1,
            )


if __name__ == '__main__':
    unittest.main()
