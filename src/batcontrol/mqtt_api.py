"""
This module provides an API to publish data from batcontrol to MQTT
for further processing and visualization.

The following topics are published:
- /status: online/offline status of batcontrol
- /evaluation_intervall: interval in seconds
- /last_evaluation: timestamp of last evaluation
- /mode: operational mode (-1 = charge from grid, 0 = avoid discharge, 8 = limit battery charge, 10 = discharge allowed)
- /max_charging_from_grid_limit: charge limit in 0.1-1
- /max_charging_from_grid_limit_percent: charge limit in %
- /always_allow_discharge_limit: always discharge limit in 0.1-1
- /always_allow_discharge_limit_percent: always discharge limit in %
- /always_allow_discharge_limit_capacity: always discharge limit in Wh
- /charge_rate: charge rate in W
- /limit_battery_charge_rate: dynamic battery charge rate limit in W
- /max_energy_capacity: maximum capacity of battery in Wh
- /stored_energy_capacity: energy stored in battery in Wh
- /stored_usable_energy_capacity: energy stored in battery in Wh and usable (min SOC considered)
- /reserved_energy_capacity: estimated energy reserved for discharge in Wh
- /SOC: state of charge in %
- /min_price_difference : minimum price difference in EUR
- /discharge_blocked        : bool  # Discharge is blocked by other sources
- /production_offset: production offset percentage (1.0 = 100%, 0.8 = 80%, etc.)

The following statistical arrays are published as JSON arrays:
- /FCST/production: forecasted production in W
- /FCST/consumption: forecasted consumption in W
- /FCST/prices: forecasted price in EUR
- /FCST/net_consumption: forecasted net consumption in W

Implemented Input-API:
- /mode/set: set mode (-1 = charge from grid, 0 = avoid discharge, 8 = limit battery charge, 10 = discharge allowed)
- /charge_rate/set: set charge rate in W, sets mode to -1
- /limit_battery_charge_rate/set: set dynamic battery charge rate limit in W
- /always_allow_discharge_limit/set: set always discharge limit in 0.1-1
- /max_charging_from_grid_limit/set: set charge limit in 0-1
- /min_price_difference/set: set minimum price difference in EUR
- /production_offset/set: set production offset percentage (0.0-2.0)

The module uses the paho-mqtt library for MQTT communication and numpy for handling arrays.
"""
import time
import json
import logging
import importlib.metadata
import paho.mqtt.client as mqtt
import numpy as np

logger = logging.getLogger(__name__)
logger.info('Loading module')


class MqttApi:
    """ MQTT API to publish data from batcontrol to MQTT for further processing+visualization"""
    SET_SUFFIX = '/set'

    def __init__(self, config: dict, interval_minutes: int = 60):
        self.config = config
        self.interval_minutes = interval_minutes
        self.base_topic = config['topic']
        if config.get('auto_discover_enable'):
            self.auto_discover_enable = config['auto_discover_enable']
        else:
            self.auto_discover_enable = False

        if config.get('auto_discover_topic'):
            self.auto_discover_topic = config['auto_discover_topic']
        else:
            self.auto_discover_topic = "homeassistant"

        self.callbacks = {}

        # Remove trailing slashes if present to avoid double slashes in topics
        self.base_topic = self.base_topic.rstrip('/')
        self.auto_discover_topic = self.auto_discover_topic.rstrip('/')

        self.client = mqtt.Client()
        if 'logger' in config and config['logger'] is True:
            self.client.enable_logger(logger)

        if 'username' in config and 'password' in config:
            self.client.username_pw_set(config['username'], config['password'])

        self.client.will_set(
            self.base_topic +
            '/status',
            'offline',
            retain=True)

        # TLS , not tested yet
        if config['tls'] is True:
            self.client.tls_set(
                config['tls']['ca_certs'],
                config['tls']['certfile'],
                config['tls']['keyfile'],
                cert_reqs=config['tls']['cert_reqs'],
                tls_version=config['tls']['tls_version'],
                ciphers=config['tls']['ciphers']
            )

        self.client.on_connect = self.on_connect
        self.client.loop_start()
        retry_attempts = config.get('retry_attempts', 5)
        retry_delay = config.get('retry_delay', 10)
        while retry_attempts > 0:
            try:
                self.client.connect(config['broker'], config['port'], 60)
                break
            except (ValueError, TypeError) as e:
                logger.error(
                    'Connection failed: %s, retrying[%d]x in [%d] seconds',
                    e,
                    retry_attempts,
                    retry_delay)
                retry_attempts -= 1
                if retry_attempts == 0:
                    logger.error('All retry attempts failed')
                    raise
                logger.info(
                    'Retrying connection in %d seconds...',
                    retry_delay)
                time.sleep(retry_delay)

    def on_connect(self, client, userdata, flags, rc):
        """ Callback for MQTT connection to serve /status"""
        logger.info('Connected with result code %s', rc)
        # Make public, that we are running.
        client.publish(self.base_topic + '/status', 'online', retain=True)
        # publish HA mqtt AutoDiscovery messages at startup
        if self.auto_discover_enable:
            self.send_mqtt_discovery_messages()
        # Handle reconnect case
        for topic in self.callbacks:
            logger.debug('Subscribing topic: %s', topic)
            for topic in self.callbacks:
                client.subscribe(topic)

    def wait_ready(self) -> bool:
        """ Wait for MQTT connection to be ready"""
        retry = 30
        # Check if we are connected and wait for it
        while self.client.is_connected() is False:
            retry -= 1
            if retry == 0:
                logger.error('Could not connect to MQTT Broker')
                return False
            logger.info('Waiting for connection')
            time.sleep(1)

        return True

    def _handle_message(self, client, userdata, message):  # pylint: disable=unused-argument
        """ Handle and dispatch incoming messages"""
        logger.debug('Received message on %s', message.topic)
        if message.topic in self.callbacks:
            try:
                self.callbacks[message.topic]['function'](
                    self.callbacks[message.topic]['convert'](message.payload)
                )
            except (ValueError, TypeError) as e:
                logger.error('Error in callback %s : %s', message.topic, e)
        else:
            logger.warning('No callback registered for %s', message.topic)

    def register_set_callback(
            self,
            topic: str,
            callback: callable,
            convert: callable) -> None:
        """ Generic- register a callback for changing values inside batcontrol via
            MQTT set topics
        """
        topic_string = self.base_topic + "/" + topic + MqttApi.SET_SUFFIX
        logger.debug('Registering callback for %s', topic_string)
        # set api endpoints, generic subscription
        self.callbacks[topic_string] = {
            'function': callback, 'convert': convert}
        self.client.subscribe(topic_string)
        self.client.message_callback_add(topic_string, self._handle_message)

    def publish_mode(self, mode: int) -> None:
        """ Publish the mode (charge, lock, discharge) to MQTT
            /mode
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic + '/mode', mode)

    def publish_charge_rate(self, rate: float) -> None:
        """ Publish the forced charge rate in W to MQTT
            /charge_rate
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic + '/charge_rate', rate)

    def publish_limit_battery_charge_rate(self, limit: int) -> None:
        """ Publish dynamic battery charge rate limit to MQTT
            /limit_battery_charge_rate
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/limit_battery_charge_rate',
                limit,
                retain=True
            )

    def publish_production(
            self,
            production: np.ndarray,
            timestamp: float) -> None:
        """ Publish the production to MQTT
            /FCST/production
            The value is in W and based of solar forecast API.
            The length is the same as used in internal arrays.
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/FCST/production',
                json.dumps(self._create_forecast(production, timestamp))
            )

    def _create_forecast(self, forecast: np.ndarray, timestamp: float) -> dict:
        """ Create a forecast JSON object from a numpy array and a timestamp.

        Handles both 15-minute and 60-minute intervals based on self.interval_minutes.
        Timestamps are aligned to the start of the current interval.
        """
        interval_seconds = self.interval_minutes * 60

        # Align timestamp to the start of the current interval
        now = timestamp - (timestamp % interval_seconds)

        data_list = []
        for i, value in enumerate(forecast):
            data_list.append(
                {
                    'time_start': now + i * interval_seconds,
                    'value': value,
                    'time_end': now + (i + 1) * interval_seconds
                }
            )

        data = {'data': data_list}
        return data

    def publish_consumption(
            self,
            consumption: np.ndarray,
            timestamp: float) -> None:
        """ Publish the consumption to MQTT
            /FCST/consumption
            The value is in W and based of load profile and multiplied with
                personal yearly consumption.
            The length is the same as used in internal arrays.
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/FCST/consumption',
                json.dumps(self._create_forecast(consumption, timestamp))
            )

    def publish_prices(self, price: np.ndarray, timestamp: float) -> None:
        """ Publish the prices to MQTT
            /FCST/prices
            The length is the same as used in internal arrays.
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/FCST/prices',
                json.dumps(self._create_forecast(price, timestamp))
            )

    def publish_net_consumption(
            self,
            net_consumption: np.ndarray,
            timestamp: float) -> None:
        """ Publish the net consumption in W to MQTT
            /FCST/net_consumption
            The length is the same as used in internal arrays.
            This is the difference between production and consumption.
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/FCST/net_consumption',
                json.dumps(self._create_forecast(net_consumption, timestamp))
            )

    def publish_SOC(self, soc: float) -> None:       # pylint: disable=invalid-name
        """ Publish the state of charge in % to MQTT
            /SOC
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic + '/SOC', f'{int(soc):03}')

    def publish_stored_energy_capacity(self, stored_energy: float) -> None:
        """ Publish the stored energy capacity in Wh to MQTT
            /stored_energy_capacity
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/stored_energy_capacity',
                f'{stored_energy:.1f}')

    def publish_stored_usable_energy_capacity(
            self, stored_energy: float) -> None:
        """ Publish the stored usable energy capacity in Wh to MQTT
            /stored_usable_energy_capacity
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/stored_usable_energy_capacity',
                f'{stored_energy:.1f}'
            )

    def publish_reserved_energy_capacity(self, reserved_energy: float) -> None:
        """ Publish the reserved energy capacity in Wh to MQTT
            /reserved_energy_capacity
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/reserved_energy_capacity',
                f'{reserved_energy:.1f}'
            )

    def publish_always_allow_discharge_limit_capacity(
            self, discharge_limit: float) -> None:
        """ Publish the always discharge limit in Wh to MQTT
            /always_allow_discharge_limit_capacity
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit_capacity',
                f'{discharge_limit:.1f}'
            )

    def publish_always_allow_discharge_limit(
            self, allow_discharge_limit: float) -> None:
        """ Publish the always discharge limit to MQTT
            /always_allow_discharge_limit as digit
            /always_allow_discharge_limit_percent
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit',
                f'{allow_discharge_limit:.2f}'
            )
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit_percent',
                f'{allow_discharge_limit * 100:.0f}'
            )

    def publish_max_charging_from_grid_limit(
            self, charge_limit: float) -> None:
        """ Publish the maximum charging limit to MQTT
            /max_charging_from_grid_limit_percent
            /max_charging_from_grid_limit   as digit.
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/max_charging_from_grid_limit_percent',
                f'{charge_limit * 100:.0f}'
            )
            self.client.publish(
                self.base_topic + '/max_charging_from_grid_limit',
                f'{charge_limit:.2f}'
            )

    def publish_min_price_difference(
            self, min_price_difference: float) -> None:
        """ Publish the minimum price difference to MQTT found in config
            /min_price_difference
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/min_price_difference',
                f'{min_price_difference:.3f}'
            )

    def publish_min_price_difference_rel(
            self, min_price_difference_rel: float) -> None:
        """ Publish the relative minimum price difference to MQTT found in config
            /min_price_difference_rel
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/min_price_difference_rel',
                f'{min_price_difference_rel:.3f}'
            )

    def publish_min_dynamic_price_diff(
            self, dynamic_price_diff: float) -> None:
        """ Publish the dynamic price difference limit to MQTT
            /min_dynamic_price_difference
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/min_dynamic_price_difference',
                f'{dynamic_price_diff:.3f}'
            )

    def publish_max_energy_capacity(self, max_capacity: float) -> None:
        """ Publish the maximum energy capacity to MQTT
            /max_energy_capacity
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/max_energy_capacity',
                f'{max_capacity:.1f}'
            )

    def publish_evaluation_intervall(self, intervall: int) -> None:
        """ Publish the evaluation intervall to MQTT
            /evaluation_intervall
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic +
                                '/evaluation_intervall', f'{intervall:.0f}')

    def publish_last_evaluation_time(self, timestamp: float) -> None:
        """ Publish the last evaluation timestamp to MQTT
            This is the time when the last evaluation was started.
            /last_evaluation
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic +
                                '/last_evaluation', f'{timestamp:.0f}')

    def publish_discharge_blocked(self, discharge_blocked: bool) -> None:
        """ Publish the discharge blocked status to MQTT
            /discharge_blocked
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic +
                '/discharge_blocked',
                str(discharge_blocked))

    def publish_production_offset(self, production_offset: float) -> None:
        """ Publish the production offset percentage to MQTT
            /production_offset
        """
        if self.client.is_connected():
            self.client.publish(
                self.base_topic + '/production_offset',
                f'{production_offset:.3f}'
            )

    # For depended APIs like the Fronius Inverter classes, which is not
    # directly batcontrol.
    def generic_publish(self, topic: str, value: str) -> None:
        """ Publish a generic value to a topic
            For depended APIs like the Fronius Inverter classes, which is not directly batcontrol.
        """
        if self.client.is_connected():
            self.client.publish(self.base_topic + '/' + topic, value)

    # mqtt auto discovery
    def send_mqtt_discovery_messages(self) -> None:
        """ Publish all offered mqtt discovery config messages
        """
        # control
        self.send_mqtt_discovery_for_mode()

        # configuration
        self.publish_mqtt_discovery_message(
            "Charge Rate",
            "batcontrol_charge_rate",
            "number",
            "power",
            "W",
            self.base_topic +
            "/charge_rate",
            self.base_topic +
            "/charge_rate/set",
            entity_category="config",
            min_value=0,
            max_value=10000,
            initial_value=10000)

        self.publish_mqtt_discovery_message(
            "Always Allow Discharge Limit",
            "batcontrol_always_allow_discharge_limit",
            "number",
            None,
            None,
            self.base_topic +
            "/always_allow_discharge_limit",
            self.base_topic +
            "/always_allow_discharge_limit/set",
            entity_category="config",
            min_value=0.0,
            max_value=1.0,
            step_value=0.1,
            initial_value=0.9)

        self.publish_mqtt_discovery_message(
            "Max Charging From Grid Limit",
            "batcontrol_max_charging_from_grid_limit",
            "number",
            None,
            None,
            self.base_topic +
            "/max_charging_from_grid_limit",
            self.base_topic +
            "/max_charging_from_grid_limit/set",
            entity_category="config",
            min_value=0.0,
            max_value=1.0,
            step_value=0.1,
            initial_value=0.9)

        self.publish_mqtt_discovery_message(
            "Min Price Difference",
            "batcontrol_min_price_difference",
            "number",
            "monetary",
            None,
            self.base_topic +
            "/min_price_difference",
            self.base_topic +
            "/min_price_difference/set",
            entity_category="config",
            min_value=0,
            max_value=0.5,
            step_value=0.01,
            initial_value=0.05)

        self.publish_mqtt_discovery_message(
            "Production Offset",
            "batcontrol_production_offset",
            "number",
            None,
            None,
            self.base_topic +
            "/production_offset",
            self.base_topic +
            "/production_offset/set",
            entity_category="config",
            min_value=0.0,
            max_value=2.0,
            step_value=0.01,
            initial_value=1.0)

        # sensors
        self.publish_mqtt_discovery_message(
            "Discharge Blocked",
            "batcontrol_discharge_blocked",
            "sensor",
            None,
            None,
            self.base_topic +
            "/discharge_blocked",
            value_template="{% if value | lower == 'True' %}blocked{% else %}not blocked{% endif %}")

        self.publish_mqtt_discovery_message(
            "Reserved Energy Capacity",
            "batcontrol_reserved_energy_capacity",
            "sensor",
            "energy",
            "Wh",
            self.base_topic +
            "/reserved_energy_capacity")

        self.publish_mqtt_discovery_message(
            "Stored Usable Energy Capacity",
            "batcontrol_stored_usable_energy_capacity",
            "sensor",
            "energy",
            "Wh",
            self.base_topic +
            "/stored_usable_energy_capacity")

        self.publish_mqtt_discovery_message(
            "Min Price Difference Relative",
            "batcontrol_min_price_difference_rel",
            "sensor",
            "monetary",
            None,
            self.base_topic +
            "/min_price_difference_rel")

        self.publish_mqtt_discovery_message(
            "Min Dynamic Price Difference",
            "batcontrol_min_dynamic_price_difference",
            "sensor",
            "monetary",
            None,
            self.base_topic +
            "/min_dynamic_price_difference")

        # diagnostic
        self.publish_mqtt_discovery_message(
            "Status",
            "batcontrol_status",
            "sensor",
            None,
            None,
            self.base_topic +
            "/status",
            command_topic=None,
            entity_category="diagnostic")

        self.publish_mqtt_discovery_message(
            "Last Evaluation",
            "batcontrol_last_evaluation",
            "sensor",
            "timestamp",
            None,
            self.base_topic +
            "/last_evaluation",
            command_topic=None,
            entity_category="diagnostic",
            options=None,
            value_template="{{ (value | int | timestamp_local) }}",
            command_template=None)

        self.publish_mqtt_discovery_message(
            "SOC Main",
            "batcontrol_soc",
            "sensor",
            "battery",
            "%",
            self.base_topic + "/SOC",
            entity_category="diagnostic")

        self.publish_mqtt_discovery_message(
            "Max Energy Capacity",
            "batcontrol_max_energy_capacity",
            "sensor",
            "energy",
            "Wh",
            self.base_topic +
            "/max_energy_capacity",
            entity_category="diagnostic")

        self.publish_mqtt_discovery_message(
            "Always Allow Discharge Limit Capacity",
            "batcontrol_always_allow_discharge_limit_capacity",
            "sensor",
            "energy",
            "Wh",
            self.base_topic +
            "/always_allow_discharge_limit_capacity",
            entity_category="diagnostic")

        self.publish_mqtt_discovery_message(
            "Stored Energy Capacity",
            "batcontrol_stored_energy_capacity",
            "sensor",
            "energy",
            "Wh",
            self.base_topic +
            "/stored_energy_capacity",
            entity_category="diagnostic")

    def send_mqtt_discovery_for_mode(self) -> None:
        """ Publish Home Assistant MQTT Auto Discovery message for mode"""
        val_templ = (
            "{% if value == '-1' %}Charge from Grid"
            "{% elif value == '0' %}Avoid Discharge"
            "{% elif value == '10' %}Discharge Allowed"
            "{% else %}Unknown"
            "{% endif %}"
        )
        cmd_templ = (
            "{% if value == 'Charge from Grid' %}-1"
            "{% elif value == 'Avoid Discharge' %}0"
            "{% elif value == 'Discharge Allowed' %}10"
            "{% else %}-1"
            "{% endif %}"
        )
        self.publish_mqtt_discovery_message(
            "Batcontrol mode",
            "batcontrol_mode",
            "select",
            None,
            None,
            self.base_topic + "/mode",
            self.base_topic + "/mode/set",
            entity_category=None,
            options=[
                "Charge from Grid",
                "Avoid Discharge",
                "Discharge Allowed"],
            value_template=val_templ,
            command_template=cmd_templ)

    # Home Assistant MQTT Auto Discovery
    # https://www.home-assistant.io/docs/mqtt/discovery/
    # item_type = sensor, switch, binary_sensor, select
    # device_class = battery, power, energy, temperature, humidity,
    #                   timestamp, signal_strength, problem, connectivity
    def publish_mqtt_discovery_message(
            self,
            name: str,
            unique_id: str,
            item_type: str,
            device_class: str,
            unit_of_measurement: str,
            state_topic: str,
            command_topic: str = None,
            entity_category: str = None,
            min_value=None,
            max_value=None,
            step_value=None,
            initial_value=None,
            options: str = None,
            value_template: str = None,
            command_template: str = None) -> None:
        """ Publish Home Assistant MQTT Auto Discovery message"""
        if self.client.is_connected():
            payload = {}
            payload["name"] = name
            payload["unique_id"] = unique_id
            payload["state_topic"] = state_topic
            if value_template:
                payload["value_template"] = value_template
            if command_topic:
                payload["command_topic"] = command_topic
            if command_template:
                payload["command_template"] = command_template
            if device_class:
                payload["device_class"] = device_class
            if unit_of_measurement:
                payload["unit_of_measurement"] = unit_of_measurement
            if item_type == "number":
                payload["min"] = min_value
                payload["max"] = max_value
                if step_value:
                    payload["step"] = step_value
                payload["mode"] = "box"
            if entity_category:
                payload["entity_category"] = entity_category
            if initial_value:
                payload["initial"] = initial_value
            if options:
                payload["options"] = options

            try:
                sw_version = importlib.metadata.version('batcontrol')
            except Exception as e:
                logger.exception(
                    "Failed to retrieve batcontrol package version: %s", e)
                sw_version = "unknown"

            device = {
                "identifiers": "Batcontrol",
                "name": "Batcontrol",
                "manufacturer": "muexxl",
                "model": "batcontrol",
                "sw_version": sw_version
            }
            payload["device"] = device
            logger.debug(
                'Sending HA AD config message for %s',
                self.auto_discover_topic +
                '/' +
                item_type +
                '/' +
                unique_id +
                '/config')
            self.client.publish(
                self.auto_discover_topic +
                '/' +
                item_type +
                '/batcontrol/' +
                unique_id +
                '/config',
                json.dumps(payload),
                retain=True)
