import logging
from .baseclass import InverterBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module')

# Dummy inverter for first startup and demonstration purposes.
# This is a minimal stub that returns static values to make batcontrol work
# out of the box without requiring real inverter configuration.
#
# Users should comment out this dummy type and configure a real inverter
# (like fronius_gen24) for actual operation.


class Dummy(InverterBaseclass):
    def __init__(self, config):
        super().__init__(config)
        self.max_grid_charge_rate = config.get('max_grid_charge_rate', 5000)
        self.installed_capacity = 10000  # 10 kWh in Wh
        self.SOC = 65.0  # static simulation SOC in percent
        self.min_soc = 10  # in percent
        self.max_soc = 95  # in percent
        self.mode = 'allow_discharge'
        logger.info('Dummy inverter initialized with static values for demonstration')

    def set_mode_force_charge(self, chargerate=500):
        self.mode = 'force_charge'
        logger.debug('Dummy inverter: Set to force charge mode (rate: %dW)', chargerate)

    def set_mode_allow_discharge(self):
        self.mode = 'allow_discharge'
        logger.debug('Dummy inverter: Set to allow discharge mode')

    def set_mode_avoid_discharge(self):
        self.mode = 'avoid_discharge'
        logger.debug('Dummy inverter: Set to avoid discharge mode')

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """ Dummy implementation for limit battery charge mode """
        self.mode = 'limit_battery_charge'
        logger.info('DUMMY: Limit battery charge rate to %d W', limit_charge_rate)

    def get_capacity(self):
        return self.installed_capacity

    def get_SOC(self):
        return self.SOC

    def activate_mqtt(self, api_mqtt_api):
        # Dummy inverter doesn't support MQTT for simplicity
        logger.debug('Dummy inverter: MQTT activation ignored (not supported)')

    def refresh_api_values(self):
        # No-op for dummy inverter - no values to refresh
        logger.debug('Dummy inverter: refresh_api_values called (no action needed)')

    def shutdown(self):
        logger.info('Dummy inverter: Shutdown called (no action needed)')