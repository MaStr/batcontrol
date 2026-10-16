""" Response models for the read-only API

The field descriptions in here are not decoration: they are what Swagger UI
renders, what `openapi.json` carries and what the MCP tool descriptions reuse.
Units belong in the description of every numeric field - a bare "energy" field
has caused enough confusion between Wh per interval and W already.

Timestamps are epoch **milliseconds** throughout the API. The MQTT API uses
epoch seconds; the conversion happens in the service layer.
"""
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

DataT = TypeVar('DataT')


class Envelope(BaseModel, Generic[DataT]):
    """ Common response envelope. Every endpoint answers in this shape. """
    ok: bool = Field(True, description='False only in an error response.')
    timestamp: int = Field(
        ...,
        description='Server time when the response was built, epoch ms.')
    data: DataT = Field(..., description='The payload.')


class Health(BaseModel):
    """ Liveness of the batcontrol process and its control loop. """
    status: str = Field(..., description='Always "ok" when reachable.')
    version: str = Field(..., description='batcontrol version.')
    has_data: bool = Field(
        ...,
        description='True once the first control cycle has produced forecasts.')
    last_evaluation: Optional[int] = Field(
        None, description='Start of the last control cycle, epoch ms.')
    cycle_age_seconds: Optional[float] = Field(
        None, description='Seconds since the last control cycle.')
    evaluation_interval_seconds: int = Field(
        ..., description='Nominal seconds between two control cycles.')
    stale: bool = Field(
        ...,
        description='True when the data must not be presented as current:'
                    ' the control loop has missed several cycles, or its'
                    ' forecast has not been refreshed for as long (for'
                    ' example while the tariff provider fails), or no cycle'
                    ' has completed since startup.')


class Version(BaseModel):
    """ Version and runtime identification. """
    version: str = Field(..., description='batcontrol version.')
    git_sha: Optional[str] = Field(
        None, description='Git commit of the build, if the image provides it.')
    runtime_env: str = Field(
        ...,
        description='Where batcontrol runs: docker, home_assistant_addon-ng,'
                    ' or source.')
    interval_minutes: int = Field(
        ..., description='Forecast slot width in minutes: 15 or 60.')
    timezone: str = Field(..., description='Configured local timezone.')


class Status(BaseModel):
    """ What batcontrol is doing right now. """
    mode: Optional[int] = Field(
        None,
        description='Inverter mode: -1 force charge from grid, 0 avoid'
                    ' discharge, 8 limit PV charge rate, 10 allow discharge.'
                    ' None before the first decision.')
    mode_code: str = Field(
        ...,
        description='Machine readable mode, same vocabulary as the decision'
                    ' trace (e.g. allow_discharging).')
    mode_label: str = Field(
        ..., description='Display label, e.g. "ALLOW DISCHARGE".')
    control_source: Optional[str] = Field(
        None,
        description='Who set the current mode: optimizer, api or evcc.')
    charge_rate: Optional[float] = Field(
        None, description='Forced grid charge rate in W, 0 when not charging.')
    limit_battery_charge_rate: int = Field(
        ...,
        description='Dynamic PV charge rate limit in W. -1 means no limit.')
    api_override_active: bool = Field(
        ..., description='True while an external override holds the mode.')
    discharge_blocked: bool = Field(
        ..., description='True while an external consumer (evcc) blocks'
                         ' discharging.')
    grid_charge_locked: bool = Field(
        ...,
        description='True while grid charging is locked externally'
                    ' (HEMS or grid operator, section 14a EnWG).')
    last_evaluation: Optional[int] = Field(
        None, description='Start of the last control cycle, epoch ms.')
    next_evaluation: Optional[int] = Field(
        None,
        description='When the next cycle is due, epoch ms. Poll against this'
                    ' instead of a fixed interval.')
    evaluation_interval_seconds: int = Field(
        ..., description='Nominal seconds between two control cycles.')
    online: bool = Field(
        ...,
        description='False when the data is stale: the control loop or its'
                    ' forecast has not been refreshed for several cycles.'
                    ' Same rule as /health stale.')
    mode_changed_at: Optional[int] = Field(
        None,
        description='When the mode last actually changed, epoch ms. Not the'
                    ' last refresh, so a UI can show "since HH:MM". Null'
                    ' until the first change after a restart.')


class SocLimits(BaseModel):
    """ The inverter's own SoC window. """
    min_soc: Optional[float] = Field(
        None, description='Lowest SoC the inverter will discharge to, percent.')
    max_soc: Optional[float] = Field(
        None, description='Highest SoC the inverter will charge to, percent.')


class Battery(BaseModel):
    """ Battery state and the limits the dashboard's SOC grid needs.

    Every energy value is in Wh and may be null while the inverter has not
    been read yet - null means unknown, never zero.
    """
    soc: Optional[float] = Field(
        None, description='State of charge in percent (0-100).')
    max_energy_capacity: Optional[float] = Field(
        None, description='Battery capacity in Wh up to max_soc, i.e. gross'
                    ' capacity * max_soc / 100 (not reduced by min_soc).')
    stored_energy_capacity: Optional[float] = Field(
        None, description='Energy stored in the battery in Wh.')
    stored_usable_energy_capacity: Optional[float] = Field(
        None,
        description='Stored energy in Wh that may actually be discharged,'
                    ' i.e. above the inverter min_soc.')
    reserved_energy_capacity: Optional[float] = Field(
        None,
        description='Energy in Wh held back for upcoming expensive hours.'
                    ' Can exceed the battery capacity; consumers should cap'
                    ' it before rendering.')
    free_capacity: Optional[float] = Field(
        None, description='Capacity in Wh still free for charging.')
    always_allow_discharge_limit_percent: Optional[float] = Field(
        None,
        description='Above this SoC discharging is always allowed, percent.')
    max_charging_from_grid_limit_percent: Optional[float] = Field(
        None,
        description='Grid charging stops at this SoC, percent.')
    soc_limits: SocLimits = Field(..., description='Inverter SoC window.')


class CurrentPrice(BaseModel):
    """ The price situation right now. """
    current: Optional[float] = Field(
        None,
        description='Price of the current slot in EUR/kWh. Taken from the'
                    ' price forecast, so it is always populated regardless of'
                    ' which decision path ran.')
    current_ts: int = Field(
        ..., description='Start of the current price slot, epoch ms.')
    unit: str = Field(..., description='Always EUR/kWh.')
    interval_minutes: int = Field(
        ..., description='Slot width in minutes: 15 or 60.')
    min_dynamic_price_difference: Optional[float] = Field(
        None,
        description='Price delta in EUR/kWh that a future slot has to beat'
                    ' before batcontrol charges from the grid for it.')


class Current(BaseModel):
    """ Everything the dashboard header and price block need in one call. """
    version: str = Field(..., description='batcontrol version.')
    price: CurrentPrice = Field(..., description='Current price situation.')
    soc: Optional[float] = Field(None, description='State of charge, percent.')
    soc_limits: SocLimits = Field(..., description='Inverter SoC window.')


class ForecastSlot(BaseModel):
    """ One forecast interval. """
    slot: int = Field(
        ...,
        description='Index, 0 being the interval that contains the current'
                    ' time.')
    time_start: int = Field(..., description='Slot start, epoch ms.')
    time_end: int = Field(..., description='Slot end, epoch ms.')
    value: float = Field(
        ...,
        description='The series value: EUR/kWh for prices, Wh per slot for'
                    ' energy series.')
    power_w: Optional[float] = Field(
        None,
        description='Average power over the slot in W. Energy series only.'
                    ' At 15-minute resolution this is four times the Wh'
                    ' value, not the same number.')


class ForecastWindow(BaseModel):
    """ Paging metadata shared by every forecast response.

    Slot 0 is the slot containing the current time, so all counts start
    there - not at the start of the control cycle's forecast.
    """
    run_time: Optional[int] = Field(
        None, description='Control cycle this forecast belongs to, epoch ms.')
    interval_minutes: int = Field(..., description='Slot width in minutes.')
    total_count: int = Field(
        ..., description='Number of slots from the current slot to the end of'
                         ' the forecast horizon.')
    returned_count: int = Field(
        ..., description='Number of slots in this response.')
    offset: int = Field(
        ..., description='Slot index this window starts at, 0 being the'
                         ' current slot.')
    has_more: bool = Field(
        ..., description='True when further slots exist beyond this window.')
    next_offset: Optional[int] = Field(
        None,
        description='Offset to request next, or null when the horizon ends'
                    ' here.')


class ForecastSeries(ForecastWindow):
    """ One forecast series, windowed by limit/offset. """
    unit: str = Field(..., description='EUR/kWh or Wh.')
    slots: list[ForecastSlot] = Field(..., description='The slots, in order.')


class ForecastBundle(ForecastWindow):
    """ All four series of one control cycle, for a single poll. """
    price: list[ForecastSlot] = Field(
        ..., description='Electricity price per slot in EUR/kWh.')
    consumption: list[ForecastSlot] = Field(
        ..., description='Forecast household consumption in Wh per slot.')
    production: list[ForecastSlot] = Field(
        ..., description='Forecast solar production in Wh per slot.')
    net_consumption: list[ForecastSlot] = Field(
        ...,
        description='Consumption minus production in Wh per slot. Negative'
                    ' means surplus solar energy.')


class Decision(BaseModel):
    """ The latest decision and the trace that explains it.

    `trace`, `decisive` and `event` are passed through from the decision
    journal. Their inner shape is documented in
    docs/development/decision-trace.md and deliberately not pinned down here,
    because the inputs of a record differ per reason code.
    """
    decided_at: Optional[int] = Field(
        None, description='When this decision was taken, epoch ms.')
    trace: dict = Field(
        ...,
        description='The full trace: timestamp, decided_by and records. Its'
                    ' own timestamp is ISO 8601, because the trace is the'
                    ' same object the MQTT Decision sensor publishes.')
    status_text: Optional[str] = Field(
        None,
        description='Resulting mode plus a plain language reason, the same'
                    ' text the Home Assistant Decision sensor shows.')
    explanation: Optional[str] = Field(
        None, description='Plain language "why" of the decisive step.')
    decisive: Optional[dict] = Field(
        None, description='The record that determined the outcome.')
    event: Optional[dict] = Field(
        None,
        description='The last status *change*, including the mode transition.'
                    ' This is not the latest evaluation: it only moves when'
                    ' the mode or value changes, or every 15 minutes. Render'
                    ' the records from `trace`, not from here.')
    event_at: Optional[int] = Field(
        None,
        description='When that status change happened, epoch ms. Compare it'
                    ' with decided_at to see how much older it is.')


class DecisionHistory(BaseModel):
    """ A window into the in-memory decision journal. """
    count: int = Field(..., description='Number of traces returned.')
    limit: int = Field(..., description='Requested maximum.')
    traces: list[dict] = Field(
        ..., description='Traces, oldest first.')


class BatteryControlConfig(BaseModel):
    """ The parameters that drive the price decision. """
    type: str = Field(..., description='Logic type: default (or its alias next).')
    min_price_difference: Optional[float] = Field(
        None, description='Absolute price delta in EUR/kWh to justify charging.')
    min_price_difference_rel: Optional[float] = Field(
        None, description='Relative price delta as a factor (0.10 = 10 %).')
    always_allow_discharge_limit: Optional[float] = Field(
        None, description='SoC ratio (0-1) above which discharging is always allowed.')
    max_charging_from_grid_limit: Optional[float] = Field(
        None, description='SoC ratio (0-1) up to which grid charging is allowed.')
    min_grid_charge_soc: Optional[float] = Field(
        None, description='Optional SoC ratio (0-1) to preserve before expensive slots.')
    preserve_min_grid_charge_soc: bool = Field(
        ..., description='Whether min_grid_charge_soc counts as reserved energy.')
    production_offset_percent: Optional[float] = Field(
        None, description='Factor applied to the solar forecast (1.0 = unchanged).')


class PeakShavingConfigModel(BaseModel):
    """ Peak shaving switches and thresholds. """
    enabled: bool = Field(..., description='Whether peak shaving runs at all.')
    time_active: bool = Field(..., description='Target-time rule active.')
    price_active: bool = Field(..., description='Cheap-price rule active.')
    solar_cap_active: bool = Field(..., description='Feed-in limit rule active.')
    allow_full_battery_after: int = Field(
        ..., description='Hour (0-23) the battery should be full by.')
    price_limit: Optional[float] = Field(
        None, description='Cheap-slot threshold in EUR/kWh, -1 disables the rule.')
    feed_in_limit_w: Optional[float] = Field(
        None, description='Grid feed-in power limit in W, 0 = off.')


class Configuration(BaseModel):
    """ Runtime parameters that explain batcontrol's behaviour.

    Built from an explicit allow-list of runtime values. No credential can
    appear here: the endpoint does not read the configuration file at all.
    """
    interval_minutes: int = Field(..., description='Forecast slot width, 15 or 60.')
    timezone: str = Field(..., description='Configured local timezone.')
    evaluation_interval_seconds: int = Field(
        ..., description='Nominal seconds between two control cycles.')
    battery_control: BatteryControlConfig = Field(
        ..., description='Price decision parameters.')
    peak_shaving: PeakShavingConfigModel = Field(
        ..., description='Peak shaving configuration.')
