from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import datetime
import numpy as np

@dataclass
class CalculationInput:
    """ Input for the calculation """
    production: np.ndarray
    consumption: np.ndarray
    prices: dict
    stored_energy: float  # Stored energy in Wh
    stored_usable_energy: float # Usable energy in Wh (reduced by MIN_SOC)
    free_capacity: float  # Free capacity in Wh (reduced by MAX_SOC)

@dataclass
class CalculationParameters:
    """ Calculations from Battery control configuration """
    max_charging_from_grid_limit: float
    min_price_difference: float
    min_price_difference_rel: float
    max_capacity: float # Maximum capacity of the battery in Wh (excludes MAX_SOC)
    # Peak shaving parameters
    peak_shaving_enabled: bool = False
    peak_shaving_allow_full_after: int = 14  # Hour (0-23)
    # Operating mode:
    #   'time'     - limit by target hour only (allow_full_battery_after)
    #   'price'    - limit by cheap-slot reservation only (price_limit required)
    #   'combined' - both limits active, stricter one wins
    peak_shaving_mode: str = 'combined'
    # Slots where price (Euro/kWh) is at or below this value are treated as
    # cheap PV windows.  -1 or any numeric value accepted; None disables
    # price-based component.
    peak_shaving_price_limit: Optional[float] = None

    def __post_init__(self):
        if not 0 <= self.peak_shaving_allow_full_after <= 23:
            raise ValueError(
                f"peak_shaving_allow_full_after must be 0-23, "
                f"got {self.peak_shaving_allow_full_after}"
            )
        valid_modes = ('time', 'price', 'combined')
        if self.peak_shaving_mode not in valid_modes:
            raise ValueError(
                f"peak_shaving_mode must be one of {valid_modes}, "
                f"got '{self.peak_shaving_mode}'"
            )
        if (self.peak_shaving_price_limit is not None
                and not isinstance(self.peak_shaving_price_limit, (int, float))):
            raise ValueError(
                f"peak_shaving_price_limit must be numeric or None, "
                f"got {type(self.peak_shaving_price_limit).__name__}"
            )

@dataclass
class CalculationOutput:
    """ Output from the calculation besides the InverterControlSettings """
    reserved_energy: float = 0.0
    required_recharge_energy: float = 0.0
    min_dynamic_price_difference: float = 0.05

@dataclass
class InverterControlSettings:
    """ Result from Calculation what to do on the current interval"""
    allow_discharge: bool
    # Force charge mode is used to charge the battery from grid
    charge_from_grid: bool
    charge_rate: int
    # Limit charge rate (via PV) to a certain value Wh
    # -1 means no limit, 0 means no charging
    limit_battery_charge_rate: int


class LogicInterface(ABC):
    """ Interface for Logic classes """

    @abstractmethod
    def __init__(self, timezone):
        """ Initialize the Logic class """
        pass

    @abstractmethod
    def set_calculation_parameters(self, parameters: CalculationParameters):
        """ Set the calculation parameters for the logic """
        pass

    @abstractmethod
    def calculate(self, input_data: CalculationInput, calc_timestamp:datetime) -> bool:
        """ Calculate the inverter control settings based on the input data """
        pass

    @abstractmethod
    def get_calculation_output(self) -> CalculationOutput:
        """ Get the calculation output from the last calculation """
        pass

    @abstractmethod
    def get_inverter_control_settings(self) -> InverterControlSettings:
        """ Get the inverter control settings from the last calculation """
        pass
