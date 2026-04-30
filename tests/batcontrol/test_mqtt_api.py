"""Tests for MqttApi._handle_message, focusing on bytes payload decoding."""
from unittest.mock import MagicMock, call

from batcontrol.core import Batcontrol
from batcontrol.logic import PeakShavingConfig
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


def _make_discovery_stub():
    """Return a minimal stub for discovery helper tests."""
    api = MagicMock(spec=MqttApi)
    api.base_topic = 'batcontrol'
    api.publish_mqtt_discovery_message = MagicMock()
    api._topic = MqttApi._topic.__get__(api, MqttApi)
    api._set_topic = MqttApi._set_topic.__get__(api, MqttApi)
    api.send_mqtt_discovery_for_mode = (
        MqttApi.send_mqtt_discovery_for_mode.__get__(api, MqttApi)
    )
    api.send_mqtt_discovery_messages = (
        MqttApi.send_mqtt_discovery_messages.__get__(api, MqttApi)
    )
    return api


def _make_publish_stub():
    """Return a minimal stub for publish helper tests."""
    api = MagicMock(spec=MqttApi)
    api.base_topic = 'batcontrol'
    api.client = MagicMock()
    api.client.is_connected.return_value = True
    api._topic = MqttApi._topic.__get__(api, MqttApi)
    api.publish_SOC = MqttApi.publish_SOC.__get__(api, MqttApi)
    api.publish_discharge_blocked = (
        MqttApi.publish_discharge_blocked.__get__(api, MqttApi)
    )
    api.publish_control_source = (
        MqttApi.publish_control_source.__get__(api, MqttApi)
    )
    api.publish_min_grid_charge_soc = (
        MqttApi.publish_min_grid_charge_soc.__get__(api, MqttApi)
    )
    return api


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


class TestReconnectSubscriptions:
    """Reconnect handling should subscribe to each callback topic once."""

    def test_on_connect_subscribes_each_callback_topic_once(self):
        api = _make_handler_stub()
        api.base_topic = 'batcontrol'
        api.auto_discover_enable = False
        api.callbacks = {
            'batcontrol/mode/set': {},
            'batcontrol/charge_rate/set': {},
        }
        api.on_connect = MqttApi.on_connect.__get__(api, MqttApi)
        client = MagicMock()

        api.on_connect(client, None, None, 0)

        assert client.subscribe.call_args_list == [
            call('batcontrol/mode/set'),
            call('batcontrol/charge_rate/set'),
        ]


class TestPublishedState:
    """Published state payloads should preserve precision and parse cleanly."""

    def test_publish_soc_uses_decimal_precision(self):
        api = _make_publish_stub()

        api.publish_SOC(87.65)

        api.client.publish.assert_called_once_with('batcontrol/SOC', '87.65')

    def test_publish_discharge_blocked_uses_lowercase_boolean(self):
        api = _make_publish_stub()

        api.publish_discharge_blocked(True)

        api.client.publish.assert_called_once_with(
            'batcontrol/discharge_blocked',
            'true',
        )

    def test_publish_min_grid_charge_soc_publishes_ratio_and_percent(self):
        api = _make_publish_stub()

        api.publish_min_grid_charge_soc(0.55)

        assert api.client.publish.call_args_list == [
            call('batcontrol/min_grid_charge_soc_percent', '55'),
            call('batcontrol/min_grid_charge_soc', '0.55'),
        ]


class TestModeDiscovery:
    """Mode discovery should expose the full externally supported mode model."""

    def test_mode_discovery_includes_limit_battery_charge_mode(self):
        api = _make_discovery_stub()

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

    def test_discovery_includes_limit_battery_charge_rate_number(self):
        api = _make_discovery_stub()

        api.send_mqtt_discovery_messages()

        assert any(
            call.args[:3] == (
                'Limit Battery Charge Rate',
                'batcontrol_limit_battery_charge_rate',
                'number',
            )
            and call.args[3] == 'power'
            and call.args[4] == 'W'
            and call.args[5] == 'batcontrol/limit_battery_charge_rate'
            and call.args[6] == 'batcontrol/limit_battery_charge_rate/set'
            and call.kwargs['entity_category'] == 'config'
            and call.kwargs['min_value'] == -1
            and call.kwargs['max_value'] == 10000
            and call.kwargs['initial_value'] == -1
            for call in api.publish_mqtt_discovery_message.call_args_list
        )

    def test_discovery_includes_api_override_active_binary_sensor(self):
        api = _make_discovery_stub()

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

    def test_discharge_blocked_discovery_accepts_lowercase_true(self):
        api = _make_discovery_stub()

        api.send_mqtt_discovery_messages()

        assert any(
            call.args[:3] == (
                'Discharge Blocked',
                'batcontrol_discharge_blocked',
                'sensor',
            )
            and call.args[5] == 'batcontrol/discharge_blocked'
            and "value | lower == 'true'" in call.kwargs['value_template']
            for call in api.publish_mqtt_discovery_message.call_args_list
        )

    def test_discovery_includes_control_source_diagnostic_sensor(self):
        api = _make_discovery_stub()

        api.send_mqtt_discovery_messages()

        assert any(
            call.args[:3] == (
                'Control Source',
                'batcontrol_control_source',
                'sensor',
            )
            and call.args[5] == 'batcontrol/control_source'
            and call.kwargs['entity_category'] == 'diagnostic'
            for call in api.publish_mqtt_discovery_message.call_args_list
        )

    def test_discovery_includes_min_grid_charge_soc_sensor(self):
        api = _make_discovery_stub()

        api.send_mqtt_discovery_messages()

        assert any(
            call.args[:3] == (
                'Minimum Grid Charge SOC',
                'batcontrol_min_grid_charge_soc',
                'sensor',
            )
            and call.args[3] == 'battery'
            and call.args[4] == '%'
            and call.args[5] == 'batcontrol/min_grid_charge_soc_percent'
            and call.kwargs['entity_category'] == 'diagnostic'
            for call in api.publish_mqtt_discovery_message.call_args_list
        )


class TestPublishControlSource:
    """Control source should publish to its dedicated state topic."""

    def test_publish_control_source_uses_control_source_topic(self):
        api = _make_publish_stub()

        api.publish_control_source('api')

        api.client.publish.assert_called_once_with(
            'batcontrol/control_source',
            'api',
            retain=True,
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


def _make_bc_stub(initial_config: PeakShavingConfig = None) -> MagicMock:
    """Stub good enough to invoke Batcontrol.api_set_peak_shaving_* directly.

    Only carries the attributes the setters actually touch.
    """
    bc = MagicMock(spec=Batcontrol)
    bc.peak_shaving_config = (initial_config if initial_config is not None
                              else PeakShavingConfig())
    bc.mqtt_api = MagicMock()
    return bc


class TestPeakShavingPriceLimitApi:
    """Batcontrol.api_set_peak_shaving_price_limit must validate and round-trip."""

    def test_valid_float_updates_and_publishes(self):
        bc = _make_bc_stub()
        Batcontrol.api_set_peak_shaving_price_limit(bc, 0.05)
        assert bc.peak_shaving_config.price_limit == 0.05
        bc.mqtt_api.publish_peak_shaving_price_limit.assert_called_once_with(0.05)

    def test_negative_one_disables_price_component(self):
        # -1 is the documented off-value (no slot price <= -1 ever exists).
        bc = _make_bc_stub()
        Batcontrol.api_set_peak_shaving_price_limit(bc, -1)
        assert bc.peak_shaving_config.price_limit == -1.0
        bc.mqtt_api.publish_peak_shaving_price_limit.assert_called_once_with(-1.0)

    def test_zero_is_accepted(self):
        bc = _make_bc_stub()
        Batcontrol.api_set_peak_shaving_price_limit(bc, 0)
        assert bc.peak_shaving_config.price_limit == 0.0

    def test_invalid_string_keeps_old_value(self):
        original = PeakShavingConfig(price_limit=0.10)
        bc = _make_bc_stub(original)
        Batcontrol.api_set_peak_shaving_price_limit(bc, 'cheap')
        assert bc.peak_shaving_config is original
        bc.mqtt_api.publish_peak_shaving_price_limit.assert_not_called()


class TestPeakShavingModeApi:
    """Batcontrol.api_set_peak_shaving_mode must validate and round-trip."""

    def test_each_valid_mode_is_accepted(self):
        for mode in ('time', 'price', 'combined'):
            bc = _make_bc_stub()
            Batcontrol.api_set_peak_shaving_mode(bc, mode)
            assert bc.peak_shaving_config.mode == mode
            bc.mqtt_api.publish_peak_shaving_mode.assert_called_once_with(mode)

    def test_uppercase_is_normalised(self):
        bc = _make_bc_stub()
        Batcontrol.api_set_peak_shaving_mode(bc, 'TIME')
        assert bc.peak_shaving_config.mode == 'time'

    def test_invalid_mode_keeps_old_value(self):
        original = PeakShavingConfig(mode='price')
        bc = _make_bc_stub(original)
        Batcontrol.api_set_peak_shaving_mode(bc, 'bogus')
        assert bc.peak_shaving_config is original
        bc.mqtt_api.publish_peak_shaving_mode.assert_not_called()
