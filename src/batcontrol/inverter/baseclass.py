""" Parent Class for implementing common functions for all inverters """
from .inverter_interface import InverterInterface

DEFAULT_MIN_SOC = 5
DEFAULT_MAX_SOC = 100


class InverterBaseclass(InverterInterface):
    def __init__(self, config):
        self.min_soc = -1
        self.max_soc = -1
        self.mqtt_api = None
        self.capacity = -1
        self.inverter_num = 0

    def get_capacity(self) -> float:
        """ Dummy implementation """
        raise RuntimeWarning("get_capacity not implemented!")

    def get_SOC(self) -> float:   # pylint: disable=invalid-name
        """ Dummy implementation """
        raise RuntimeWarning("get_capacity not implemented!")

    def get_designed_capacity(self) -> float:
        """ Returns the designed maximum capacity of the battery in kWh,
            which does not include MIN_SOC , MAX_SOC or other restrictions.
        """
        return self.get_capacity()

    def get_stored_energy(self) -> float:
        """ Returns the stored energy in the battery in kWh """
        current_soc = self.get_SOC()
        capa = self.get_capacity()
        energy = current_soc/100*capa
        if energy < 0:
            return 0
        return energy

    def get_stored_usable_energy(self) -> float:
        """ Returns the stored energy in the battery in kWh which can be used .
            It reduces the amount by the minimum SOC.
        """
        current_soc = self.get_SOC()
        capa = self.get_capacity()
        energy = (current_soc-self.min_soc)/100*capa
        if energy < 0:
            return 0
        return energy

    def get_usable_capacity(self) -> float:
        """ Returns Capacity which can be used from Battery.
            This value is reduced by MIN_SOC & MAX_SOC limitations.
        """
        usable_capa = (self.max_soc-self.min_soc)/100*self.get_capacity()
        return usable_capa

    def get_max_capacity(self) -> float:
        """ Returns Capacity reduced by MAX_SOC """
        return self.max_soc/100*self.get_capacity()

    def get_free_capacity(self) -> float:
        """ Return Capacity Wh to be chargeable
            this value is reduced by MAX_SOC.
        """
        current_soc = self.get_SOC()
        capa = self.get_capacity()
        free_capa = (self.max_soc-current_soc)/100*capa
        return free_capa

    # Used to implement the mqtt basic topic.
    def get_mqtt_inverter_topic(self) -> str:
        """ Used to implement the mqtt basic topic."""
        return f'inverters/{self.inverter_num}/'

    def refresh_api_values(self):
        if self.mqtt_api:
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic() + 'SOC', self.get_SOC())
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic() + 'stored_energy', self.get_stored_energy())
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic() + 'stored_usable_energy', self.get_stored_usable_energy())
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic() + 'free_capacity', self.get_free_capacity())
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic() + 'max_capacity', self.get_max_capacity())

    def publish_inverter_discovery_messages(self):
        """Publish Home Assistant MQTT Auto Discovery messages for common inverter sensors"""
        if self.mqtt_api:
            topic = self.get_mqtt_inverter_topic()
            base_topic = self.mqtt_api.base_topic

            # Common inverter sensors
            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} SOC",
                f"batcontrol_inverter_{self.inverter_num}_SOC",
                "sensor", "battery", "%",
                base_topic + "/" + topic + "SOC",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Stored Energy",
                f"batcontrol_inverter_{self.inverter_num}_stored_energy",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "stored_energy",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Stored Usable Energy",
                f"batcontrol_inverter_{self.inverter_num}_stored_usable_energy",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "stored_usable_energy",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Free Capacity",
                f"batcontrol_inverter_{self.inverter_num}_free_capacity",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "free_capacity",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Max Capacity",
                f"batcontrol_inverter_{self.inverter_num}_max_capacity",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "max_capacity",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Usable Capacity",
                f"batcontrol_inverter_{self.inverter_num}_usable_capacity",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "usable_capacity",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Capacity",
                f"batcontrol_inverter_{self.inverter_num}_capacity",
                "sensor", "energy", "Wh",
                base_topic + "/" + topic + "capacity",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Min SOC",
                f"batcontrol_inverter_{self.inverter_num}_min_soc",
                "sensor", "battery", "%",
                base_topic + "/" + topic + "min_soc",
                entity_category="diagnostic")

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Max SOC",
                f"batcontrol_inverter_{self.inverter_num}_max_soc",
                "sensor", "battery", "%",
                base_topic + "/" + topic + "max_soc",
                entity_category="diagnostic")

    def shutdown(self):
        pass
