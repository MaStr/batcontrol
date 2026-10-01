""" Dummy inverter backend for first startup and demonstration purposes """
import logging
from .baseclass import InverterBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module')

# Dummy inverter for first startup and demonstration purposes.
# This is a minimal stub that returns static values to make batcontrol work
# out of the box without requiring real inverter configuration.
#
# All static values can be overridden in the config, which is mainly useful to
# simulate several differently sized/charged batteries without any hardware.
#
# Users should comment out this dummy type and configure a real inverter
# (like fronius_gen24) for actual operation.

DEFAULT_CAPACITY = 10000  # 10 kWh in Wh
DEFAULT_SOC = 65.0  # in percent
DEFAULT_MIN_SOC = 10  # in percent
DEFAULT_MAX_SOC = 95  # in percent


class Dummy(InverterBaseclass):
    """ Simulated inverter that returns static, configurable values.

    Used to run batcontrol without hardware - including several batteries of
    different size and charge level, which is handy to exercise InverterGroup.
    """

    def __init__(self, config):
        super().__init__(config)
        self.max_grid_charge_rate = config.get('max_grid_charge_rate', 5000)
        self.installed_capacity = config.get('capacity', DEFAULT_CAPACITY)
        # static simulation SOC in percent
        self.SOC = config.get('soc', DEFAULT_SOC)
        self.min_soc = config.get('min_soc', DEFAULT_MIN_SOC)  # in percent
        self.max_soc = config.get('max_soc', DEFAULT_MAX_SOC)  # in percent
        self.mode = 'allow_discharge'
        self.last_charge_rate = 0
        logger.info(
            'Dummy inverter initialized with static values for demonstration '
            '(capacity: %s Wh, SOC: %s %%, min_soc: %s %%, max_soc: %s %%)',
            self.installed_capacity, self.SOC, self.min_soc, self.max_soc)

    def set_mode_force_charge(self, chargerate=500):
        self.mode = 'force_charge'
        self.last_charge_rate = chargerate
        logger.info(
            'DUMMY %s: Set to force charge mode (rate: %d W)',
            self.inverter_num, chargerate)

    def set_mode_allow_discharge(self):
        self.mode = 'allow_discharge'
        self.last_charge_rate = 0
        logger.info('DUMMY %s: Set to allow discharge mode', self.inverter_num)

    def set_mode_avoid_discharge(self):
        self.mode = 'avoid_discharge'
        self.last_charge_rate = 0
        logger.info('DUMMY %s: Set to avoid discharge mode', self.inverter_num)

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """ Dummy implementation for limit battery charge mode """
        self.mode = 'limit_battery_charge'
        self.last_charge_rate = limit_charge_rate
        logger.info(
            'DUMMY %s: Limit battery charge rate to %d W',
            self.inverter_num, limit_charge_rate)

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
