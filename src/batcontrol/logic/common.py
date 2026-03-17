""" This module contains battery control logic, that is not specific to control strategies.
It includes the handling of:

  - is always allowed discharge
  - charge_rate multiplier
  """
import logging


# Minimum charge rate to controlling loops between charging and
#   self discharge.
# 500W is Fronius' internal value for forced recharge.
MIN_CHARGE_RATE = 500


logger = logging.getLogger(__name__)

# Singleton pattern to ensure only one instance of CommonLogic exists
class CommonLogic:
    """ General logic for battery control that is not specific to control strategies. """

    _instance = None  # Singleton instance
    charge_rate_multiplier: float
    always_allow_discharge_limit: float
    max_capacity: float  # Maximum capacity of the battery in Wh
    min_charge_energy: float = 100  # Minimum amount of energy before charging from grid in Wh

    @classmethod
    def get_instance(cls, charge_rate_multiplier=1.1,
                     always_allow_discharge_limit=0.9,
                     max_capacity=10000,
                     min_charge_energy=100) -> "CommonLogic":
        """ Get the singleton instance of CommonLogic. """
        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls._instance.initialize(charge_rate_multiplier,
                                 always_allow_discharge_limit,
                                 max_capacity,
                                 min_charge_energy)
        return cls._instance

    def __init__(self, *args, **kwargs):
        """ This method is overridden to prevent direct instantiation. """
        if CommonLogic._instance is not None:
            raise RuntimeError(
                "Use CommonLogic.get_instance() instead of constructor")
        self.initialize(*args, **kwargs)

    def initialize(self, charge_rate_multiplier=1.1,
               always_allow_discharge_limit=0.9,
               max_capacity=10000,
               min_charge_energy=100):
        """ Private initialization method. """
        self.charge_rate_multiplier = charge_rate_multiplier
        self.always_allow_discharge_limit = always_allow_discharge_limit
        self.max_capacity = max_capacity
        self.min_charge_energy = min_charge_energy

    def set_charge_rate_multiplier(self, multiplier: float):
        """ Set the charge rate multiplier. """
        logger.debug('Setting charge rate multiplier to %s', multiplier)
        self.charge_rate_multiplier = multiplier

    def set_always_allow_discharge_limit(self, limit: float):
        """ Set the always allowed discharge limit. """
        logger.debug(
            'Setting always allowed discharge limit to %s', limit)
        self.always_allow_discharge_limit = limit

    def get_always_allow_discharge_limit(self) -> float:
        """ Get the always allowed discharge limit. """
        return self.always_allow_discharge_limit

    def is_discharge_always_allowed_soc(self, soc: float) -> bool:
        """ Check if discharge is always allowed based on the state of charge (SOC).
        Args:
            soc (float): State of charge as a percentage (0-100).
        Returns:
            bool: True if discharge is always allowed, False otherwise."""
        if soc/100 >= self.always_allow_discharge_limit:
            logger.debug('Discharge is always allowed for SOC: %s', soc)
            return True
        logger.debug('Discharge is NOT always allowed for SOC: %s', soc)
        return False

    def is_discharge_always_allowed_capacity(self, capacity: float) -> bool:
        """ Check if discharge is always allowed based on the battery capacity.
        Args:
            capacity (float): Battery capacity in Wh.
        Returns:
            bool: True if discharge is always allowed, False otherwise."""

        if capacity >= self.max_capacity * self.always_allow_discharge_limit:
            logger.debug(
                'Discharge is \'always allowed\' for current capacity: %.0f Wh', round(capacity,0))
            return True

        logger.debug(
            'Discharge is NOT \'always allowed\' for current capacity: %.0f Wh', round(capacity,0))
        return False

    def is_charging_above_minimum(self, needed_energy: float) -> bool:
        """ Check if charging from grid is allowed based on the needed energy.
        Args:
            needed_energy (float): Needed energy in Wh.
        Returns:
            bool: True if charging from grid is allowed, False otherwise."""
        if needed_energy >= self.min_charge_energy:
            return True

        logger.debug(
            'Charging needed recharge energy is below threshold(%.0f): %.0f Wh',
            round(self.min_charge_energy, 0),
            round(needed_energy, 0))
        return False

    def calculate_charge_rate(self, charge_rate: float) -> int:
        """ Calculate the charge rate based on the charge rate multiplier.
        Args:
            charge_rate (float): The initial charge rate in W.
        Returns:
            int: The adjusted charge rate in W."""
        logger.debug('Calculating charge rate: %.1f W', charge_rate)
        adjusted_charge_rate = charge_rate * self.charge_rate_multiplier
        if adjusted_charge_rate < MIN_CHARGE_RATE:
            logger.debug(
                'Charge rate increased to minimum %d W from %.1f W',
                MIN_CHARGE_RATE, adjusted_charge_rate)
            adjusted_charge_rate = MIN_CHARGE_RATE

        adjusted_charge_rate = int(round(adjusted_charge_rate, 0))
        logger.debug('Adjusted charge rate: %d W', adjusted_charge_rate)
        return adjusted_charge_rate
