"""Tests for MqttApi._handle_message, focusing on bytes payload decoding."""
from unittest.mock import MagicMock

from batcontrol.mqtt_api import MqttApi


def _make_handler_stub():
    """Return a minimal stub that only has the attributes used by _handle_message.

    This avoids having to initialise a full MqttApi (which tries to connect to
    MQTT broker, requires a full config, etc.).
    """
    stub = MagicMock(spec=MqttApi)
    stub.callbacks = {}
    # Bind the real _handle_message to our stub
    stub._handle_message = MqttApi._handle_message.__get__(stub, MqttApi)
    return stub


def _make_message(topic: str, payload):
    """Create a minimal MQTT message mock."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload
    return msg


class TestHandleMessageBytesDecoding:
    """_handle_message must decode bytes payloads before calling convert."""

    def test_str_convert_with_bytes_true(self):
        """bytes b'true' must be decoded to 'true', not "b'true'"."""
        api = _make_handler_stub()
        received = []
        api.callbacks['batcontrol/test/set'] = {
            'function': received.append,
            'convert': str,
        }
        msg = _make_message('batcontrol/test/set', b'true')
        api._handle_message(None, None, msg)
        assert received == ['true']

    def test_str_convert_with_bytes_false(self):
        """bytes b'false' must be decoded to 'false'."""
        api = _make_handler_stub()
        received = []
        api.callbacks['batcontrol/test/set'] = {
            'function': received.append,
            'convert': str,
        }
        msg = _make_message('batcontrol/test/set', b'false')
        api._handle_message(None, None, msg)
        assert received == ['false']

    def test_float_convert_with_bytes(self):
        """float conversion from bytes string still works correctly."""
        api = _make_handler_stub()
        received = []
        api.callbacks['batcontrol/test/set'] = {
            'function': received.append,
            'convert': float,
        }
        msg = _make_message('batcontrol/test/set', b'1.5')
        api._handle_message(None, None, msg)
        assert received == [1.5]

    def test_int_convert_with_bytes(self):
        """int conversion from bytes string still works correctly."""
        api = _make_handler_stub()
        received = []
        api.callbacks['batcontrol/test/set'] = {
            'function': received.append,
            'convert': int,
        }
        msg = _make_message('batcontrol/test/set', b'42')
        api._handle_message(None, None, msg)
        assert received == [42]

    def test_str_convert_with_plain_string(self):
        """Plain string payloads (already decoded) remain unchanged."""
        api = _make_handler_stub()
        received = []
        api.callbacks['batcontrol/test/set'] = {
            'function': received.append,
            'convert': str,
        }
        msg = _make_message('batcontrol/test/set', 'true')
        api._handle_message(None, None, msg)
        assert received == ['true']


class TestModeDiscovery:
    """Mode discovery should expose the full externally supported mode model."""

    def test_mode_discovery_includes_limit_battery_charge_mode(self):
        api = MagicMock(spec=MqttApi)
        api.base_topic = 'batcontrol'
        api.publish_mqtt_discovery_message = MagicMock()
        api.send_mqtt_discovery_for_mode = (
            MqttApi.send_mqtt_discovery_for_mode.__get__(api, MqttApi)
        )

        api.send_mqtt_discovery_for_mode()

        options = api.publish_mqtt_discovery_message.call_args.kwargs['options']
        value_template = api.publish_mqtt_discovery_message.call_args.kwargs['value_template']
        command_template = api.publish_mqtt_discovery_message.call_args.kwargs['command_template']

        assert options == [
            'Charge from Grid',
            'Avoid Discharge',
            'Limit Battery Charge',
            'Discharge Allowed',
        ]
        assert "{% elif value == '8' %}Limit Battery Charge" in value_template
        assert "{% elif value == 'Limit Battery Charge' %}8" in command_template


class TestDiscoveryMessages:
    """Discovery should expose key externally visible runtime state."""

    def test_discovery_includes_api_override_active_binary_sensor(self):
        api = MagicMock(spec=MqttApi)
        api.base_topic = 'batcontrol'
        api.publish_mqtt_discovery_message = MagicMock()
        api.send_mqtt_discovery_for_mode = MagicMock()
        api.send_mqtt_discovery_messages = (
            MqttApi.send_mqtt_discovery_messages.__get__(api, MqttApi)
        )

        api.send_mqtt_discovery_messages()

        assert any(
            call.args[:3] == (
                'API Override Active',
                'batcontrol_api_override_active',
                'binary_sensor',
            )
            and call.args[5] == 'batcontrol/api_override_active'
            and call.kwargs['entity_category'] == 'diagnostic'
            and "value == 'true'" in call.kwargs['value_template']
            for call in api.publish_mqtt_discovery_message.call_args_list
        )


class TestPeakShavingEnabledApi:
    """Regression test: peak_shaving/enabled must correctly parse the bytes payload."""

    def _make_api_with_callback(self):
        api = _make_handler_stub()
        self.enabled_values = []

        def fake_set_enabled(enabled_str: str):
            enabled = enabled_str.strip().lower() in ('true', 'on', '1')
            self.enabled_values.append(enabled)

        topic = 'batcontrol/peak_shaving/enabled/set'
        api.callbacks[topic] = {
            'function': fake_set_enabled,
            'convert': str,
        }
        return api, topic

    def test_bytes_true_sets_enabled(self):
        """Sending b'true' via MQTT must enable peak shaving."""
        api, topic = self._make_api_with_callback()
        api._handle_message(None, None, _make_message(topic, b'true'))
        assert self.enabled_values == [True]

    def test_bytes_false_sets_disabled(self):
        """Sending b'false' via MQTT must disable peak shaving."""
        api, topic = self._make_api_with_callback()
        api._handle_message(None, None, _make_message(topic, b'false'))
        assert self.enabled_values == [False]

    def test_bytes_on_sets_enabled(self):
        """Sending b'ON' (HA switch ON) via MQTT must enable peak shaving."""
        api, topic = self._make_api_with_callback()
        api._handle_message(None, None, _make_message(topic, b'ON'))
        assert self.enabled_values == [True]

    def test_bytes_off_sets_disabled(self):
        """Sending b'OFF' (HA switch OFF) via MQTT must disable peak shaving."""
        api, topic = self._make_api_with_callback()
        api._handle_message(None, None, _make_message(topic, b'OFF'))
        assert self.enabled_values == [False]
