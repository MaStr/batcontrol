""" Runtime state of the control loop, grouped by topic

The control loop computes a number of values every cycle that MQTT, the REST
API and the MCP server all report. They used to live in loose ``last_*``
attributes on the core, and some of them were only ever computed inside the
MQTT branch - with MQTT disabled they simply did not exist.

Each group below is a frozen dataclass. The loop replaces a group with one
attribute assignment, which is atomic, so a reader on another thread (the web
API) sees either the previous or the new state of a group, never a mix. That
is the same rule ForecastSnapshot follows for the forecast arrays.

Units: energy in Wh, power in W, SoC limits in percent unless a field says it
is a ratio (0-1). -1 is batcontrol's "not read yet" sentinel for values that
come from the inverter.
"""
import dataclasses
from typing import Optional

# Sentinel for values that have not been read from the inverter yet.
UNKNOWN = -1


def as_number(value, default: float = UNKNOWN) -> float:
    """ value as float, or default if it is not a number. """
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclasses.dataclass(frozen=True)
class BatteryState:  # pylint: disable=too-many-instance-attributes
    """ Battery values read from the inverter, cached per control cycle.

    Updated field by field through dataclasses.replace() as the loop reads
    them, so each update is one atomic assignment.
    """
    soc: float = UNKNOWN
    max_capacity: float = UNKNOWN          # Wh, gross capacity * max_soc
    stored_energy: float = UNKNOWN         # Wh
    stored_usable_energy: float = UNKNOWN  # Wh above min_soc
    free_capacity: float = UNKNOWN         # Wh up to max_soc
    min_soc: float = UNKNOWN               # percent
    max_soc: float = UNKNOWN               # percent
    # max_capacity * always_allow_discharge_limit, Wh
    always_allow_discharge_limit_capacity: float = UNKNOWN

    @property
    def gross_capacity(self) -> float:
        """ Capacity in Wh without the max_soc reduction.

        max_capacity is already reduced by max_soc (the inverter's
        get_max_capacity()). Scaling it back up gives the gross capacity
        without a call to the inverter; for an InverterGroup the weighted
        max_soc makes this exactly the sum of the member capacities.
        """
        if self.max_capacity == UNKNOWN:
            return UNKNOWN
        if self.max_soc > 0:
            return self.max_capacity * 100 / self.max_soc
        return self.max_capacity

    def update(self, **changes) -> 'BatteryState':
        """ A copy with the given fields replaced. """
        return dataclasses.replace(self, **changes)


def cap_reserved_energy(reserved_energy: float, battery: BatteryState,
                        always_allow_discharge_limit: float) -> float:
    """ The part of a reservation the battery can actually hold back.

    The logic reports the raw demand for upcoming expensive slots, which can
    exceed the battery. Nothing is held back above always_allow_discharge
    (discharging is always allowed there) or above max_soc, and nothing below
    min_soc is usable, so that window bounds the reservation.
    """
    gross = battery.gross_capacity
    if reserved_energy is None or reserved_energy == UNKNOWN or gross == UNKNOWN:
        return UNKNOWN
    min_soc = battery.min_soc if battery.min_soc != UNKNOWN else 0
    max_soc = battery.max_soc if battery.max_soc != UNKNOWN else 100
    top_soc = min(max_soc, always_allow_discharge_limit * 100)
    window = gross * max(0.0, top_soc - min_soc) / 100
    return max(0.0, min(float(reserved_energy), window))


@dataclasses.dataclass(frozen=True)
class CalculationResult:
    """ What the logic computed in the last cycle that ran it. """
    reserved_energy: float                 # Wh, raw demand, can exceed battery
    reserved_energy_effective: float       # Wh, capped to the battery window
    min_dynamic_price_difference: float    # EUR/kWh
    # Ratio 0-1, None when the minimum grid charge target is not configured
    effective_min_grid_charge_soc: Optional[float] = None


@dataclasses.dataclass(frozen=True)
class SolarMetrics:
    """ Indicators derived from the forecast, see forecast_metrics.py. """
    solar_active: bool                     # production in the current slot
    solar_surplus_wh: float                # expected PV overflow, Wh
    pv_start_battery_wh: float             # battery above min_soc when PV takes over, Wh
    forecast_min_battery_wh: float         # lowest battery above min_soc in the horizon, Wh


@dataclasses.dataclass(frozen=True)
class PeakShavingState:  # pylint: disable=too-many-instance-attributes
    """ What peak shaving did in the last cycle that ran the logic.

    The parameters themselves live in PeakShavingConfig; this is the runtime
    outcome.
    """
    enabled: bool                  # switch, including runtime overrides
    active: bool                   # evaluated this cycle (enabled, not suspended)
    suspended_by: Optional[str]    # reason code when evcc suspended it
    charge_limit_w: int            # resulting PV charge limit in W, -1 none
    # Outcome and reason code of the peak shaving trace record
    outcome: Optional[str] = None
    reason: Optional[str] = None
    # Same for the solar (feed-in) limit rule, which is configured as part of
    # peak shaving (solar_cap_active)
    solar_limit_outcome: Optional[str] = None
    solar_limit_reason: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class ConfiguredParameters:  # pylint: disable=too-many-instance-attributes
    """ Runtime-adjustable parameters as they were configured at startup.

    MQTT, evcc and the grid charge lock can change these at runtime. The
    baseline is kept so the API can tell an overridden value from a
    configured one.
    """
    min_price_difference: float
    min_price_difference_rel: float
    always_allow_discharge_limit: float
    max_charging_from_grid_limit: float
    production_offset_percent: float
    peak_shaving_enabled: bool
    peak_shaving_allow_full_battery_after: int
    peak_shaving_price_limit: Optional[float]
