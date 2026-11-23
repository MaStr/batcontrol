import threading
import time

import pytest
from unittest.mock import Mock, MagicMock, patch, call

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from batcontrol.inverter import mqtt_inverter, resilient_wrapper
from batcontrol.inverter.exceptions import InverterCommunicationError, InverterOutageError
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.inverter.mqtt_inverter import MqttInverter
from batcontrol.mqtt_api import MqttApi
from batcontrol.inverter.inverter import Inverter


class TestMqttInverter:
    """Test the MQTT inverter implementation.

    These tests are implementation-specific, but they also make the shared
    inverter command semantics explicit for MQTT-backed control.
    """

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
        mock_mqtt_api = MagicMock(spec=MqttApi)
        mock_mqtt_api.base_topic = 'batcontrol'
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client

        # Activate MQTT
        inverter.activate_mqtt(mock_mqtt_api)

        # Verify client was set
        assert inverter.mqtt_client == mock_client
        assert inverter.mqtt_api == mock_mqtt_api

        # Verify subscription goes through MqttApi so it survives a reconnect
        mock_mqtt_api.register_raw_subscription.assert_called_once_with(
            'test/inverter/status/#', inverter._on_message)
        mock_client.subscribe.assert_not_called()

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

    def test_force_charge_matches_inverter_mode_command_contract(self):
        """Test that MQTT force charge matches the expected inverter command contract."""
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

    def test_allow_discharge_matches_inverter_mode_command_contract(self):
        """Test that MQTT allow discharge matches the expected inverter command contract."""
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

    def test_avoid_discharge_matches_inverter_mode_command_contract(self):
        """Test that MQTT avoid discharge matches the expected inverter command contract."""
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

    def test_limit_battery_charge_matches_inverter_mode_command_contract(self):
        """Test that MQTT limit battery charge matches the expected inverter command contract."""
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
        mock_mqtt_api = MagicMock(spec=MqttApi)
        mock_mqtt_api.base_topic = 'batcontrol'
        mock_client = MagicMock()
        mock_mqtt_api.client = mock_client
        inverter.activate_mqtt(mock_mqtt_api)

        # Shutdown
        inverter.shutdown()

        # Verify unsubscribe was called
        mock_mqtt_api.unregister_raw_subscription.assert_called_once_with(
            'inverter/status/#')

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

        # Invalid payload must not create a SOC value
        assert inverter._soc is None

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

    def test_status_subscription_survives_reconnect(self):
        """Regression: after a broker reconnect status/# must be subscribed
        again, otherwise SOC updates stop until batcontrol is restarted."""
        config = {
            'base_topic': 'inverter',
            'capacity': 10000,
            'max_grid_charge_rate': 5000
        }
        inverter = MqttInverter(config)

        api = MagicMock(spec=MqttApi)
        api.base_topic = 'batcontrol'
        api.auto_discover_enable = False
        api.callbacks = {}
        api.raw_subscriptions = {}
        api.client = MagicMock()
        for name in ('on_connect', 'register_raw_subscription',
                     'unregister_raw_subscription'):
            setattr(api, name, getattr(MqttApi, name).__get__(api, MqttApi))

        inverter.activate_mqtt(api)

        reconnect_client = MagicMock()
        api.on_connect(reconnect_client, None, None,
                      ReasonCode(PacketTypes.CONNACK, 'Success'), None)
        reconnect_client.subscribe.assert_called_once_with('inverter/status/#')
        reconnect_client.message_callback_remove.assert_not_called()

        inverter.shutdown()
        assert api.raw_subscriptions == {}


def _soc_message(inverter, value):
    message = Mock()
    message.topic = f'{inverter.inverter_topic}/status/soc'
    message.payload = str(value).encode('utf-8')
    inverter._on_message(None, None, message)


def _activated_inverter(**extra):
    config = {'base_topic': 'inverter', 'capacity': 10000,
              'max_grid_charge_rate': 5000}
    config.update(extra)
    inverter = MqttInverter(config)
    mqtt_api = MagicMock(spec=MqttApi)
    mqtt_api.base_topic = 'batcontrol'
    mqtt_api.client = MagicMock()
    inverter.activate_mqtt(mqtt_api)
    return inverter


class FakeClock:
    """Monotonic clock; time only moves when the test says so."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeEvent:
    """Stands in for threading.Event; wait() advances the fake clock."""

    def __init__(self, clock, on_wait=None):
        self.clock = clock
        self.on_wait = on_wait
        self.flag = False
        self.waits = []

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait()
        if not self.flag:
            self.clock.now += timeout
        return self.flag


class TestGetSocWaiting:
    """get_SOC waits only inside the startup window (R-M23)."""

    @pytest.fixture
    def clock(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(mqtt_inverter.time, 'monotonic', clock.monotonic)
        return clock

    def _fake_event(self, inverter, clock, on_wait=None):
        event = FakeEvent(clock, on_wait)
        inverter._soc_received = event
        return event

    def test_cached_value_returned_without_waiting(self, clock):
        inverter = _activated_inverter()
        event = self._fake_event(inverter, clock)
        _soc_message(inverter, 42.0)
        assert inverter.get_SOC() == 42.0
        assert event.waits == []

    def test_waits_until_soc_arrives(self, clock):
        inverter = _activated_inverter()
        event = self._fake_event(
            inverter, clock, on_wait=lambda: _soc_message(inverter, 55.0))
        assert inverter.get_SOC() == 55.0
        assert event.waits == [mqtt_inverter.SOC_STARTUP_WAIT_SECONDS]

    def test_wait_is_limited_to_window_after_activation(self, clock):
        inverter = _activated_inverter()
        event = self._fake_event(inverter, clock)
        clock.now += 100
        with pytest.raises(RuntimeError, match='within 180 seconds after startup'):
            inverter.get_SOC()
        assert event.waits == [mqtt_inverter.SOC_STARTUP_WAIT_SECONDS - 100]

    def test_no_wait_after_window_passed(self, clock):
        inverter = _activated_inverter()
        event = self._fake_event(inverter, clock)
        with pytest.raises(RuntimeError, match='after startup'):
            inverter.get_SOC()
        with pytest.raises(RuntimeError, match='after startup'):
            inverter.get_SOC()
        assert len(event.waits) == 1

    def test_read_before_activation_starts_window(self, clock):
        inverter = MqttInverter({'base_topic': 'inverter', 'capacity': 10000,
                                 'max_grid_charge_rate': 5000})
        event = self._fake_event(inverter, clock)
        with pytest.raises(RuntimeError, match='after startup'):
            inverter.get_SOC()
        assert event.waits == [mqtt_inverter.SOC_STARTUP_WAIT_SECONDS]

    def test_activation_does_not_restart_window(self, clock):
        inverter = MqttInverter({'base_topic': 'inverter', 'capacity': 10000,
                                 'max_grid_charge_rate': 5000})
        event = self._fake_event(inverter, clock)
        with pytest.raises(RuntimeError, match='after startup'):
            inverter.get_SOC()  # starts and uses up the window
        mqtt_api = MagicMock(spec=MqttApi)
        mqtt_api.base_topic = 'batcontrol'
        mqtt_api.client = MagicMock()
        inverter.activate_mqtt(mqtt_api)
        with pytest.raises(RuntimeError, match='after startup'):
            inverter.get_SOC()
        assert event.waits == [mqtt_inverter.SOC_STARTUP_WAIT_SECONDS]

    def test_expired_soc_raises_immediately(self, clock):
        inverter = _activated_inverter(cache_ttl=60)
        event = self._fake_event(inverter, clock)
        _soc_message(inverter, 70.0)
        assert inverter.get_SOC() == 70.0

        clock.now += 61  # value expired
        with pytest.raises(RuntimeError, match='within 60 seconds'):
            inverter.get_SOC()
        assert event.waits == []

        _soc_message(inverter, 71.0)  # bridge recovers
        assert inverter.get_SOC() == 71.0

    def test_soc_at_ttl_boundary_is_still_current(self, clock):
        inverter = _activated_inverter(cache_ttl=60)
        _soc_message(inverter, 70.0)
        clock.now += 60
        assert inverter.get_SOC() == 70.0


def test_concurrent_callers_share_startup_window(monkeypatch):
    """A first read from another thread (e.g. evcc) must not use up the wait."""
    monkeypatch.setattr(mqtt_inverter, 'SOC_STARTUP_WAIT_SECONDS', 5)
    inverter = _activated_inverter()
    results = {}

    def read(name):
        try:
            results[name] = inverter.get_SOC()
        except RuntimeError as e:  # pragma: no cover - reported via assert
            results[name] = e

    threads = [threading.Thread(target=read, args=(n,)) for n in ('evcc', 'main')]
    for t in threads:
        t.start()
    time.sleep(0.1)
    _soc_message(inverter, 63.0)
    for t in threads:
        t.join(timeout=5)
    assert results == {'evcc': 63.0, 'main': 63.0}


class TestGetSocWithResilientWrapper:
    """Missing SOC after initialization is a tolerated outage (R-M23)."""

    def test_missing_soc_skips_cycle_then_terminates(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(mqtt_inverter.time, 'monotonic', clock.monotonic)
        monkeypatch.setattr(resilient_wrapper.time, 'monotonic', clock.monotonic)

        inverter = _activated_inverter(cache_ttl=120)
        event = FakeEvent(clock)
        inverter._soc_received = event
        wrapper = ResilientInverterWrapper(inverter, outage_tolerance_seconds=600)

        _soc_message(inverter, 50.0)
        assert wrapper.get_SOC() == 50.0
        wrapper.set_mode_allow_discharge()  # initialization complete

        clock.now += 200  # bridge stopped publishing
        with pytest.raises(InverterCommunicationError):
            wrapper.get_SOC()
        clock.now += 601
        with pytest.raises(InverterOutageError):
            wrapper.get_SOC()
        assert event.waits == []


def test_discovery_includes_limit_battery_charge_rate():
    """The peak shaving command topic is announced to Home Assistant (R-M12)."""
    inverter = _activated_inverter()
    calls = inverter.mqtt_api.publish_mqtt_discovery_message.call_args_list
    by_topic = {c.args[5]: c for c in calls}
    topic = 'inverter/command/limit_battery_charge_rate'
    assert topic in by_topic
    args = by_topic[topic].args
    assert args[1] == 'batcontrol_mqtt_inverter_0_command_limit_battery_charge_rate'
    assert args[2:5] == ('sensor', 'power', 'W')
    assert by_topic[topic].kwargs['entity_category'] == 'diagnostic'
