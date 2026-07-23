import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any
import datetime
import numpy as np

logger = logging.getLogger(__name__)

# Shared tuple of valid peak-shaving operating modes.
PEAK_SHAVING_VALID_MODES = ('time', 'price', 'combined')


def _default_grid_charge_target_config():
    """Create default grid-charge target strategy config lazily."""
    from .grid_charge_target import GridChargeTargetConfig  # pylint: disable=import-outside-toplevel
    return GridChargeTargetConfig()


@dataclass
class PeakShavingConfig:  # pylint: disable=too-many-instance-attributes
    """ Holds peak shaving configuration parameters, initialized from the config dict.

    Range/type validation runs in ``__post_init__``. The "combined mode without
    price_limit" fallback warning is emitted in :py:meth:`from_config` only,
    so it fires once at config load and not on every ``dataclasses.replace``
    in the per-evaluation build path.

    ``mode`` is DEPRECATED in favour of explicit per-rule switches
    (``time_active``, ``price_active``, ``solar_cap_active``); see
    :py:meth:`from_config` for the mapping and
    docs/development/solar-limit-evaluation.md for the rationale.
    """
    enabled: bool = False
    mode: str = 'combined'
    allow_full_battery_after: int = 14
    price_limit: Optional[float] = None
    # ``None`` is a resolution sentinel, not a valid external value: when
    # left unset, __post_init__ derives it from ``mode`` (backward
    # compatibility for code that still constructs this dataclass directly
    # with ``mode=`` instead of the explicit switches). Externally these
    # fields always behave as booleans defaulting to True (i.e. equivalent
    # to today's 'combined' mode) once construction has completed.
    time_active: Optional[bool] = None
    price_active: Optional[bool] = None
    solar_cap_active: bool = False
    # Feed-in power limit in W for the solar_cap rule. 0 = neutral (rule has
    # no effect even if solar_cap_active is true).
    feed_in_limit_w: float = 0.0
    # Safety factor >= 1.0 applied to the forecast surplus for the solar_cap
    # rule's reservation and floor sizing.
    feed_in_limit_headroom: float = 1.0

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
        if (isinstance(self.feed_in_limit_w, bool)
                or not isinstance(self.feed_in_limit_w, (int, float))):
            raise ValueError(
                f"peak_shaving.feed_in_limit_w must be numeric, "
                f"got {type(self.feed_in_limit_w).__name__}"
            )
        if self.feed_in_limit_w < 0:
            raise ValueError(
                f"peak_shaving.feed_in_limit_w must be >= 0, "
                f"got {self.feed_in_limit_w}"
            )
        if (isinstance(self.feed_in_limit_headroom, bool)
                or not isinstance(self.feed_in_limit_headroom, (int, float))):
            raise ValueError(
                f"peak_shaving.feed_in_limit_headroom must be numeric, "
                f"got {type(self.feed_in_limit_headroom).__name__}"
            )
        if self.feed_in_limit_headroom < 1.0:
            raise ValueError(
                f"peak_shaving.feed_in_limit_headroom must be >= 1.0, "
                f"got {self.feed_in_limit_headroom}"
            )
        # Resolve the deprecated ``mode`` into the explicit switches when the
        # caller did not set them explicitly (see the field comment above).
        # ``from_config`` always passes concrete booleans, so this path only
        # matters for direct dataclass construction (tests, expert use).
        if self.time_active is None:
            self.time_active = self.mode in ('time', 'combined')
        if self.price_active is None:
            self.price_active = self.mode in ('price', 'combined')

    @classmethod
    def from_config(cls, config: dict) -> 'PeakShavingConfig':
        """ Create a PeakShavingConfig instance from a configuration dict.

        Emits a one-time warning when peak shaving is enabled in 'combined'
        mode without a configured ``price_limit``: the price component is
        disabled in that case and behaviour falls back to time-only.

        ``mode`` is deprecated in favour of the explicit switches
        ``time_active``/``price_active``/``solar_cap_active``. If any switch
        key is present in the config, the switches win; a ``mode`` key
        present alongside them has no effect on the switches (warning
        logged), but its value is still validated -- an invalid ``mode``
        raises ValueError so configuration typos fail fast instead of being
        silently swallowed. If only ``mode`` is present, it is mapped onto
        the switches (``time`` -> ``time_active=True, price_active=False``;
        ``price`` -> ``price_active=True, time_active=False``;
        ``combined`` -> both True) and a debug-level deprecation notice is
        logged (deliberately below WARNING so unmigrated configs keep
        loading quietly). If neither is present, the defaults apply
        (equivalent to ``combined``).
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

        mode = ps.get('mode', 'combined')
        switch_keys = ('time_active', 'price_active', 'solar_cap_active')
        switches_present = any(key in ps for key in switch_keys)
        mode_present = 'mode' in ps

        if switches_present:
            if mode_present:
                logger.warning(
                    "peak_shaving.mode is deprecated and ignored because "
                    "explicit switches (time_active/price_active/"
                    "solar_cap_active) are configured. Remove peak_shaving.mode "
                    "from the configuration to silence this warning."
                )
            time_active = ps.get('time_active', True)
            price_active = ps.get('price_active', True)
        elif mode_present:
            # Deprecation notice at debug level: the existing test suite
            # (and users who have not yet migrated) expect a plain mode=
            # config to load silently at WARNING level; the combined+missing
            # price_limit fallback below still warns as before.
            logger.debug(
                "peak_shaving.mode is deprecated; use the explicit switches "
                "time_active/price_active/solar_cap_active instead. Mapping "
                "mode='%s' onto the switches for now.", mode
            )
            time_active = mode in ('time', 'combined')
            price_active = mode in ('price', 'combined')
        else:
            time_active = True
            price_active = True

        instance = cls(
            enabled=ps.get('enabled', False),
            mode=mode,
            allow_full_battery_after=ps.get('allow_full_battery_after', 14),
            price_limit=price_limit,
            time_active=time_active,
            price_active=price_active,
            solar_cap_active=ps.get('solar_cap_active', False),
            feed_in_limit_w=ps.get('feed_in_limit_w', 0.0),
            feed_in_limit_headroom=ps.get('feed_in_limit_headroom', 1.0),
        )
        if instance.enabled and instance.price_active \
                and instance.price_limit is None:
            if instance.time_active:
                logger.warning(
                    "peak_shaving price_active is enabled (combined-equivalent: "
                    "time_active and price_active both active) but no "
                    "peak_shaving.price_limit configured: the price "
                    "component is disabled; falling back to time-only "
                    "behaviour. Set a numeric price_limit or disable "
                    "price_active to silence this warning."
                )
            else:
                logger.warning(
                    "peak_shaving.price_active is enabled but no "
                    "peak_shaving.price_limit configured: the price "
                    "component is disabled entirely (time_active is also "
                    "disabled, so there is no fallback). Set a numeric "
                    "price_limit or disable price_active to silence this "
                    "warning."
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
    # Grid-charge target strategy sub-configuration. The concrete config class
    # lives in grid_charge_target.py; use a lazy factory to avoid import cycles.
    grid_charge_target: Any = field(default_factory=_default_grid_charge_target_config)

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
    effective_min_grid_charge_soc: Optional[float] = None

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
