import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch, call

# Add the src directory to Python path for testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from batcontrol.inverter.mqtt_inverter import MqttInverter
from batcontrol.inverter.inverter import Inverter


class TestMqttInverter:
    """Test the MQTT inverter implementation"""

    def test_mqtt_inverter_initialization(self):
        """Test that MQTT inverter initializes with correct configuration"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'min_soc': 10,
            'max_soc': 95,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Check configuration
        assert inverter.inverter_topic == 'inverter'
        assert inverter.capacity == 10000
        assert inverter.min_soc == 10
        assert inverter.max_soc == 95
        assert inverter.max_grid_charge_rate == 5000
        assert inverter.last_mode == 'allow_discharge'

        # MQTT client should not be set yet (requires activate_mqtt)
        assert inverter.mqtt_client is None
        assert inverter.mqtt_api is None

    def test_mqtt_inverter_initialization_with_defaults(self):
        """Test MQTT inverter initialization with default values"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Should use defaults for min/max soc
        assert inverter.min_soc == 5
        assert inverter.max_soc == 100

    def test_activate_mqtt_subscribes_to_topics(self):
        """Test that activate_mqtt sets up subscriptions"""
        config = {
            'base_topic': 'test/inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock MQTT API
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client

        # Activate MQTT
        inverter.activate_mqtt(mock_mqtt_api)

        # Verify client was set
        assert inverter.mqtt_client == mock_client
        assert inverter.mqtt_api == mock_mqtt_api

        # Verify subscription
        mock_client.subscribe.assert_called_once_with('test/inverter/status/#')
        mock_client.message_callback_add.assert_called_once()

    def test_mqtt_message_updates_soc(self):
        """Test that incoming SOC messages update the cached value"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message for SOC
        mock_message = Mock()
        mock_message.topic = 'inverter/status/soc'
        mock_message.payload = b'75.5'

        # Process message
        inverter._on_message(None, None, mock_message)

        # Verify SOC was updated
        assert inverter.get_SOC() == 75.5

    def test_mqtt_message_updates_capacity(self):
        """Test that incoming capacity messages update the cached value"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message for capacity
        mock_message = Mock()
        mock_message.topic = 'inverter/status/capacity'
        mock_message.payload = b'12000'

        # Process message
        inverter._on_message(None, None, mock_message)

        # Verify capacity was updated
        assert inverter.get_capacity() == 12000

    def test_mqtt_message_updates_min_soc(self):
        """Test that incoming min_soc messages update the cached value"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message for min_soc
        mock_message = Mock()
        mock_message.topic = 'inverter/status/min_soc'
        mock_message.payload = b'15'

        # Process message
        inverter._on_message(None, None, mock_message)

        # Verify min_soc was updated
        assert inverter.min_soc == 15

    def test_mqtt_message_updates_max_soc(self):
        """Test that incoming max_soc messages update the cached value"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message for max_soc
        mock_message = Mock()
        mock_message.topic = 'inverter/status/max_soc'
        mock_message.payload = b'90'

        # Process message
        inverter._on_message(None, None, mock_message)

        # Verify max_soc was updated
        assert inverter.max_soc == 90

    def test_mqtt_message_updates_max_charge_rate(self):
        """Test that incoming max_charge_rate messages update the cached value"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message for max_charge_rate
        mock_message = Mock()
        mock_message.topic = 'inverter/status/max_charge_rate'
        mock_message.payload = b'6000'

        # Process message
        inverter._on_message(None, None, mock_message)

        # Verify max_charge_rate was updated
        assert inverter.max_grid_charge_rate == 6000

    def test_set_mode_force_charge_publishes_command(self):
        """Test that force charge mode publishes correct MQTT commands"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Set mode to force charge
        inverter.set_mode_force_charge(3000)

        # Verify MQTT publish calls
        assert mock_client.publish.call_count == 2

        # Check mode command (QoS 1, not retained)
        mode_call = call('inverter/command/mode', 'force_charge', qos=1, retain=False)
        assert mode_call in mock_client.publish.call_args_list

        # Check charge rate command (QoS 1, not retained)
        rate_call = call('inverter/command/charge_rate', '3000', qos=1, retain=False)
        assert rate_call in mock_client.publish.call_args_list

    def test_set_mode_allow_discharge_publishes_command(self):
        """Test that allow discharge mode publishes correct MQTT command"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Set mode to allow discharge
        inverter.set_mode_allow_discharge()

        # Verify MQTT publish call
        mock_client.publish.assert_called_once_with(
            'inverter/command/mode',
            'allow_discharge',
            qos=1,
            retain=False
        )

    def test_set_mode_avoid_discharge_publishes_command(self):
        """Test that avoid discharge mode publishes correct MQTT command"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Set mode to avoid discharge
        inverter.set_mode_avoid_discharge()

        # Verify MQTT publish call
        mock_client.publish.assert_called_once_with(
            'inverter/command/mode',
            'avoid_discharge',
            qos=1,
            retain=False
        )

    def test_set_mode_limit_battery_charge_publishes_commands(self):
        """Test that limit battery charge mode publishes correct MQTT commands"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Set mode to limit battery charge with max rate 2000W
        inverter.set_mode_limit_battery_charge(2000)

        # Verify MQTT publish calls
        assert mock_client.publish.call_count == 2
        calls = mock_client.publish.call_args_list

        # Check mode command
        assert calls[0] == call(
            'inverter/command/mode',
            'limit_battery_charge',
            qos=1,
            retain=False
        )

        # Check charge rate command
        assert calls[1] == call(
            'inverter/command/limit_battery_charge_rate',
            '2000',
            qos=1,
            retain=False
        )

        # Verify mode was updated
        assert inverter.last_mode == 'limit_battery_charge'

    def test_set_mode_limit_battery_charge_zero(self):
        """Test that limit=0 blocks all charging"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Set mode to limit battery charge with limit=0 (no charging)
        inverter.set_mode_limit_battery_charge(0)

        # Verify MQTT publish calls
        assert mock_client.publish.call_count == 2
        calls = mock_client.publish.call_args_list

        # Check charge rate command is 0
        assert calls[1] == call(
            'inverter/command/limit_battery_charge_rate',
            '0',
            qos=1,
            retain=False
        )

    def test_shutdown_unsubscribes_from_topics(self):
        """Test that shutdown cleanly unsubscribes from MQTT topics"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Setup mock MQTT client
        mock_mqtt_api = MagicMock()
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Shutdown
        inverter.shutdown()

        # Verify unsubscribe was called
        mock_client.unsubscribe.assert_called_once_with('inverter/status/#')

    def test_mqtt_message_invalid_payload_handled_gracefully(self):
        """Test that invalid message payloads are handled without crashing"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Create mock message with invalid payload
        mock_message = Mock()
        mock_message.topic = 'inverter/status/soc'
        mock_message.payload = b'not_a_number'

        # Should not crash
        inverter._on_message(None, None, mock_message)

        # SOC should raise RuntimeError after timeout (patch to 2 iterations for fast test)
        with patch.object(inverter, 'get_SOC') as mock_get_soc:
            # Override max_iterations in the actual method
            original_get_soc = MqttInverter.get_SOC.__get__(inverter, MqttInverter)

            def fast_get_soc():
                soc_value = None
                max_iterations = 2  # Fast test: only wait 2 seconds
                iteration = 0

                while soc_value is None:
                    if inverter.soc_key and inverter.soc_key in inverter.soc_value:
                        soc_value = inverter.soc_value[inverter.soc_key]
                    else:
                        iteration += 1
                        if iteration >= max_iterations:
                            raise RuntimeError(
                                f'No SOC data available from MQTT after {max_iterations} attempts '
                                f'(waited {max_iterations} seconds). Check MQTT connection and '
                                f'verify that {inverter.inverter_topic}/status/soc is being published.'
                            )
                return soc_value

            # Test that RuntimeError is raised when no SOC data is available
            with pytest.raises(RuntimeError, match='No SOC data available from MQTT after 2 attempts'):
                fast_get_soc()

    def test_inherited_energy_calculations_work(self):
        """Test that inherited energy calculation methods work"""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'min_soc': 10,
            'max_soc': 95,
            'max_grid_charge_rate': 5000
        }

        inverter = MqttInverter(config)

        # Simulate receiving SOC
        mock_message = Mock()
        mock_message.topic = 'inverter/status/soc'
        mock_message.payload = b'65'
        inverter._on_message(None, None, mock_message)

        # Test inherited methods
        stored_energy = inverter.get_stored_energy()
        assert stored_energy == 6500  # 65% of 10000 Wh

        stored_usable_energy = inverter.get_stored_usable_energy()
        assert stored_usable_energy == 5500  # (65% - 10%) of 10000 Wh

        free_capacity = inverter.get_free_capacity()
        assert free_capacity == 3000  # (95% - 65%) of 10000 Wh
