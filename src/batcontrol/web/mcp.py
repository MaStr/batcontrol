""" Read-only MCP server

Exposes batcontrol to MCP clients (Claude Desktop, Claude Code, a voice
assistant) as query tools. Every tool is a thin wrapper around the shared
service layer - no logic lives here.

**Read-only.** There is deliberately no tool that sets a mode, a charge rate
or a parameter. An assistant can explain what batcontrol is doing and why; it
cannot steer the battery. Runtime overrides stay on the MQTT API, where the
broker authenticates them.

Conventions follow the MCP server best practices:
  - names are `batcontrol_<action>_<resource>` (snake_case, service prefixed,
    action first), so they do not collide with another server's tools
  - tool docstrings are the LLM-facing documentation and carry the unit, the
    sign convention, the time base and what the tool does *not* know
  - every tool returns a typed envelope, so clients get an output schema and
    structured content rather than a prose blob
  - long series are paged with limit/offset and report total_count,
    has_more and next_offset
  - failures are reported inside the result, never as a protocol error, with
    a next step for the caller and no internal detail

Transport is HTTP only, served at /mcp by the same application as the REST
API (see app.py). There is deliberately no stdio transport: with stdio the
client spawns the server process, and a freshly spawned batcontrol either
opens a second connection to the inverter - two controllers on one battery -
or runs without a control loop, in which case every tool can only answer "no
data yet". The useful MCP server is the one attached to the batcontrol that is
already running, and that one is reachable over HTTP.

Requires the `mcp` SDK (>= 2.3, where FastMCP was renamed to MCPServer).
"""
import logging
from typing import Annotated, Any, Dict, Generic, Optional, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .. import __pkginfo__
from .auth import bearer_guard
from .models import (
    Battery, Configuration, Current, Decision, DecisionHistory,
    ForecastSeries, Inverter, InverterList, PeakShavingStatus, PriceStatus,
    SolarMetrics, Status, Version,
)
from .service import (
    HISTORY_MAX, WINDOW_MAX, BatcontrolService, NoDataAvailable, NotFound,
)

logger = logging.getLogger(__name__)


SERVER_NAME = 'batcontrol'

# The forecast tools default to service.window_slots() - 24 hours, resolved
# to a slot count from the configured resolution (24 slots hourly, 96
# quarter-hourly). A model rarely needs more than the next day up front and
# pays for every token it is given - and because the series are paged, it can
# ask for the rest whenever it actually needs it.
DEFAULT_HISTORY = 10

INSTRUCTIONS = """
batcontrol charges a home battery when electricity is cheap and preserves it
for expensive hours, using dynamic tariff prices, a solar forecast and a
consumption forecast.

These tools are read-only: they report state, forecasts and the reasoning
behind the current inverter mode. Nothing here can change the battery's
behaviour; say so if the user asks for a change.

Conventions that apply to every tool:
- Timestamps are epoch milliseconds.
- Energy is Wh per forecast slot; energy series also carry power_w, the
  average power over the slot. At 15-minute resolution those differ by a
  factor of four - do not mix them up.
- Prices are EUR/kWh.
- Forecast slot 0 is the interval containing the current time.
- Series are paged: check has_more and request next_offset for the rest.
- A tool answers available=false with a reason when batcontrol has not
  completed a control cycle yet. Report that instead of inventing numbers.
""".strip()

DataT = TypeVar('DataT')

# Parameter annotations, so the generated input schema carries the
# descriptions a model needs instead of a bare integer.
SlotLimit = Annotated[Optional[int], Field(
    ge=1, le=WINDOW_MAX,
    description='Maximum number of forecast slots to return. Omit for the next'
                ' 24 hours, which is 24 slots at 60-minute and 96 slots at'
                ' 15-minute resolution. One slot is interval_minutes long.',
    examples=[24, 96])]
SlotOffset = Annotated[int, Field(
    ge=0, le=WINDOW_MAX,
    description='Slot index to start at. Pass the next_offset of the previous'
                ' response to page through the horizon.',
    examples=[0, 24])]
InverterId = Annotated[int, Field(
    ge=0,
    description='Inverter id as listed by batcontrol_list_inverters.',
    examples=[0])]
HistoryLimit = Annotated[int, Field(
    ge=1, le=HISTORY_MAX,
    description='Maximum number of decision traces to return, newest last.',
    examples=[10, 50])]


class ToolResult(BaseModel, Generic[DataT]):
    """ Uniform tool envelope, so every answer has an output schema. """
    available: bool = Field(
        ...,
        description='False when the data does not exist yet or could not be'
                    ' read. Then data is null and reason explains why.')
    reason: Optional[str] = Field(
        None, description='Why the data is unavailable, including what to do'
                          ' about it.')
    data: Optional[DataT] = Field(None, description='The payload.')


def _ok(model, payload: Dict[str, Any]):
    return ToolResult[model](available=True, data=model(**payload))


def _unavailable(model, reason: str):
    return ToolResult[model](available=False, reason=reason)


def _call(model, func, *args, **kwargs):
    """ Run a service call and wrap it in the envelope.

    Errors are reported inside the result, as the MCP guidance requires, with
    a next step for the caller. An unexpected exception is logged in full but
    reported generically: an MCP answer is not the place to leak internals.
    """
    try:
        return _ok(model, func(*args, **kwargs))
    except NoDataAvailable as exc:
        return _unavailable(
            model,
            f'{exc} Retry after the next control cycle; batcontrol evaluates'
            ' every few minutes.')
    except NotFound as exc:
        return _unavailable(model, str(exc))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error('MCP tool %s failed', getattr(func, '__name__', func),
                     exc_info=True)
        return _unavailable(
            model,
            'batcontrol could not answer this request. Check the batcontrol'
            ' log for the cause.')


def build_server(service: BatcontrolService):  # pylint: disable=too-many-locals
    """ Create the MCP server and register the read-only tools. """
    server = MCPServer(
        name=SERVER_NAME,
        title='batcontrol',
        instructions=INSTRUCTIONS,
        version=__pkginfo__.__version__,
    )

    # Hints, not guarantees - but these tools genuinely only read this
    # instance, so open_world_hint is false.
    read_only = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    def tool(func):
        return server.tool(annotations=read_only)(func)

    def _forecast(name: str, limit: Optional[int], offset: int):
        # None means "24 hours": the slot count depends on the configured
        # interval, so it cannot be a literal default in the input schema.
        # The range itself is enforced by the schema.
        return _call(ForecastSeries, service.forecast, name,
                     limit=service.window_slots() if limit is None else limit,
                     offset=offset)

    @tool
    def batcontrol_get_status() -> ToolResult[Status]:
        """Get the current inverter mode and control state of batcontrol.

        Returns the active mode (-1 force charge from grid, 0 avoid discharge,
        8 limit PV charge rate, 10 allow discharge) with a label, who set it
        (optimizer, api or evcc), the forced grid charge rate in W, whether an
        external override or a grid-charge lock is active, and when the next
        evaluation is due. `online` is false when the control loop has missed
        several cycles, which means every value here is stale.
        """
        return _call(Status, service.status)

    @tool
    def batcontrol_get_battery() -> ToolResult[Battery]:
        """Get state of charge, capacities and SoC limits of the battery.

        All energies are Wh. `stored_usable_energy_capacity` is what may
        actually be discharged (above the inverter's min_soc);
        `reserved_energy_capacity` is what batcontrol wants to hold back for
        upcoming expensive hours and can exceed the battery capacity -
        `reserved_energy_capacity_effective` is the part the battery can
        actually hold back. A null value means not read yet, never zero. The
        values are the ones cached by the last control cycle: this tool does
        not query the inverter.
        """
        return _call(Battery, service.battery)

    @tool
    def batcontrol_get_current() -> ToolResult[Current]:
        """Get the current electricity price together with the state of charge.

        The one call to make for "what is the price right now and how full is
        the battery". The price is the current forecast slot in EUR/kWh and is
        always populated, independent of which decision path ran.
        """
        return _call(Current, service.current)

    @tool
    def batcontrol_get_price_status() -> ToolResult[PriceStatus]:
        """Get price statistics for the next 24 hours in EUR/kWh.

        The current price with its rank (1 = cheapest slot of the window),
        the cheapest and the most expensive slot with their start times,
        the average and the spread. This answers "is it cheap right now",
        "when is the cheapest time today" and "how big is the spread" in one
        small call - prefer it over paging through the price forecast.

        All values are per forecast slot (interval_minutes long). The window
        is 24 hours from now; `complete` is false when the tariff provider's
        prices end earlier.
        """
        return _call(PriceStatus, service.price_status)

    @tool
    def batcontrol_get_price_forecast(
            limit: SlotLimit = None,
            offset: SlotOffset = 0) -> ToolResult[ForecastSeries]:
        """Get the electricity price forecast in EUR/kWh per slot.

        Slot 0 is the current price. For "is it cheap right now", "when is
        the cheapest time" or "what is the spread" call
        batcontrol_get_price_status instead - it answers those in one small
        call. Use this tool when the individual slots matter. The horizon
        depends on the tariff provider, typically to the end of tomorrow
        once the next day's prices are published.

        Defaults to the next 24 hours. Returns at most `limit` slots from
        `offset`; check has_more and next_offset to page through the rest.
        """
        return _forecast('price', limit, offset)

    @tool
    def batcontrol_get_solar_forecast(
            limit: SlotLimit = None,
            offset: SlotOffset = 0) -> ToolResult[ForecastSeries]:
        """Get the expected solar production in Wh per slot.

        `power_w` is the average power over the slot. This is a forecast from
        the configured solar provider, not a measurement: it says nothing
        about what the panels produced in the past or produce this second.

        Defaults to the next 24 hours. Returns at most `limit` slots from
        `offset`; check has_more and next_offset to page through the rest.
        """
        return _forecast('production', limit, offset)

    @tool
    def batcontrol_get_consumption_forecast(
            limit: SlotLimit = None,
            offset: SlotOffset = 0) -> ToolResult[ForecastSeries]:
        """Get the expected household consumption in Wh per slot.

        Derived from a load profile scaled to the configured yearly
        consumption, so it is a statistical expectation, not a measurement,
        and it does not know about a specific appliance being switched on.

        Defaults to the next 24 hours. Returns at most `limit` slots from
        `offset`; check has_more and next_offset to page through the rest.
        """
        return _forecast('consumption', limit, offset)

    @tool
    def batcontrol_get_net_consumption_forecast(
            limit: SlotLimit = None,
            offset: SlotOffset = 0) -> ToolResult[ForecastSeries]:
        """Get consumption minus solar production in Wh per slot.

        Positive means energy has to come from the battery or the grid.
        Negative means surplus solar energy that can charge the battery or be
        fed into the grid. This is the series batcontrol's decisions are based
        on, so it is the one to look at when explaining a decision.

        Defaults to the next 24 hours. Returns at most `limit` slots from
        `offset`; check has_more and next_offset to page through the rest.
        """
        return _forecast('net_consumption', limit, offset)

    @tool
    def batcontrol_get_solar() -> ToolResult[SolarMetrics]:
        """Get solar surplus indicators derived from the forecast.

        `solar_active` says whether the panels are expected to produce in the
        current slot. `solar_surplus_wh` is the solar energy in Wh that will
        not fit into the battery - above 0 it is worth starting a large
        consumer. `pv_start_battery_wh` is the usable battery energy left
        when solar takes over the household again, `forecast_min_battery_wh`
        the lowest usable energy over the whole horizon (0 = the battery is
        expected to run empty). Forecast-based, not measured.
        """
        return _call(SolarMetrics, service.solar)

    @tool
    def batcontrol_get_peak_shaving() -> ToolResult[PeakShavingStatus]:
        """Get what peak shaving did in the last control cycle.

        Peak shaving limits PV charging of the battery so it is not full
        before midday and can absorb the production peak. Returns whether it
        is switched on now, whether it ran (evcc suspends it while a car
        charges), the resulting PV charge limit in W (-1 = none) and the
        outcome and reason code of its rules. The thresholds are in
        batcontrol_get_config.
        """
        return _call(PeakShavingStatus, service.peak_shaving)

    @tool
    def batcontrol_list_inverters() -> ToolResult[InverterList]:
        """List the inverters batcontrol controls, by id.

        With several inverters every other tool reports them as one
        combined battery. Use an id from here with batcontrol_get_inverter
        to look at a single one.
        """
        return _call(InverterList, service.inverters)

    @tool
    def batcontrol_get_inverter(
            inverter_id: InverterId) -> ToolResult[Inverter]:
        """Get the values of one inverter from the last control cycle.

        SoC in percent, capacities and stored energy in Wh, rate limits in
        W, plus backend specific values in `extra`. The values were read by
        batcontrol during its last cycle; this tool does not query the
        inverter.
        """
        return _call(Inverter, service.inverter, inverter_id)

    @tool
    def batcontrol_get_decision() -> ToolResult[Decision]:
        """Get the reason why batcontrol chose the current inverter mode.

        Returns the decision trace of the last control cycle: every rule that
        was evaluated, which one was decisive, and a plain language
        explanation. `status_text` is the one-line summary the Home Assistant
        Decision sensor shows. This is the tool for "why is the battery not
        discharging right now".
        """
        return _call(Decision, service.decision)

    @tool
    def batcontrol_list_decisions(
            limit: HistoryLimit = DEFAULT_HISTORY
    ) -> ToolResult[DecisionHistory]:
        """List recent decisions from the in-memory journal, oldest first.

        Use this for "what has it been doing over the last hours" or to spot a
        mode flapping between two states. The journal is not persisted: it
        starts empty after a restart and holds a few hundred traces at most,
        so it is not a long-term history.

        Returns at most `limit` traces.
        """
        return _call(DecisionHistory, service.decision_history,
                     limit=limit)

    @tool
    def batcontrol_get_config() -> ToolResult[Configuration]:
        """Get the runtime parameters that explain batcontrol's behaviour.

        Price thresholds, SoC limits and the peak shaving settings - the
        numbers a decision has to be read against. This is an allow-list of
        behavioural parameters: it holds no credentials, no inverter address
        and no tariff API token, and it cannot be used to change anything.
        """
        return _call(Configuration, service.configuration)

    @tool
    def batcontrol_get_version() -> ToolResult[Version]:
        """Get version, build and runtime environment of this instance.

        Includes the forecast interval (15 or 60 minutes), which determines
        the slot width of every forecast, and the configured timezone that
        local times should be rendered in.
        """
        return _call(Version, service.version)

    return server


def tool_names(server) -> list:
    """ Names of the registered tools, for tests and diagnostics. """
    # list_tools() on the tool manager is the documented inspection path; the
    # manager itself is private, hence the disable.
    return sorted(
        definition.name
        for definition in server._tool_manager.list_tools()  # pylint: disable=protected-access
    )


def http_routes(server, path: str = '/mcp', host: str = '127.0.0.1',
                token: str = ''):
    """ The MCP transport as (routes, lifespan_app) for a FastAPI application.

    The SDK hands out a complete Starlette app whose lifespan runs the
    session manager. Its routes are attached to the FastAPI router directly
    rather than mounting the app, for one practical reason: a mounted app with
    an inner "/" route answers /mcp with a 307 redirect to /mcp/, and an
    exact route avoids making every client follow that.

    The returned app is still needed - the caller has to enter its lifespan,
    because attaching routes does not start the session manager.
    """
    app = server.streamable_http_app(
        streamable_http_path=path,
        host=host,
        # No Host header validation (maintainer decision): it breaks every
        # reverse proxy that forwards its own host name. The SDK switches it
        # on by itself for a loopback host, so it is turned off explicitly.
        # api.mcp_auth_token is what protects /mcp.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False),
    )
    routes = list(app.routes)
    if token:
        for route in routes:
            route.app = bearer_guard(route.app, token)
    return routes, app
