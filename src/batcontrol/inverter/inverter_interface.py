""" Interface for inverter classes """

from abc import ABC, abstractmethod

class InverterInterface(ABC):
    """ Interface for Inverter classes """
    @abstractmethod
    def __init__(self, dict_config: dict):
        """ Initialize the Inverter class """

    @abstractmethod
    def set_mode_force_charge(self, chargerate: float):
        """ Set the inverter to force charge mode """

    @abstractmethod
    def set_mode_avoid_discharge(self):
        """ Set the inverter to allow discharge mode """

    @abstractmethod
    def set_mode_allow_discharge(self):
        """ Set the inverter to avoid discharge mode """

    @abstractmethod
    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """ Set the inverter to limit PV charging while allowing discharge

        Args:
            limit_charge_rate: Maximum charge rate in W (0 = no charging)
        """

    @abstractmethod
    def get_stored_energy(self) -> float:
        """ Get the stored energy in the inverter.
        This value does not account MIN_SOC and MAX_SOC values.
        Returns:
            float: The stored energy in the inverter in Wh.
        """

    @abstractmethod
    def get_stored_usable_energy(self) -> float:
        """ Get the stored energy in the inverter.
        It reduces the amount by the minimum SOC.
        Returns:
            float: The stored energy in the inverter in Wh.
        """

    @abstractmethod
    def get_capacity(self) -> float:
        """ Get the maximum capacity of the inverter.
        This value does not account MIN_SOC and MAX_SOC values.
        Returns:
            float: The maximum capacity of the inverter in Wh.
        """

    @abstractmethod
    def get_free_capacity(self) -> float:
        """ Get the free capacity of the inverter.
            This value is reduced by MAX_SOC
        Returns:
            float: The free capacity of the inverter in Wh.
        """

    @abstractmethod
    def get_max_capacity(self) -> float:
        """ Get the maximum capacity of the inverter.
        This value is reduced by MAX_SOC.
        Returns:
            float: The maximum capacity of the inverter in Wh.
        """
    @abstractmethod
    def get_SOC(self) -> float:  # pylint: disable=invalid-name
        """ Get the state of charge of the inverter in percentage.
        Returns:
            float: The SOC of the inverter in percentage.
        """

    @abstractmethod
    def activate_mqtt(self, api_mqtt_api: object):
        """ Activate the MQTT connection for the inverter """

    @abstractmethod
    def refresh_api_values(self):
        """ Refresh the values for the API """

    @abstractmethod
    def shutdown(self):
        """ Class to bring the inverter into a consistent state while
            batcontrol is shutting down """

