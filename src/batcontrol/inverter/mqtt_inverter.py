"""
MQTT Inverter Driver for batcontrol

This driver enables batcontrol to integrate with any battery/inverter system via MQTT topics.
It acts as a generic bridge allowing external systems to provide battery state and receive
control commands over MQTT.

ARCHITECTURE
============
This driver uses batcontrol's shared MQTT connection (configured in the main batcontrol MQTT API).
It does NOT create a separate MQTT client. Instead, it subscribes to inverter-specific topics
using the existing connection. This ensures:
- Single MQTT connection per batcontrol instance
- Consistent MQTT broker configuration
- Shared connection pool and resources
- Unified logging and error handling

TOPIC STRUCTURE AND RETENTION REQUIREMENTS
===========================================

All topics follow the pattern: <batcontrol_base_topic>/inverters/$num/<subtopic>
Here, <batcontrol_base_topic> is the MQTT base topic (from configuration), and <b>$num</b> is a placeholder for the inverter number (e.g., 0, 1, 2), not the literal string "$num".
For example, if base_topic is "batcontrol" and inverter number is 0, the topic would be: batcontrol/inverters/0/status/capacity

Status Topics (Inverter -> batcontrol):
---------------------------------------
These topics MUST be published as RETAINED by the external inverter/bridge
system:
- <batcontrol_base_topic>/inverters/$num/status/capacity             - Battery capacity in Wh (float)

Optional status topics (also RETAINED):
- <batcontrol_base_topic>/inverters/$num/status/min_soc              - Minimum SoC limit in %
                                             (float, 0-100)
- <batcontrol_base_topic>/inverters/$num/status/max_soc              - Maximum SoC limit in %
                                             (float, 0-100)
- <batcontrol_base_topic>/inverters/$num/status/max_charge_rate      - Maximum charge rate in W
                                             (float)
Update these topics at least every 2 minutes to ensure batcontrol has fresh data:

- <batcontrol_base_topic>/inverters/$num/status/soc                  - State of Charge in %
                                             (float, 0-100)

Command Topics (batcontrol -> Inverter):
----------------------------------------
These topics are published by batcontrol and MUST NOT be retained:
- <batcontrol_base_topic>/inverters/$num/command/mode                - Set mode:
                                             'force_charge',
                                             'allow_discharge',
                                             'avoid_discharge'
- <batcontrol_base_topic>/inverters/$num/command/charge_rate         - Set charge rate in W
                                             (float)

WHY RETENTION MATTERS:
- Status topics MUST be RETAINED so batcontrol can read the current state
  immediately on startup
- Command topics MUST NOT be retained to avoid re-executing stale commands
  on reconnect
- If command topics are retained, the inverter may execute old commands
  after restart

CONFIGURATION EXAMPLE
=====================
Note: MQTT connection settings (broker, port, credentials) are configured in batcontrol's
main MQTT API section, not in the inverter configuration.

```yaml
# Main batcontrol MQTT configuration
mqtt:
  broker: 192.168.1.100
  port: 1883
  user: batcontrol
  password: secret

# Inverter configuration
inverter:
  type: mqtt
  capacity: 10000              # Battery capacity in Wh (required)
  min_soc: 5                   # Minimum SoC % (default: 5)
  max_soc: 100                 # Maximum SoC % (default: 100)
  max_grid_charge_rate: 5000   # Maximum charge rate in W (required)
  cache_ttl: 120               # Cache TTL for SOC values in seconds (default: 120)
```

EXTERNAL BRIDGE/INVERTER REQUIREMENTS
======================================

Your external system (inverter, bridge script, etc.) must:

1. Publish battery status as RETAINED messages:
   - Battery capacity in Wh
   - Current operating mode

2. Current soc as normal message (non-retained) to allow timely updates

3. Subscribe to command topics (non-retained):
   - Mode changes (force_charge, allow_discharge, avoid_discharge)
   - Charge rate adjustments

4. Handle reconnection gracefully:
   - Re-publish all status topics as RETAINED on reconnect
   - Don't retain command topics to avoid stale command execution

EXAMPLE BRIDGE IMPLEMENTATION (Python + paho-mqtt)
==================================================
```python
import paho.mqtt.client as mqtt

# Connect to MQTT broker
client = mqtt.Client()
client.username_pw_set("batcontrol", "secret")
client.connect("192.168.1.100", 1883, 60)

# Publish initial state (RETAINED)
client.publish("batcontrol/inverters/0/status/soc", "65.5")
client.publish("batcontrol/inverters/0/status/capacity", "10000", retain=True)
client.publish("batcontrol/inverters/0/status/min_soc", "5", retain=True)
client.publish("batcontrol/inverters/0/status/max_soc", "100", retain=True)
client.publish("batcontrol/inverters/0/status/max_charge_rate", "5000", retain=True)

# Subscribe to commands
def on_message(client, userdata, message):
    topic = message.topic
    value = message.payload.decode()

    if topic == "batcontrol/inverters/0/command/mode":
        print(f"Setting mode to: {value}")
        # Implement your inverter control here

    elif topic == "batcontrol/inverters/0/command/charge_rate":
        print(f"Setting charge rate to: {value}W")
        # Implement your charge rate control here

client.on_message = on_message
client.subscribe("batcontrol/inverters/0/command/#")
client.loop_forever()
```

LIMITATIONS
===========
- No bidirectional acknowledgment: batcontrol assumes commands succeed
- No auto-discovery: All topics must follow the documented structure
- Network dependency: MQTT broker must be reliable and accessible
- Initial state required: Status topics must be available at startup
- Clock sync: Ensure time sync between batcontrol and inverter for accurate scheduling
- QoS 1 for commands: Guarantees delivery but not exactly-once semantics

TROUBLESHOOTING
================
1. "No SOC data available":
   - Check that <base_topic>/status/soc is published frequently, optional retain that.
   - Verify MQTT broker connectivity and credentials
   - Check topic permissions

2. "Commands not executed":
   - Verify external system subscribes to <base_topic>/command/#
   - Check MQTT logs for delivery confirmation
   - Ensure external system is connected

3. "Stale commands after restart":
   - Verify command topics are NOT retained
   - Clear retained command topics: mosquitto_pub -r -n -t 'inverter/command/mode'

4. "State not updating":
   - Ensure external system publishes status regularly
   - Check for MQTT disconnections in logs
   - Verify status topics are RETAINED
"""

import logging
import time
from cachetools import TTLCache
from .baseclass import InverterBaseclass
from ..mqtt_api import MqttApi

logger = logging.getLogger(__name__)
logger.info('Loading module')


class MqttInverter(InverterBaseclass):
    """
    MQTT Inverter driver for batcontrol.

    Integrates external battery/inverter systems via MQTT topics.
    See module docstring for complete documentation on topics, retention, and configuration.
    """

    def __init__(self, config):
        """
        Initialize MQTT Inverter driver.

        Note: MQTT connection is provided by batcontrol's MQTT API and configured there.
        This driver only needs inverter-specific configuration.

        Args:
            config (dict): Configuration dictionary containing:
                - capacity: Battery capacity in Wh (required)
                - min_soc: Minimum SoC in % (default: 5)
                - max_soc: Maximum SoC in % (default: 100)
                - max_grid_charge_rate: Maximum charge rate in W (required)
                - cache_ttl: optional TTL for cached values in seconds (default: 120)
                - base_topic: optional, else uses default structure
        """
        super().__init__(config)

        # Configuration
        self.inverter_topic = config.get('base_topic', 'default')
        self.cache_ttl = config.get('cache_ttl', 120)

        # Battery parameters (from config or defaults)
        self.min_soc = config.get('min_soc', 5)
        self.max_soc = config.get('max_soc', 100)

        # These values should be set in the config, if not throw ValueError
        if 'capacity' not in config:
            raise ValueError('Battery capacity must be specified in config')
        if 'max_grid_charge_rate' not in config:
            raise ValueError('Max grid charge rate must be specified in config')

        self.capacity = config.get('capacity')
        self.max_grid_charge_rate = config.get('max_grid_charge_rate')

        self.last_mode = "allow_discharge"

        # Cached state from MQTT
        self.soc_value = TTLCache(maxsize=2, ttl=config.get('cache_ttl', 120))
        # use timestamp as key to store latest soc value
        self.soc_key = None
        self.charge_rate = 0  # Current charge rate in W

        # MQTT client reference (will be set via activate_mqtt)
        self.mqtt_client = None
        self.mqtt_api = None

        logger.info('MQTT Inverter initialized with base topic: %s (waiting for MQTT API connection)',
                   self.inverter_topic)

    def activate_mqtt(self, api_mqtt_api:MqttApi):
        """
        Activate batcontrol's MQTT API for publishing inverter state and control.

        Uses the shared MQTT connection from batcontrol's MQTT API for both
        publishing inverter state and subscribing to inverter control topics.

        Args:
            api_mqtt_api: The MQTT API instance from batcontrol
        """
        self.mqtt_api = api_mqtt_api
        self.mqtt_client = api_mqtt_api.client

        # Set default inverter_topic if needed, just before subscriptions and discovery
        if self.inverter_topic == "default":
            self.inverter_topic = f'{self.mqtt_api.base_topic}/{self.get_mqtt_inverter_topic()}'

        # Remove trailing slash(es) if present to avoid double slashes in topics
        self.inverter_topic = self.inverter_topic.rstrip('/')

        # Now self.inverter_topic is guaranteed to be set correctly for the following methods
        self._setup_subscriptions()
        self.publish_inverter_discovery_messages()

        logger.info('MQTT API activated for MQTT inverter on base topic: %s', self.inverter_topic)


    def _setup_subscriptions(self):
        """
        Subscribe to all status topics.
        Called after MQTT API is activated.
        """
        if self.mqtt_client:
            status_topic = f'{self.inverter_topic}/status/#'
            self.mqtt_client.message_callback_add(f'{self.inverter_topic}/status/#', self._on_message)
            self.mqtt_client.subscribe(status_topic)
            logger.info('Subscribed to %s', status_topic)

    def _on_message(self, client, userdata, message):  # pylint: disable=unused-argument
        """
        Callback for incoming MQTT messages.
        Updates cached state from status topics.
        """
        topic = message.topic
        payload = message.payload.decode('utf-8')

        logger.debug('Received MQTT message: %s = %s', topic, payload)

        try:
            # Parse topic
            if topic == f'{self.inverter_topic}/status/soc':
                new_soc_key = time.time()
                self.soc_value[new_soc_key] = float(payload)
                self.soc_key = new_soc_key
                logger.debug('Updated SOC: %s%%', self.soc_value[new_soc_key])

            elif topic == f'{self.inverter_topic}/status/capacity':
                self.capacity = float(payload)
                logger.debug('Updated capacity: %s Wh', self.capacity)
            elif topic == f'{self.inverter_topic}/status/min_soc':
                self.min_soc = float(payload)
                logger.debug('Updated min_soc: %s%%', self.min_soc)

            elif topic == f'{self.inverter_topic}/status/max_soc':
                self.max_soc = float(payload)
                logger.debug('Updated max_soc: %s%%', self.max_soc)

            elif topic == f'{self.inverter_topic}/status/max_charge_rate':
                self.max_grid_charge_rate = float(payload)
                logger.debug('Updated max_charge_rate: %s W',
                           self.max_grid_charge_rate)

        except (ValueError, TypeError) as e:
            logger.error('Error parsing MQTT message %s: %s', topic, e)

    def set_mode_force_charge(self, chargerate=500):
        """
        Set inverter to force charge mode.

        Publishes mode and charge rate to MQTT command topics (non-retained).

        Args:
            chargerate (float): Charge rate in W
        """
        self.last_mode = 'force_charge'
        logger.info('Setting mode to force_charge with rate %sW', chargerate)

        # Publish mode command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/mode',
            'force_charge',
            qos=1,
            retain=False
        )

        # Publish charge rate command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/charge_rate',
            str(chargerate),
            qos=1,
            retain=False
        )
        self.charge_rate = chargerate

    def set_mode_allow_discharge(self):
        """
        Set inverter to allow discharge mode.

        Publishes mode to MQTT command topic (non-retained).
        """
        self.last_mode = 'allow_discharge'
        logger.info('Setting mode to allow_discharge')

        # Publish mode command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/mode',
            'allow_discharge',
            qos=1,
            retain=False
        )

    def set_mode_avoid_discharge(self):
        """
        Set inverter to avoid discharge mode.

        Publishes mode to MQTT command topic (non-retained).
        """
        self.last_mode = 'avoid_discharge'
        logger.info('Setting mode to avoid_discharge')

        # Publish mode command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/mode',
            'avoid_discharge',
            qos=1,
            retain=False
        )

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """
        Set inverter to limit battery charge rate mode.

        Publishes mode and max charge rate to MQTT command topics (non-retained).

        Args:
            limit_charge_rate: Maximum charge rate in W (0 = no charging)
        """
        self.last_mode = 'limit_battery_charge'
        logger.info('Setting mode to limit_battery_charge with max rate %sW', limit_charge_rate)

        # Publish mode command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/mode',
            'limit_battery_charge',
            qos=1,
            retain=False
        )

        # Publish max charge rate command (QoS 1, not retained)
        self.mqtt_client.publish(
            f'{self.inverter_topic}/command/limit_battery_charge_rate',
            str(limit_charge_rate),
            qos=1,
            retain=False
        )

    def get_capacity(self):
        """
        Get battery capacity in Wh.

        Returns:
            float: Battery capacity in Wh
        """
        return self.capacity

    def get_SOC(self):  # pylint: disable=invalid-name
        """
        Get current state of charge.

        Returns:
            float: State of charge in percentage (0-100)

        Raises:
            RuntimeError: If SOC data is not available after 180 attempts
        """

        soc_value = None
        max_iterations = 180
        iteration = 0

        while soc_value is None:
            if self.soc_key and self.soc_key in self.soc_value:
                soc_value = self.soc_value[self.soc_key]
                logger.debug('Current SOC value: %s', soc_value)
            else:
                iteration += 1
                if iteration >= max_iterations:
                    raise RuntimeError(
                        f'No SOC data available from MQTT after {max_iterations} attempts '
                        f'(waited {max_iterations} seconds). Check MQTT connection and '
                        f'verify that {self.inverter_topic}/status/soc is being published.'
                    )
                logger.warning('No SOC data available from MQTT, waiting... (attempt %d/%d)',
                             iteration, max_iterations)
                time.sleep(1)  # Wait before retrying

        return soc_value

    def refresh_api_values(self):
        """
        Publish current values to batcontrol's MQTT API.

        This is separate from the inverter MQTT topics - it publishes to
        batcontrol's own MQTT topic structure for monitoring/visualization.
        """
        # Parent class handles standard MQTT publishing
        if self.mqtt_api:
            # Call parent to publish standard metrics
            super().refresh_api_values()

    def publish_inverter_discovery_messages(self):
        """Publish Home Assistant MQTT Auto Discovery messages for MQTT inverter sensors"""
        # First publish common inverter sensors
        super().publish_inverter_discovery_messages()

        # MQTT inverter specific sensors and controls
        if self.mqtt_api:
            # Status topics (external inverter -> batcontrol) are SENSORS in HA
            # These are read from the external system
            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Status SOC",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_status_soc",
                "sensor", "battery", "%",
                f"{self.inverter_topic}/status/soc",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Status Capacity",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_status_capacity",
                "sensor", "energy", "Wh",
                f"{self.inverter_topic}/status/capacity",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Status Min SOC",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_status_min_soc",
                "sensor", "battery", "%",
                f"{self.inverter_topic}/status/min_soc",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Status Max SOC",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_status_max_soc",
                "sensor", "battery", "%",
                f"{self.inverter_topic}/status/max_soc",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Status Max Charge Rate",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_status_max_charge_rate",
                "sensor", "power", "W",
                f"{self.inverter_topic}/status/max_charge_rate",
                entity_category="diagnostic")

            # Command topics (batcontrol -> external inverter) are SENSORS in HA
            # These show what commands batcontrol is sending
            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Command Mode",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_command_mode",
                "sensor", "", "",
                f"{self.inverter_topic}/command/mode",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"MQTT Inverter {self.inverter_num} Command Charge Rate",
                f"batcontrol_mqtt_inverter_{self.inverter_num}_command_charge_rate",
                "sensor", "power", "W",
                f"{self.inverter_topic}/command/charge_rate",
                entity_category="diagnostic")

        logger.info('Published MQTT inverter discovery messages')

    def shutdown(self):
        """
        Cleanly shutdown the MQTT Inverter.

        Note: MQTT connection is managed by batcontrol's MQTT API,
        so we don't disconnect here.
        """
        logger.info('Shutting down MQTT Inverter')
        # Unsubscribe from topics
        if self.mqtt_client:
            try:
                status_topic = f'{self.inverter_topic}/status/#'
                self.mqtt_client.unsubscribe(status_topic)
                logger.info('Unsubscribed from %s', status_topic)
            except Exception as e:
                logger.error('Error during MQTT Inverter shutdown: %s', e)
        logger.info('MQTT Inverter shutdown complete')
