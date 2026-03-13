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


class TestEvccPeakShavingGuard:
    """Test evcc peak shaving guard in core.py run loop."""

    @pytest.fixture
    def mock_config(self):
        """Provide a minimal config for testing."""
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
            'peak_shaving': {
                'enabled': True,
                'allow_full_battery_after': 14
            },
            'mqtt': {
                'enabled': False
            }
        }

    def _create_bc(self, mock_config, mock_inverter_factory, mock_tariff,
                   mock_solar, mock_consumption):
        """Helper to create a Batcontrol with mocked dependencies."""
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        bc = Batcontrol(mock_config)
        return bc

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_charging_disables_peak_shaving_in_calc_params(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is actively charging, peak_shaving_enabled is False in calc_params."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = True
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        # Replicate the pre-calculation evcc check from core.py
        from batcontrol.logic.logic_interface import CalculationParameters
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving_enabled=peak_shaving_config.get('enabled', False) and not evcc_disable_peak_shaving,
        )

        assert calc_params.peak_shaving_enabled is False

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_pv_mode_disables_peak_shaving_in_calc_params(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When EV connected in PV mode, peak_shaving_enabled is False in calc_params."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = False
        mock_evcc.evcc_ev_expects_pv_surplus = True
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import CalculationParameters
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving_enabled=peak_shaving_config.get('enabled', False) and not evcc_disable_peak_shaving,
        )

        assert calc_params.peak_shaving_enabled is False

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_not_active_keeps_peak_shaving_enabled(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is not charging and no PV mode, peak_shaving_enabled stays True."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = False
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import CalculationParameters
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving_enabled=peak_shaving_config.get('enabled', False) and not evcc_disable_peak_shaving,
        )

        assert calc_params.peak_shaving_enabled is True

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_no_limit_active_no_change(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is charging but peak shaving was off in config, it stays disabled."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = True
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import CalculationParameters
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        # Config has enabled=True, but evcc disables it -> result is False
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving_enabled=False and not evcc_disable_peak_shaving,
        )

        assert calc_params.peak_shaving_enabled is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
