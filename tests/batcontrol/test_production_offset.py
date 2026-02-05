"""
Test suite for production offset (wintermode) functionality.

Tests the production offset feature that allows adjusting solar production
forecast by a percentage factor.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch

from batcontrol.core import Batcontrol
from batcontrol.logic.logic_interface import InverterControlSettings, CalculationOutput


class TestProductionOffset:
    """Test suite for production offset functionality"""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration for testing"""
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'battery_control': {
                'min_price_difference': 0.05,
                'min_price_difference_rel': 0.10,
                'always_allow_discharge_limit': 0.90,
                'max_charging_from_grid_limit': 0.89,
                'min_recharge_amount': 100,
            },
            'battery_control_expert': {
                'charge_rate_multiplier': 1.1,
                'round_price_digits': 4,
                'production_offset_percent': 0.8,  # 80% of production
            },
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 0,
            },
            'utility': {
                'type': 'awattar_de',
                'vat': 0.19,
                'fees': 0.015,
                'markup': 0.03,
            },
            'mqtt': {
                'enabled': False,
            },
            'pvinstallations': [
                {
                    'name': 'Test PV',
                    'lat': 48.0,
                    'lon': 8.0,
                    'declination': 30,
                    'azimuth': 0,
                    'kWp': 10.0,
                }
            ],
            'solar_forecast_provider': 'fcsolarapi',
            'consumption_forecast': {
                'type': 'csv',
                'csv': {
                    'annual_consumption': 4500,
                    'load_profile': 'load_profile.csv',
                }
            },
        }

    def test_production_offset_initialization_default(self, mock_config):
        """Test that production offset initializes with default value when not configured"""
        # Remove production_offset_percent from config
        del mock_config['battery_control_expert']['production_offset_percent']

        with patch('batcontrol.core.tariff_factory'), \
             patch('batcontrol.core.inverter_factory'), \
             patch('batcontrol.core.solar_factory'), \
             patch('batcontrol.core.consumption_factory'):

            batcontrol = Batcontrol(mock_config)

            # Should default to 1.0 (100%, no offset)
            assert batcontrol.production_offset_percent == 1.0

    def test_production_offset_initialization_from_config(self, mock_config):
        """Test that production offset is initialized from config"""
        with patch('batcontrol.core.tariff_factory'), \
             patch('batcontrol.core.inverter_factory'), \
             patch('batcontrol.core.solar_factory'), \
             patch('batcontrol.core.consumption_factory'):

            batcontrol = Batcontrol(mock_config)

            # Should load value from config
            assert batcontrol.production_offset_percent == 0.8

    def test_production_offset_applied_to_forecast(self, mock_config):
        """Test that production offset is applied to production forecast"""
        with patch('batcontrol.core.tariff_factory'), \
             patch('batcontrol.core.inverter_factory'), \
             patch('batcontrol.core.solar_factory'), \
             patch('batcontrol.core.consumption_factory'):

            batcontrol = Batcontrol(mock_config)
            batcontrol.production_offset_percent = 0.5  # 50% reduction

            # Create mock forecasts
            production_forecast = {0: 1000, 1: 2000, 2: 3000}  # W
            consumption_forecast = {0: 500, 1: 500, 2: 500}
            price_dict = {0: 0.20, 1: 0.25, 2: 0.30}

            # Mock the forecast methods
            batcontrol.dynamic_tariff = Mock()
            batcontrol.dynamic_tariff.get_prices = Mock(return_value=price_dict)

            batcontrol.fc_solar = Mock()
            batcontrol.fc_solar.get_forecast = Mock(return_value=production_forecast)

            batcontrol.fc_consumption = Mock()
            batcontrol.fc_consumption.get_forecast = Mock(return_value=consumption_forecast)

            batcontrol.inverter = Mock()
            batcontrol.inverter.get_SOC = Mock(return_value=50.0)
            batcontrol.inverter.get_stored_energy = Mock(return_value=5000)
            batcontrol.inverter.get_stored_usable_energy = Mock(return_value=4000)
            batcontrol.inverter.get_free_capacity = Mock(return_value=5000)
            batcontrol.inverter.get_max_capacity = Mock(return_value=10000)
            batcontrol.inverter.get_reserved_energy = Mock(return_value=1000)

            batcontrol.mqtt_api = None
            batcontrol.evcc_api = None

            # Mock LogicFactory to avoid complex logic
            with patch('batcontrol.core.LogicFactory') as mock_logic_factory:
                mock_logic = Mock()
                mock_logic.mode = 10
                mock_logic.charge_rate = 0

                # Create proper InverterControlSettings object
                inverter_settings = InverterControlSettings(
                    allow_discharge=True,
                    charge_from_grid=False,
                    charge_rate=0,
                    limit_battery_charge_rate=-1  # No limit
                )
                mock_logic.get_inverter_control_settings = Mock(return_value=inverter_settings)

                # Create proper CalculationOutput object
                calc_output = CalculationOutput(
                    reserved_energy=1000,
                    required_recharge_energy=0,
                    min_dynamic_price_difference=0.05
                )
                mock_logic.get_calculation_output = Mock(return_value=calc_output)
                mock_logic.calculate = Mock(return_value=True)
                mock_logic.set_calculation_parameters = Mock()

                mock_logic_factory.create_logic = Mock(return_value=mock_logic)

                # Call run to apply the offset
                batcontrol.run()

                # Check that production was offset correctly
                # Note: production[0] is adjusted for elapsed time in current interval
                # so we only check indices [1] and [2] for exact values

            batcontrol = Batcontrol(mock_config)
            original_value = batcontrol.production_offset_percent

            # Try to set invalid value
            batcontrol.api_set_production_offset(-0.5)

            # Should not be updated
            assert batcontrol.production_offset_percent == original_value

    def test_production_offset_api_set_invalid_too_high(self, mock_config):
        """Test setting production offset via API with invalid high value"""
        with patch('batcontrol.core.tariff_factory'), \
             patch('batcontrol.core.inverter_factory'), \
             patch('batcontrol.core.solar_factory'), \
             patch('batcontrol.core.consumption_factory'):

            batcontrol = Batcontrol(mock_config)
            original_value = batcontrol.production_offset_percent

            # Try to set invalid value (> 2.0)
            batcontrol.api_set_production_offset(2.5)

            # Should not be updated
            assert batcontrol.production_offset_percent == original_value

    def test_production_offset_api_set_boundary_values(self, mock_config):
        """Test setting production offset via API with boundary values"""
        with patch('batcontrol.core.tariff_factory'), \
             patch('batcontrol.core.inverter_factory'), \
             patch('batcontrol.core.solar_factory'), \
             patch('batcontrol.core.consumption_factory'):

            batcontrol = Batcontrol(mock_config)

            # Test minimum boundary (0.0)
            batcontrol.api_set_production_offset(0.0)
            assert batcontrol.production_offset_percent == 0.0

            # Test maximum boundary (2.0)
            batcontrol.api_set_production_offset(2.0)
            assert batcontrol.production_offset_percent == 2.0

            # Test normal value (1.0 = 100%)
            batcontrol.api_set_production_offset(1.0)
            assert batcontrol.production_offset_percent == 1.0


class TestProductionOffsetMqtt:
    """Test suite for production offset MQTT functionality"""

    def test_mqtt_publish_production_offset(self):
        """Test that production offset is published via MQTT"""
        from batcontrol.mqtt_api import MqttApi

        mock_config = {
            'broker': 'localhost',
            'port': 1883,
            'topic': 'test/batcontrol',
            'auto_discover_enable': False,
            'tls': False,
        }

        with patch('batcontrol.mqtt_api.mqtt.Client') as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.is_connected.return_value = True

            mqtt_api = MqttApi(mock_config)

            # Test publish
            mqtt_api.publish_production_offset(0.85)

            # Verify publish was called
            mock_client.publish.assert_called_with(
                'test/batcontrol/production_offset',
                '0.850'
            )

    def test_mqtt_callback_registration(self):
        """Test that production offset callback can be registered"""
        from batcontrol.mqtt_api import MqttApi

        mock_config = {
            'broker': 'localhost',
            'port': 1883,
            'topic': 'test/batcontrol',
            'auto_discover_enable': False,
            'tls': False,
        }

        with patch('batcontrol.mqtt_api.mqtt.Client') as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mqtt_api = MqttApi(mock_config)

            # Register callback
            callback_fn = Mock()
            mqtt_api.register_set_callback('production_offset', callback_fn, float)

            # Verify subscription
            mock_client.subscribe.assert_called_with('test/batcontrol/production_offset/set')

            # Verify callback is registered
            assert 'test/batcontrol/production_offset/set' in mqtt_api.callbacks
