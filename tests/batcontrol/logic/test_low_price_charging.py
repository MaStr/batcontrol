"""Tests for the NextLogic low-price charging lock feature.

Covers:
- _apply_low_price_charging_lock decision logic
- Override of always_allow_discharge_limit when current price <= threshold
- Force-charge from grid at the absolute minimum-price slot
- Wait (block discharge + grid charging) when a cheaper slot is still ahead
- No effect when current price is above threshold
- Disabled by default
"""
import logging
import unittest
import datetime
import numpy as np

from batcontrol.logic.next import NextLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
    InverterControlSettings,
)
from batcontrol.logic.common import CommonLogic

logging.basicConfig(level=logging.DEBUG)


class TestLowPriceChargingLockDecision(unittest.TestCase):
    """Direct tests for _apply_low_price_charging_lock."""

    def setUp(self):
        self.max_capacity = 10000  # 10 kWh
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)
        # Reset singleton so always_allow_discharge_limit is consistent
        # across test classes that share the CommonLogic instance.
        CommonLogic._instance = None
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
            low_price_charging_enabled=True,
            low_price_charging_threshold=-0.10,
            low_price_charging_force_charge=True,
            max_grid_charge_rate=5000,
        )
        self.logic.set_calculation_parameters(self.params)

    def _make_input(self, prices, free_capacity=5000, stored_energy=5000):
        n = len(prices)
        return CalculationInput(
            production=np.zeros(n, dtype=float),
            consumption=np.zeros(n, dtype=float),
            prices=np.array(prices, dtype=float),
            stored_energy=stored_energy,
            stored_usable_energy=stored_energy,
            free_capacity=free_capacity,
        )

    def _make_settings(self, allow_discharge=True, charge_from_grid=False,
                       charge_rate=0, limit_battery_charge_rate=-1):
        return InverterControlSettings(
            allow_discharge=allow_discharge,
            charge_from_grid=charge_from_grid,
            charge_rate=charge_rate,
            limit_battery_charge_rate=limit_battery_charge_rate,
        )

    def test_above_threshold_no_change(self):
        """Current price above threshold -> settings untouched."""
        settings = self._make_settings(allow_discharge=True)
        calc_input = self._make_input(prices=[0.05, -0.20, 0.10, 0.30])
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertTrue(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 0)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_at_min_slot_force_charges_from_grid(self):
        """Current slot is the cheapest -> force charge at max grid rate."""
        settings = self._make_settings(allow_discharge=True)
        # current is the min and well below threshold
        calc_input = self._make_input(
            prices=[-0.20, -0.15, -0.05, 0.10, 0.30],
            free_capacity=4000,
        )
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertFalse(result.allow_discharge)
        self.assertTrue(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 5000)  # max_grid_charge_rate
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_below_threshold_but_min_ahead_waits(self):
        """Current is low but a cheaper slot is ahead -> wait (no charging)."""
        settings = self._make_settings(allow_discharge=True)
        # current = -0.12 (<= threshold), min later at slot 2 (-0.20)
        calc_input = self._make_input(prices=[-0.12, -0.11, -0.20, 0.10])
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertFalse(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 0)
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_overrides_always_allow_discharge(self):
        """High SOC must NOT bypass the lock - explicit user requirement."""
        # Pre-set settings as if always_allow_discharge had granted discharge
        settings = self._make_settings(allow_discharge=True,
                                       limit_battery_charge_rate=-1)
        calc_input = self._make_input(
            prices=[-0.15, -0.05, 0.10],
            free_capacity=200,
            stored_energy=9800,  # well above 90% always-allow threshold
        )
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertFalse(result.allow_discharge)

    def test_at_min_slot_battery_full_blocks_discharge_only(self):
        """Min slot but no free capacity -> block discharge, no force charge."""
        settings = self._make_settings(allow_discharge=True,
                                       charge_from_grid=False)
        calc_input = self._make_input(
            prices=[-0.20, -0.10, 0.10],
            free_capacity=0,
            stored_energy=10000,
        )
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertFalse(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 0)

    def test_at_min_slot_force_charge_disabled(self):
        """force_charge_at_min=False -> only block discharge at min slot."""
        self.params.low_price_charging_force_charge = False
        self.logic.set_calculation_parameters(self.params)
        settings = self._make_settings(allow_discharge=True)
        calc_input = self._make_input(
            prices=[-0.20, -0.05, 0.10],
            free_capacity=4000,
        )
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertFalse(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 0)

    def test_max_grid_charge_rate_zero_uses_fallback(self):
        """max_grid_charge_rate=0 -> use large fallback (capped in core.py)."""
        self.params.max_grid_charge_rate = 0
        self.logic.set_calculation_parameters(self.params)
        settings = self._make_settings(allow_discharge=True)
        calc_input = self._make_input(
            prices=[-0.20, -0.05, 0.10],
            free_capacity=4000,
        )
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        self.assertTrue(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 999999)

    def test_pv_charging_not_blocked(self):
        """PV charging must remain enabled (limit_battery_charge_rate=-1)."""
        settings = self._make_settings(allow_discharge=True,
                                       limit_battery_charge_rate=200)
        calc_input = self._make_input(prices=[-0.20, -0.05])
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        # Lock clears any prior PV limit so PV freely charges into battery
        self.assertEqual(result.limit_battery_charge_rate, -1)

    def test_disabled_no_change(self):
        """low_price_charging_enabled=False -> settings untouched."""
        self.params.low_price_charging_enabled = False
        self.logic.set_calculation_parameters(self.params)
        settings = self._make_settings(allow_discharge=True)
        calc_input = self._make_input(prices=[-0.20, -0.05])
        # Note: we still call the method directly to confirm it would change
        # things if enabled. The enable gate sits in calculate_inverter_mode.
        # Here we test the gate via integration (next test).
        result = self.logic._apply_low_price_charging_lock(
            settings, calc_input)
        # Direct call ignores the enabled flag (gating happens upstream),
        # so this still locks. The integration test below covers the gate.
        self.assertFalse(result.allow_discharge)


class TestLowPriceChargingLockIntegration(unittest.TestCase):
    """Integration tests via NextLogic.calculate_inverter_mode."""

    def setUp(self):
        self.max_capacity = 10000
        CommonLogic._instance = None
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.0,
            always_allow_discharge_limit=0.90,
            max_capacity=self.max_capacity,
        )
        self.logic = NextLogic(timezone=datetime.timezone.utc,
                               interval_minutes=60)

    def _params(self, lpc_enabled=True, threshold=-0.10):
        return CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity,
            low_price_charging_enabled=lpc_enabled,
            low_price_charging_threshold=threshold,
            low_price_charging_force_charge=True,
            max_grid_charge_rate=5000,
        )

    def _calc_input(self, prices, free_capacity=4000, stored_energy=6000):
        n = len(prices)
        return CalculationInput(
            production=np.zeros(n, dtype=float),
            consumption=np.ones(n, dtype=float) * 500,
            prices=np.array(prices, dtype=float),
            stored_energy=stored_energy,
            stored_usable_energy=stored_energy - self.max_capacity * 0.05,
            free_capacity=free_capacity,
        )

    def test_lock_at_min_slot_overrides_always_allow_discharge(self):
        """High SOC + at min slot -> force charge, NOT allow discharge."""
        self.logic.set_calculation_parameters(self._params())
        # stored_energy=9800 -> well above 90% always-allow threshold
        calc_input = self._calc_input(
            prices=[-0.20, -0.05, 0.10, 0.30],
            free_capacity=200,
            stored_energy=9800,
        )
        ts = datetime.datetime(2025, 6, 20, 12, 0, 0,
                               tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, ts))
        result = self.logic.get_inverter_control_settings()
        self.assertFalse(result.allow_discharge)
        self.assertTrue(result.charge_from_grid)
        self.assertEqual(result.charge_rate, 5000)

    def test_lock_disabled_falls_through_to_default(self):
        """Disabled lock -> always_allow_discharge_limit applies normally."""
        self.logic.set_calculation_parameters(self._params(lpc_enabled=False))
        calc_input = self._calc_input(
            prices=[-0.20, -0.05, 0.10, 0.30],
            free_capacity=200,
            stored_energy=9800,
        )
        ts = datetime.datetime(2025, 6, 20, 12, 0, 0,
                               tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, ts))
        result = self.logic.get_inverter_control_settings()
        # Without the lock, high SOC -> discharge allowed
        self.assertTrue(result.allow_discharge)
        self.assertFalse(result.charge_from_grid)


if __name__ == '__main__':
    unittest.main()
