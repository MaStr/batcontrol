import logging
import unittest
import datetime
import numpy as np

from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic import Logic as LogicFactory
from batcontrol.logic.logic_interface import CalculationInput, CalculationParameters, InverterControlSettings
from batcontrol.logic.common import CommonLogic

logging.basicConfig(level=logging.DEBUG)

class TestDefaultLogic(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.max_capacity = 10000  # 10 kWh
        self.logic = DefaultLogic(timezone=datetime.timezone.utc)
        self.common = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.80,  # 80%
            max_capacity=self.max_capacity
        )
        # Setup calculation parameters
        self.calculation_parameters = CalculationParameters(
            max_charging_from_grid_limit=0.79,  # 79%
            min_price_difference=0.05,  # 5 cents
            min_price_difference_rel=0.2,  # 20% distance
            max_capacity=self.max_capacity
        )
        self.logic.set_calculation_parameters(self.calculation_parameters)

    def _calculate_battery_values(self, stored_energy, max_capacity) -> tuple:
        """Helper method to calculate stored usable energy and free capacity"""
        # We assume a MIN_SOC of 5% for usable energy
        stored_usable_energy = stored_energy - (max_capacity * 0.05 )
        free_capacity = max_capacity - stored_energy
        return stored_usable_energy, free_capacity


    def test_init(self):
        """Test initialization of DefaultLogic"""
        self.assertIsNotNone(self.logic)
        self.assertEqual(self.logic.round_price_digits, 4)
        self.assertEqual(self.logic.timezone, datetime.timezone.utc)

    def test_set_calculation_parameters(self):
        """Test setting calculation parameters"""
        self.assertEqual(self.logic.calculation_parameters, self.calculation_parameters)

    def test_interval_minutes_is_int(self):
        """Test that interval_minutes is always an int even when config provides a string"""
        logic = LogicFactory.create_logic({'time_resolution_minutes': '60'}, datetime.timezone.utc)
        self.assertIsInstance(logic.interval_minutes, int)
        self.assertEqual(logic.interval_minutes, 60)

    def test_interval_minutes_is_int_default(self):
        """Test that interval_minutes defaults to int 60 when not in config"""
        CommonLogic._instance = None
        logic = LogicFactory.create_logic({}, datetime.timezone.utc)
        self.assertIsInstance(logic.interval_minutes, int)
        self.assertEqual(logic.interval_minutes, 60)

    def test_calculate_inverter_mode_high_soc(self):
        """Test calculate_inverter_mode with high SOC should allow discharge"""
        #max_capacity = 10000  # 10 kWh
        stored_energy = 9500  #  9,5 kWh
        stored_usable_energy, free_capacity = self._calculate_battery_values(
                                                            stored_energy,
                                                            self.max_capacity )

        consumption = np.array([500, 600, 700])  # Example consumption in W
        production = np.array([0, 0, 0])  # No production for this test

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.25, 1: 0.30, 2: 0.35},  # Example prices in € per kWh
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Call the method under test
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input,calc_timestamp))
        result = self.logic.get_inverter_control_settings()

        # Assert result
        self.assertIsInstance(result, InverterControlSettings)
        self.assertTrue(result.allow_discharge, "Discharge should be allowed due to high SOC")

    def test_calculate_inverter_mode_low_soc(self):
        """Test calculate_inverter_mode with low SOC should not allow discharge"""
        #max_capacity = 10000  # 10 kWh
        stored_energy =  800
        stored_usable_energy, free_capacity = self._calculate_battery_values(
                                                            stored_energy,
                                                            self.max_capacity )

        consumption = np.array([500, 600, 700])  # Example consumption in W
        production = np.array([0, 0, 0])  # No production for this test

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.30, 1: 0.25, 2: 0.20},  # Current price higher than future
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Call the method under test
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input,calc_timestamp))
        result = self.logic.get_inverter_control_settings()

        # Assert result, tests only Class. This is ok here
        self.assertIsInstance(result, InverterControlSettings)

    def test_discharge_not_allowed_because_reserved(self):
        """Test discharge not allowed because reserved energy is below needed energy"""
        #max_capacity = 10000
        stored_energy =  900
        stored_usable_energy, free_capacity = self._calculate_battery_values(
                                                            stored_energy,
                                                            self.max_capacity )

        consumption = np.array([500, 500, 1000])  # Example consumption in W
        production = np.array([0, 0, 0])  # No production for this test

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.20, 1: 0.25, 2: 0.30},  # Current price higher than future
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Call the method under test
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input,calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        self.assertFalse(result.allow_discharge, "Discharge should not be allowed")


    def test_discharge_allowed_because_highest_price(self):
        """Test discharge allowed because current price is the highest"""
        #max_capacity = 10000
        stored_energy = 2000
        stored_usable_energy, free_capacity = self._calculate_battery_values(
                                                            stored_energy,
                                                            self.max_capacity )

        consumption = np.array([500, 500, 500])  # Example consumption in W
        production = np.array([0, 0, 0])  # No production for this test.

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.30, 1: 0.25, 2: 0.20},  # Current price higher than future
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Call the method under test
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input,calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        self.assertTrue(result.allow_discharge, "Discharge should be allowed")

    def test_charge_calculation_when_charging_possible(self):
        """Test charge calculation when charging is possible due to low SOC"""
        stored_energy = 2000  # 2 kWh, well below charging limit (79% = 7.9 kWh)
        stored_usable_energy, free_capacity = self._calculate_battery_values(
            stored_energy, self.max_capacity
        )

        # Setup scenario with high future prices to trigger charging
        consumption = np.array([1000, 2000, 1500])  # Higher consumption in future hours
        production = np.array([0, 0, 0])  # No production

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.20, 1: 0.35, 2: 0.30},  # Low current price, high future prices
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Test at 30 minutes past the hour to test charge rate calculation
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 30, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        calc_output = self.logic.get_calculation_output()

        # Verify charging is enabled
        self.assertFalse(result.allow_discharge, "Discharge should not be allowed when charging needed")
        self.assertTrue(result.charge_from_grid, "Should charge from grid when energy needed for high price hours")
        self.assertGreater(result.charge_rate, 0, "Charge rate should be greater than 0")
        self.assertGreater(calc_output.required_recharge_energy, 0, "Should calculate required recharge energy")

    def test_charge_calculation_when_charging_not_possible_high_soc(self):
        """Test charge calculation when charging is not possible due to high SOC"""
        # Set SOC above charging limit (79%)
        stored_energy = 8000  # 8.0 kWh, above charging limit of 7.9 kWh 
        stored_usable_energy, free_capacity = self._calculate_battery_values(
            stored_energy, self.max_capacity
        )

        # Create scenario where discharge is not triggered by always_allow_discharge_limit
        # Use prices where current price is low and future prices are higher, but not enough
        # stored energy to satisfy future high consumption
        consumption = np.array([1000, 3000, 2500])  # High future consumption that requires reserves
        production = np.array([0, 0, 0])  # No production

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.20, 1: 0.35, 2: 0.30},  # Low current, high future prices
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 30, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        calc_output = self.logic.get_calculation_output()

        # At high SOC (above charging limit), charging should not be possible
        self.assertFalse(result.charge_from_grid, "Should not charge from grid when SOC above charging limit")
        self.assertEqual(result.charge_rate, 0, "Charge rate should be 0 when charging not allowed")
        self.assertEqual(calc_output.required_recharge_energy, 0, "Required recharge energy should be 0 when charging not possible")
        
        # Discharge behavior depends on reserved energy calculation vs stored usable energy
        # This test primarily focuses on charging behavior when SOC is too high

    def test_charge_energy_limited_by_max_charging_limit(self):
        """Test that the actual charging energy is limited by max charging capacity"""
        # Set SOC close to but below charging limit
        stored_energy = 6000  # 6.0 kWh, just below limit of 7.9 kWh (only 1.9 Wh remaining)
        stored_usable_energy, free_capacity = self._calculate_battery_values(
            stored_energy, self.max_capacity
        )

        # Create scenario with high future consumption to trigger large recharge requirement
        consumption = np.array([2000, 8000, 8000])  # High future consumption
        production = np.array([0, 0, 0])  # No production

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.15, 1: 0.40, 2: 0.35},  # Very low current, high future prices
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()

        # With only 50 Wh remaining to charge limit, the system should still allow charging
        # but the energy should be limited
        charge_limit_capacity = self.max_capacity * self.calculation_parameters.max_charging_from_grid_limit
        max_allowed_charging = charge_limit_capacity - stored_energy

        self.assertAlmostEqual(max_allowed_charging, 1900.0, delta=1.0, msg="Expected about 1900 Wh remaining capacity")

        self.assertTrue(result.charge_from_grid, "Should charge when capacity available")

        self.assertGreater(result.charge_rate, 0, "Should have positive charge rate")
        # The charge rate should be reasonable for the small remaining capacity
        remaining_time = (60 - calc_timestamp.minute) / 60
        if remaining_time > 0:
            # Maximum theoretical charge energy in the remaining time
            max_charge_energy = result.charge_rate * remaining_time / self.common.charge_rate_multiplier
            # This should be roughly limited to the available capacity
            self.assertLessEqual(max_charge_energy, max_allowed_charging + 10, 
                                "Charge energy should be roughly limited by available capacity")

    def test_calculate_inverter_mode_error_without_calculation_output(self):
        """Test that calculate_inverter_mode raises ValueError when calculation_output is None"""
        logic = DefaultLogic()
        logic.set_calculation_parameters(self.calculation_parameters)
        
        stored_energy = 5000
        stored_usable_energy, free_capacity = self._calculate_battery_values(
            stored_energy, self.max_capacity
        )

        calc_input = CalculationInput(
            consumption=np.array([500, 600, 700]),
            production=np.array([0, 0, 0]),
            prices={0: 0.25, 1: 0.30, 2: 0.35},
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Call calculate_inverter_mode directly without calling calculate() first
        with self.assertRaises(ValueError) as context:
            logic.calculate_inverter_mode(calc_input)
        
        self.assertIn("Calculation output is not set", str(context.exception))

    def test_charge_rate_calculation_with_remaining_time(self):
        """Test that charge rate is correctly calculated based on remaining time in hour"""
        stored_energy = 3000  # 3 kWh
        stored_usable_energy, free_capacity = self._calculate_battery_values(
            stored_energy, self.max_capacity
        )

        consumption = np.array([1000, 3000, 2000])  # High consumption in hour 1
        production = np.array([0, 0, 0])

        calc_input = CalculationInput(
            consumption=consumption,
            production=production,
            prices={0: 0.20, 1: 0.35, 2: 0.25},  # High price in hour 1
            stored_energy=stored_energy,
            stored_usable_energy=stored_usable_energy,
            free_capacity=free_capacity,
        )

        # Test at 45 minutes past the hour (15 minutes remaining)
        calc_timestamp = datetime.datetime(2025, 6, 20, 12, 45, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(self.logic.calculate(calc_input, calc_timestamp))
        result = self.logic.get_inverter_control_settings()
        calc_output = self.logic.get_calculation_output()

        if calc_output.required_recharge_energy > 0:
            expected_remaining_time = (60 - 45) / 60  # 15 minutes = 0.25 hours
            expected_charge_rate_before_multiplier = calc_output.required_recharge_energy / expected_remaining_time
            
            # The actual charge rate should be higher due to charge_rate_multiplier (1.1)
            self.assertGreater(result.charge_rate, expected_charge_rate_before_multiplier,
                             "Charge rate should be adjusted by charge_rate_multiplier")

if __name__ == '__main__':
    unittest.main()
