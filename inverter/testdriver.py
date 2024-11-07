import logging
from .baseclass import InverterBaseclass

logger = logging.getLogger('__main__')
logger.info(f'[Testdriver] loading module ')

#from .fronius import InverterBaseclass

# Testdriver to simulate a inverter for local testing.
#
# Following values can be set via MQTT:
# - SOC (int): State of charge in percent
#            : <mqtt_topic>/inverters/0/SOC/set


class Testdriver(InverterBaseclass):
    def __init__(self, max_grid_charge:float):
        self.max_grid_charge_rate=max_grid_charge
        self.installed_capacity=11000 # in Wh
        self.SOC=69.0 # static simulation SOC in percent
        self.min_soc=8 # in percent
        self.max_soc=100 # in percent
        self.mode='allow_discharge'
        self.mqtt_api = None

    def set_mode_force_charge(self,chargerate=500):
        self.mode='force_charge'

    def set_mode_allow_discharge(self):
        self.mode='allow_discharge'

    def set_mode_avoid_discharge(self):
        self.mode='avoid_discharge'

    def get_stored_energy(self):
        current_soc = self.get_SOC()
        capa = self.get_capacity()
        energy = (current_soc - self.min_soc) / 100 * capa
        return energy

    def get_free_capacity(self):
        current_soc = self.get_SOC()
        capa = self.get_capacity()
        free_capa = (self.max_soc - current_soc) / 100 * capa
        return free_capa

    def get_capacity(self):
        return self.installed_capacity

    def get_max_capacity(self):
        return self.max_soc/100*self.get_capacity()

    def get_usable_capacity(self):
        usable_capa = (self.max_soc-self.min_soc)/100*self.get_capacity()
        return usable_capa

    def get_SOC(self):
        return self.SOC

    def api_set_SOC(self, SOC:int):
        if SOC < 0 or SOC > 100:
            logger.warning(f'[BatCtrl] testdriver API: Invalid SOC {SOC}')
            return
        logger.info(f'[BatCtrl] testdriver API: Setting SOC: {SOC}%')
        self.SOC = SOC

    def activate_mqtt(self, api_mqtt_api):  # no type here to prevent the need of loading mqtt_api
        import mqtt_api
        self.mqtt_api = api_mqtt_api
        # /set is appended to the topic
        self.mqtt_api.register_set_callback(self._get_mqtt_topic() + 'SOC', self.api_set_SOC, int)

    def refresh_api_values(self):
        if self.mqtt_api:
            self.mqtt_api.generic_publish(self._get_mqtt_topic() + 'SOC', self.get_SOC())
            self.mqtt_api.generic_publish(self._get_mqtt_topic() + 'mode', self.mode)
            self.mqtt_api.generic_publish(self._get_mqtt_topic() + 'stored_energy', self.get_stored_energy())
            self.mqtt_api.generic_publish(self._get_mqtt_topic() + 'free_capacity', self.get_free_capacity())
            self.mqtt_api.generic_publish(self._get_mqtt_topic() + 'max_capacity', self.get_max_capacity())