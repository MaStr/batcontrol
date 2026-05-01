import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import datetime
import numpy as np

logger = logging.getLogger(__name__)

# Shared tuple of valid peak-shaving operating modes.
PEAK_SHAVING_VALID_MODES = ('time', 'price', 'combined')


@dataclass
class PeakShavingConfig:
    """ Holds peak shaving configuration parameters, initialized from the config dict.

    Range/type validation runs in ``__post_init__``. The "combined mode without
    price_limit" fallback warning is emitted in :py:meth:`from_config` only,
    so it fires once at config load and not on every ``dataclasses.replace``
    in the per-evaluation build path.
    """
    enabled: bool = False
    mode: str = 'combined'
    allow_full_battery_after: int = 14
    price_limit: Optional[float] = None

    def __post_init__(self):
        """Validate configuration values and raise ValueError with a clear,
        config-key-based message on invalid input."""
        if self.mode not in PEAK_SHAVING_VALID_MODES:
            raise ValueError(
                f"peak_shaving.mode must be one of "
                f"{PEAK_SHAVING_VALID_MODES}, got '{self.mode}'"
            )
        if not isinstance(self.allow_full_battery_after, int) \
                or isinstance(self.allow_full_battery_after, bool):
            raise ValueError(
                f"peak_shaving.allow_full_battery_after must be an integer, "
                f"got {type(self.allow_full_battery_after).__name__}"
            )
        if not 0 <= self.allow_full_battery_after <= 23:
            raise ValueError(
                f"peak_shaving.allow_full_battery_after must be between "
                f"0 and 23, got {self.allow_full_battery_after}"
            )
        if self.price_limit is not None and (
                isinstance(self.price_limit, bool)
                or not isinstance(self.price_limit, (int, float))):
            raise ValueError(
                f"peak_shaving.price_limit must be numeric or None, "
                f"got {type(self.price_limit).__name__}"
            )

    @classmethod
    def from_config(cls, config: dict) -> 'PeakShavingConfig':
        """ Create a PeakShavingConfig instance from a configuration dict.

        Emits a one-time warning when peak shaving is enabled in 'combined'
        mode without a configured ``price_limit``: the price component is
        disabled in that case and behaviour falls back to time-only.
        """
        ps = config.get('peak_shaving', {})
        price_limit_raw = ps.get('price_limit', None)
        if price_limit_raw is None or isinstance(price_limit_raw, bool):
            # ``None`` stays ``None``; bool is rejected by __post_init__ with a
            # key-prefixed message. Skip float() so we do not lose the type info.
            price_limit = price_limit_raw
        else:
            try:
                price_limit = float(price_limit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"peak_shaving.price_limit must be numeric or None, "
                    f"got {price_limit_raw!r}"
                ) from exc
        instance = cls(
            enabled=ps.get('enabled', False),
            mode=ps.get('mode', 'combined'),
            allow_full_battery_after=ps.get('allow_full_battery_after', 14),
            price_limit=price_limit,
        )
        if instance.enabled and instance.mode == 'combined' \
                and instance.price_limit is None:
            logger.warning(
                "peak_shaving.mode='combined' but no peak_shaving.price_limit "
                "configured: the price component is disabled; falling back "
                "to time-only behaviour. Set a numeric price_limit or change "
                "mode to 'time' to silence this warning."
            )
        return instance


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
    # Optional minimum SoC target used when grid charging is already economical.
    # None disables the target. Values are ratios from 0.0 to 1.0.
    min_grid_charge_soc: Optional[float] = None
    # Expert option: also preserve the target as reserved energy during
    # cheap/pre-expensive windows.
    preserve_min_grid_charge_soc: bool = False
    # Peak shaving sub-configuration. evcc may set ``enabled=False`` for a
    # single calculation cycle via ``dataclasses.replace`` in core.py.
    peak_shaving: PeakShavingConfig = field(default_factory=PeakShavingConfig)

    def __post_init__(self):
        if self.min_grid_charge_soc is not None:
            if (isinstance(self.min_grid_charge_soc, bool)
                    or not isinstance(self.min_grid_charge_soc, (int, float))):
                raise ValueError(
                    f"min_grid_charge_soc must be numeric between 0 and 1 or None, "
                    f"got {type(self.min_grid_charge_soc).__name__}"
                )
            if not 0 <= self.min_grid_charge_soc <= 1:
                raise ValueError(
                    f"min_grid_charge_soc must be between 0 and 1 or None, "
                    f"got {self.min_grid_charge_soc}"
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
