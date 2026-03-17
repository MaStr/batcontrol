import logging
import unittest

from batcontrol.logic.common import CommonLogic, MIN_CHARGE_RATE

logging.basicConfig(level=logging.DEBUG)

class TestCommonLogic(unittest.TestCase):
    """Test suite for CommonLogic class"""

    def setUp(self):
        """Set up test fixtures"""
        # Reset the singleton instance before each test
        CommonLogic._instance = None
        self.logic = CommonLogic.get_instance(
            charge_rate_multiplier=1.1,
            always_allow_discharge_limit=0.9,
            max_capacity=10000
        )

    def test_singleton_pattern(self):
        """Test that CommonLogic implements the singleton pattern correctly"""
        # Get another instance
        another_instance = CommonLogic.get_instance()

        # Both instances should be the same object
        self.assertIs(self.logic, another_instance)

        # Trying to create an instance directly should raise RuntimeError
        with self.assertRaises(RuntimeError):
            CommonLogic()

    def test_set_charge_rate_multiplier(self):
        """Test setting the charge rate multiplier"""
        new_multiplier = 1.5
        self.logic.set_charge_rate_multiplier(new_multiplier)
        self.assertEqual(self.logic.charge_rate_multiplier, new_multiplier)

    def test_set_always_allowed_discharge_limit(self):
        """Test setting the always allowed discharge limit"""
        new_limit = 0.85
        self.logic.set_always_allow_discharge_limit(new_limit)
        self.assertEqual(self.logic.always_allow_discharge_limit, new_limit)
        self.assertEqual(self.logic.get_always_allow_discharge_limit(), new_limit)

    def test_is_discharge_always_allowed_soc(self):
        """Test discharge always allowed when SOC is above threshold"""
        # SOC above the threshold (90%)
        self.assertTrue(self.logic.is_discharge_always_allowed_soc(95))  # 95% > 90%

        # SOC at the threshold (90%)
        self.assertTrue(self.logic.is_discharge_always_allowed_soc(90))  # 90% = 90%

        # SOC below the threshold (90%)
        self.assertFalse(self.logic.is_discharge_always_allowed_soc(85))  # 85% < 90%

    def test_is_discharge_always_allowed_capacity(self):
        """Test discharge always allowed when capacity is above threshold"""
        # Capacity above the threshold (9000 Wh)
        self.assertTrue(self.logic.is_discharge_always_allowed_capacity(9500))  # 9500 Wh > 9000 Wh

        # Capacity at the threshold (9000 Wh)
        self.assertTrue(self.logic.is_discharge_always_allowed_capacity(9000))  # 9000 Wh = 9000 Wh

        # Capacity below the threshold (9000 Wh)
        self.assertFalse(self.logic.is_discharge_always_allowed_capacity(8500))  # 8500 Wh < 9000 Wh

    def test_calculate_charge_rate(self):
        """Test charge rate calculation"""
        # Normal case: charge rate multiplied by the multiplier
        input_charge_rate = 1000
        expected_charge_rate = input_charge_rate * 1.1
        self.assertEqual(self.logic.calculate_charge_rate(input_charge_rate), expected_charge_rate)

        # Case where calculated charge rate is below MIN_CHARGE_RATE
        low_input = 400  # 400 * 1.1 = 440, which is < MIN_CHARGE_RATE (500)
        self.assertEqual(self.logic.calculate_charge_rate(low_input), MIN_CHARGE_RATE)

        # Case with very high charge rate
        high_input = 5000
        expected_high_rate = high_input * 1.1
        self.assertEqual(self.logic.calculate_charge_rate(high_input), expected_high_rate)

    def test_custom_initialization(self):
        """Test initialization with custom values"""
        # Reset the singleton instance
        CommonLogic._instance = None

        # Create with custom values
        custom_logic = CommonLogic.get_instance(
            charge_rate_multiplier=1.2,
            always_allow_discharge_limit=0.8,
            max_capacity=12000
        )

        # Check the values were set correctly
        self.assertEqual(custom_logic.charge_rate_multiplier, 1.2)
        self.assertEqual(custom_logic.always_allow_discharge_limit, 0.8)
        self.assertEqual(custom_logic.max_capacity, 12000)
