# API to publish data from batcontrol to MQTT for further processing+visualization
#
#  base_topic : string  # topic to publish to
#            /status  : online/offline  # is batcontrol running?
#
#            /evaluation_intervall : int  # intervall in seconds
#            /last_evaluation      : int  # timestamp of last evaluation
#
#            /mode    : -1 = charge from grid , 0 = avoid discharge , 10 = discharge allowed
#
#            /max_capacity : float  # Maximum capacity of battery in Wh
#
#            /max_charging_from_grid_limit : float  # Charge limit in 0.1-1
#            /max_charging_from_grid_limit_percent : float  # Charge limit in %
#
#            /always_allow_discharge_limit : float  # Always Discharge limit until 0.1-1
#            /always_allow_discharge_limit_percent : float  # Always Discharge limit in %
#            /always_allow_discharge_limit_capacity : float  # Always discharge limit in Wh
#                                                    (max_capacity * always_allow_discharge_limit)
#
#            /charge_rate : float  # Charge rate in W
#
#            # Battery values in absoulte values, which might be across multiple batteries.
#            /max_energy_capacity      : float  # Maximum capacity of battery in Wh
#            /stored_energy_capacity   : float  # Energy stored in battery in Wh
#
#            /reserved_energy_capacity : float  # Estimated energy reserved for discharge in Wh
#                                                   in the next hours
#
#            /SOC                      : float  # State of charge in % calculated from
#                                                   stored_energy_capacity / max_energy_capacity
#
#            /min_price_difference     : float  # Minimum price difference in EUR
#
#
#    Following statistical arrays as JSON Arrays
#            /FCST/production        # Forecasted production in W
#            /FCST/consumption       # Forecasted consumption in W
#            /FCST/prices            # Forecasted price in EUR
#            /FCST/net_consumption   # Forecasted net consumption in W
#
#            Timestamps in unix time
#            JSON schema:
#            {
#                "data" : [
#                    { "time_start" : int, "value" : float, "time_end" : int },
#                  ]
#            }
#
# Implemented Input-API:
#
#   Values are expected as "string"  and will be converted to the correct type
#
#   Changing these values will stop evaluation for one interation.
#   Make sure the update is fast enough to not miss the next evaluation.
#
#    /mode/set        : int  # set mode
#           -1 = charge from grid ,
#            0 = avoid discharge ,
#           10 = discharge allowed
#    /charge_rate/set : int  # set charge rate in W , sets mode to -1
#    /always_allow_discharge_limit/set : float  # set always discharge limit in 0.1-1
#    /max_charging_from_grid_limit/set : float  # set charge limit in NOTin 0-1
#    /min_price_difference/set : float  # set minimum price difference in EUR
#

import time
import json
import logging
import paho.mqtt.client as mqtt
import numpy as np

logger = logging.getLogger('__main__')
logger.info(f'[MQTT] loading module ')

mqtt_api = None

## Callbacks go through
def on_connect( client, userdata, flags, rc ):
    logger.info(f'[MQTT] Connected with result code {rc}')
    # Make public, that we are running.
    client.publish(mqtt_api.base_topic + '/status', 'online', retain=True)

def on_message_msgs(mosq, obj, msg):
    # This callback will only be called for messages with topics that match
    # $SYS/broker/messages/#
    print("MESSAGES: " + msg.topic + " " + str(msg.qos) + " " + str(msg.payload))

class MQTT_API(object):
    SET_SUFFIX = '/set'
    def __init__(self, config:dict):
        self.config=config
        self.base_topic = config['topic']

        self.callbacks = {}

        self.client = mqtt.Client()
        if 'logger' in config and config['logger'] == True:
            self.client.enable_logger(logger)

        if 'username' in config and 'password' in config:
            self.client.username_pw_set(config['username'], config['password'])

        self.client.will_set(self.base_topic + '/status', 'offline', retain=True)
        global mqtt_api
        mqtt_api = self

        # TLS , not tested yet
        if config['tls'] == True:
            self.client.tls_set(
                config['tls']['ca_certs'],
                config['tls']['certfile'],
                config['tls']['keyfile'],
                cert_reqs=config['tls']['cert_reqs'],
                tls_version=config['tls']['tls_version'],
                ciphers=config['tls']['ciphers']
            )

        self.client.on_connect = on_connect
        self.client.loop_start()

        self.client.connect(config['broker'], config['port'], 60)

        return

    def wait_ready(self) -> bool:
        retry = 30
        # Check if we are connected and wait for it
        while self.client.is_connected() == False:
            retry -= 1
            if retry == 0:
                logger.error(f'[MQTT] Could not connect to MQTT Broker')
                return False
            logger.info(f'[MQTT] Waiting for connection')
            time.sleep(1)

        return True

    def _handle_message(self, client, userdata, message):
        logger.debug(f'[MQTT] Received message on {message.topic}')
        if message.topic in self.callbacks:
            try:
                self.callbacks[message.topic]['function'](
                    self.callbacks[message.topic]['convert'](message.payload)
                )
            except Exception as e:
                logger.error(f'[MQTT] Error in callback {message.topic} : {e}')
        else:
            logger.warning(f'[MQTT] No callback registered for {message.topic}')
        return

    def register_set_callback(self, topic:str,  callback:callable, convert: callable) -> None:
        topic_string = self.base_topic + "/" + topic + MQTT_API.SET_SUFFIX
        logger.debug(f'[MQTT] Registering callback for {topic_string}')
                # set api endpoints, generic subscription
        self.callbacks[topic_string] = { 'function' : callback , 'convert' : convert }
        self.client.subscribe(topic_string)
        self.client.message_callback_add(topic_string , self._handle_message)
        return

    def publish_mode(self, mode:int) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/mode', mode)
        return

    def publish_charge_rate(self, rate:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/charge_rate', rate)
        return

    def publish_production(self, production:np.ndarray, timestamp:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/FCST/production',
                json.dumps(self._create_forecast(production, timestamp))
            )
        return

    def _create_forecast(self, forecast:np.ndarray, timestamp:float) -> dict:
        # Take timestamp and reduce it to the first second of the hour
        now = timestamp - (timestamp % 3600)

        list = []
        for h in range(0, len(forecast)):
            # nächste stunde nach now
            list.append (
                { 'time_start' :now + h * 3600,
                  'value' :   forecast[h],
                  'time_end' : now -1  + ( h+1) *3600
                }
            )

        data = { 'data' : list }
        return data


    def publish_consumption(self, consumption:np.ndarray, timestamp:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/FCST/consumption',
                json.dumps(self._create_forecast(consumption,timestamp))
            )
        return

    def publish_prices(self, price:np.ndarray ,timestamp:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/FCST/prices',
                json.dumps(self._create_forecast(price,timestamp))
            )
        return

    def publish_net_consumption(self, net_consumption:np.ndarray, timestamp:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/FCST/net_consumption',
                json.dumps(self._create_forecast(net_consumption,timestamp))
            )
        return

    def publish_SOC(self, soc:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/SOC', f'{int(soc):03}')
        return

    def publish_stored_energy_capacity(self, stored_energy:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/stored_energy_capacity', f'{stored_energy:.1f}')
        return

    def publish_reserved_energy_capacity(self, reserved_energy:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/reserved_energy_capacity',
                f'{reserved_energy:.1f}'
            )
        return

    def publish_always_allow_discharge_limit_capacity(self, discharge_limit:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit_capacity',
                f'{discharge_limit:.1f}'
            )
        return

    def publish_always_allow_discharge_limit(self, allow_discharge_limit:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit',
                f'{allow_discharge_limit:.2f}'
            )
            self.client.publish(
                self.base_topic + '/always_allow_discharge_limit_percent',
                f'{allow_discharge_limit * 100:.0f}'
            )
        return

    def publish_max_charging_from_grid_limit(self, charge_limit:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/max_charging_from_grid_limit_percent',
                f'{charge_limit * 100:.0f}'
            )
            self.client.publish(
                self.base_topic + '/max_charging_from_grid_limit',
                f'{charge_limit:.2f}'
            )
        return

    def publish_min_price_difference(self, min_price_differences:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/min_price_differences',
                f'{min_price_differences:.3f}'
            )
        return

    def publish_max_energy_capacity(self, max_capacity:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(
                self.base_topic + '/max_energy_capacity',
                f'{max_capacity:.1f}'
            )
        return

    def publish_evaluation_intervall(self, intervall:int) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/evaluation_intervall', f'{intervall:.0f}')
        return

    def publish_last_evaluation_time(self, timestamp:float) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/last_evaluation', f'{timestamp:.0f}')
        return

    # For depended APIs like the Fronius Inverter classes, which is not directly batcontrol.
    def generic_publish(self, topic:str, value:str) -> None:
        if self.client.is_connected() == True:
            self.client.publish(self.base_topic + '/' + topic, value)
        return
