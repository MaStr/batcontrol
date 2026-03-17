"""Tests for core batcontrol functionality including MODE_LIMIT_BATTERY_CHARGE_RATE"""
import pytest
from unittest.mock import MagicMock, patch

from batcontrol.core import (
    Batcontrol,
    MODE_LIMIT_BATTERY_CHARGE_RATE,
)


class TestModeLimitBatteryChargeRate:
    """Test MODE_LIMIT_BATTERY_CHARGE_RATE (mode 8) functionality"""

    @pytest.fixture
    def mock_config(self):
        """Provide a minimal config for testing"""
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {
                'type': 'tibber',
                'token': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'mqtt': {
                'enabled': False
            }
        }

    def test_mode_constant_exists(self):
        """Test that MODE_LIMIT_BATTERY_CHARGE_RATE constant is defined"""
        assert MODE_LIMIT_BATTERY_CHARGE_RATE == 8

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_limit_battery_charge_rate_method(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test limit_battery_charge_rate method applies correct limits"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Test setting limit within bounds
        bc.limit_battery_charge_rate(2000)

        # Verify inverter method was called with correct value
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(2000)
        assert bc.last_mode == MODE_LIMIT_BATTERY_CHARGE_RATE

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_limit_battery_charge_rate_capped_by_max(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that limit is capped by max_pv_charge_rate"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Try to set limit above max_pv_charge_rate
        bc.limit_battery_charge_rate(5000)

        # Verify it was capped to max_pv_charge_rate
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(3000)

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_limit_battery_charge_rate_floored_by_min(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that limit is floored by min_pv_charge_rate"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Try to set limit below min_pv_charge_rate
        bc.limit_battery_charge_rate(50)

        # Verify it was floored to min_pv_charge_rate
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(100)

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_limit_battery_charge_rate_zero_allowed(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that limit=0 blocks charging when min=0"""
        # Modify config to allow zero charging
        mock_config['inverter']['min_pv_charge_rate'] = 0

        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Set limit to 0
        bc.limit_battery_charge_rate(0)

        # Verify it was set to 0 (charging blocked)
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(0)

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_limit_battery_charge_rate_min_exceeds_max(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that when min_pv_charge_rate > max_pv_charge_rate, min is clamped to max at init"""
        mock_config['inverter']['min_pv_charge_rate'] = 4000

        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance — misconfiguration is corrected at init
        bc = Batcontrol(mock_config)

        # min_pv_charge_rate should have been clamped to max_pv_charge_rate at init
        assert bc.min_pv_charge_rate == 3000

        # Set any positive limit - should be clamped to max_pv_charge_rate (3000)
        bc.limit_battery_charge_rate(1000)

        # Verify effective limit does not exceed max_pv_charge_rate
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(3000)

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_api_set_mode_accepts_mode_8(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that api_set_mode accepts MODE_LIMIT_BATTERY_CHARGE_RATE"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Set a valid limit first (otherwise default -1 will fall back to mode 10)
        bc._limit_battery_charge_rate = 2000

        # Call api_set_mode with mode 8
        bc.api_set_mode(MODE_LIMIT_BATTERY_CHARGE_RATE)

        # Verify mode was set
        assert bc.last_mode == MODE_LIMIT_BATTERY_CHARGE_RATE
        assert bc.api_overwrite is True

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_api_set_limit_battery_charge_rate(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test api_set_limit_battery_charge_rate updates the dynamic value"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Call api_set_limit_battery_charge_rate
        bc.api_set_limit_battery_charge_rate(2500)

        # Verify the value was stored
        assert bc._limit_battery_charge_rate == 2500

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_api_set_limit_applies_immediately_in_mode_8(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """Test that changing limit applies immediately when in mode 8"""
        # Setup mocks
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        # Create Batcontrol instance
        bc = Batcontrol(mock_config)

        # Set mode to 8 first
        bc.limit_battery_charge_rate(1000)
        mock_inverter.set_mode_limit_battery_charge.reset_mock()

        # Now change the limit
        bc.api_set_limit_battery_charge_rate(2000)

        # Verify the new limit was applied immediately
        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(2000)


class TestAlwaysAllowDischargeLimitConversion:
    """Tests that always_allow_discharge_limit is always a float in core.py"""

    @pytest.fixture
    def mock_inverter(self):
        """Create a mock inverter"""
        inv = MagicMock()
        inv.max_pv_charge_rate = 3000
        inv.set_mode_limit_battery_charge = MagicMock()
        inv.get_max_capacity = MagicMock(return_value=10000)
        return inv

    def _make_batcontrol(self, mock_inverter, always_allow_discharge_limit):
        """Helper to create a Batcontrol instance with a given discharge limit config"""
        config = {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {'type': 'tibber', 'token': 'test_token'},
            'pvinstallations': [],
            'consumption_forecast': {'type': 'simple', 'value': 500},
            'battery_control': {
                'max_charging_from_grid_limit': 0.5,
                'min_price_difference': 0.05,
                'always_allow_discharge_limit': always_allow_discharge_limit,
            },
            'mqtt': {'enabled': False}
        }
        with patch('batcontrol.core.tariff_factory.create_tarif_provider'), \
             patch('batcontrol.core.inverter_factory.create_inverter',
                   return_value=mock_inverter), \
             patch('batcontrol.core.solar_factory.create_solar_provider'), \
             patch('batcontrol.core.consumption_factory.create_consumption'):
            from batcontrol.logic.common import CommonLogic
            CommonLogic._instance = None
            return Batcontrol(config)

    def test_config_string_dot_notation(self, mock_inverter):
        """Config value '0.9' (string) is converted to float 0.9"""
        bc = self._make_batcontrol(mock_inverter, '0.9')
        result = bc.get_always_allow_discharge_limit()
        assert isinstance(result, float)
        assert abs(result - 0.9) < 1e-9

    def test_config_european_comma_notation(self, mock_inverter):
        """Config value '0,9' (European decimal) is converted to float 0.9"""
        bc = self._make_batcontrol(mock_inverter, '0,9')
        result = bc.get_always_allow_discharge_limit()
        assert isinstance(result, float)
        assert abs(result - 0.9) < 1e-9

    def test_setter_string_dot_notation(self, mock_inverter):
        """Setter with string '0.85' is converted to float"""
        bc = self._make_batcontrol(mock_inverter, 0.9)
        bc.set_always_allow_discharge_limit('0.85')
        result = bc.get_always_allow_discharge_limit()
        assert isinstance(result, float)
        assert abs(result - 0.85) < 1e-9

    def test_setter_european_comma_notation(self, mock_inverter):
        """Setter with European '0,85' is converted to float"""
        bc = self._make_batcontrol(mock_inverter, 0.9)
        bc.set_always_allow_discharge_limit('0,85')
        result = bc.get_always_allow_discharge_limit()
        assert isinstance(result, float)
        assert abs(result - 0.85) < 1e-9

    def test_getter_always_returns_float(self, mock_inverter):
        """get_always_allow_discharge_limit always returns a float"""
        bc = self._make_batcontrol(mock_inverter, 0.9)
        result = bc.get_always_allow_discharge_limit()
        assert isinstance(result, float)


class TestNumericConfigStringCoercion:
    """Tests that all numeric battery_control config values are coerced to float,
    covering HA addon configs where every value arrives as a string."""

    @pytest.fixture
    def mock_inverter(self):
        """Create a mock inverter"""
        inv = MagicMock()
        inv.max_pv_charge_rate = 3000
        inv.set_mode_limit_battery_charge = MagicMock()
        inv.get_max_capacity = MagicMock(return_value=10000)
        return inv

    def _make_batcontrol(self, mock_inverter, battery_control_overrides):
        """Helper to create a Batcontrol instance with customised battery_control config"""
        battery_control = {
            'max_charging_from_grid_limit': 0.5,
            'min_price_difference': 0.05,
            'min_price_difference_rel': 0,
            'always_allow_discharge_limit': 0.9,
            'charge_rate_multiplier': 1.1,
            'min_recharge_amount': 100.0,
        }
        battery_control.update(battery_control_overrides)
        config = {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {'type': 'tibber', 'token': 'test_token'},
            'pvinstallations': [],
            'consumption_forecast': {'type': 'simple', 'value': 500},
            'battery_control': battery_control,
            'mqtt': {'enabled': False}
        }
        with patch('batcontrol.core.tariff_factory.create_tarif_provider'), \
             patch('batcontrol.core.inverter_factory.create_inverter',
                   return_value=mock_inverter), \
             patch('batcontrol.core.solar_factory.create_solar_provider'), \
             patch('batcontrol.core.consumption_factory.create_consumption'):
            from batcontrol.logic.common import CommonLogic
            CommonLogic._instance = None
            return Batcontrol(config)

    def test_max_charging_from_grid_limit_string(self, mock_inverter):
        """'0.5' string is coerced to float 0.5"""
        bc = self._make_batcontrol(mock_inverter, {'max_charging_from_grid_limit': '0.5'})
        assert isinstance(bc.max_charging_from_grid_limit, float)
        assert abs(bc.max_charging_from_grid_limit - 0.5) < 1e-9

    def test_max_charging_from_grid_limit_european(self, mock_inverter):
        """'0,5' European notation is coerced to float 0.5"""
        bc = self._make_batcontrol(mock_inverter, {'max_charging_from_grid_limit': '0,5'})
        assert isinstance(bc.max_charging_from_grid_limit, float)
        assert abs(bc.max_charging_from_grid_limit - 0.5) < 1e-9

    def test_min_price_difference_string(self, mock_inverter):
        """'0.05' string is coerced to float"""
        bc = self._make_batcontrol(mock_inverter, {'min_price_difference': '0.05'})
        assert isinstance(bc.min_price_difference, float)
        assert abs(bc.min_price_difference - 0.05) < 1e-9

    def test_min_price_difference_rel_string(self, mock_inverter):
        """'0' string is coerced to float"""
        bc = self._make_batcontrol(mock_inverter, {'min_price_difference_rel': '0'})
        assert isinstance(bc.min_price_difference_rel, float)
        assert abs(bc.min_price_difference_rel - 0.0) < 1e-9

    def test_charge_rate_multiplier_string(self, mock_inverter):
        """'1.1' string is coerced to float"""
        from batcontrol.logic.common import CommonLogic
        self._make_batcontrol(mock_inverter, {'charge_rate_multiplier': '1.1'})
        assert isinstance(CommonLogic.get_instance().charge_rate_multiplier, float)
        assert abs(CommonLogic.get_instance().charge_rate_multiplier - 1.1) < 1e-9

    def test_min_recharge_amount_string(self, mock_inverter):
        """'100' string is coerced to float"""
        from batcontrol.logic.common import CommonLogic
        self._make_batcontrol(mock_inverter, {'min_recharge_amount': '100'})
        assert isinstance(CommonLogic.get_instance().min_charge_energy, float)
        assert abs(CommonLogic.get_instance().min_charge_energy - 100.0) < 1e-9

    def test_all_string_values_from_ha_addon(self, mock_inverter):
        """All numeric battery_control values as strings (simulates HA addon config)"""
        from batcontrol.logic.common import CommonLogic
        bc = self._make_batcontrol(mock_inverter, {
            'max_charging_from_grid_limit': '0,5',
            'min_price_difference': '0,05',
            'min_price_difference_rel': '0',
            'always_allow_discharge_limit': '0,9',
            'charge_rate_multiplier': '1,1',
            'min_recharge_amount': '100',
        })
        assert isinstance(bc.max_charging_from_grid_limit, float)
        assert isinstance(bc.min_price_difference, float)
        assert isinstance(bc.min_price_difference_rel, float)
        assert isinstance(bc.get_always_allow_discharge_limit(), float)
        assert isinstance(CommonLogic.get_instance().charge_rate_multiplier, float)
        assert isinstance(CommonLogic.get_instance().min_charge_energy, float)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
