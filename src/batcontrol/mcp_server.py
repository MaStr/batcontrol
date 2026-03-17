"""MCP (Model Context Protocol) Server for Batcontrol

Provides AI-accessible tools to query system state, forecasts,
battery info, and manage overrides.

Runs in-process as a thread alongside the main batcontrol evaluation loop.
Supports Streamable HTTP transport (for Docker/HA addon) and stdio transport.

Requires the optional 'mcp' dependency (Python >=3.10):
    pip install batcontrol[mcp]

Use `is_available()` to check at runtime before instantiation.
"""
import logging
import threading
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    FastMCP = None


def is_available() -> bool:
    """Check whether the MCP SDK is installed and importable."""
    return MCP_AVAILABLE

logger = logging.getLogger(__name__)

# Mode constants (duplicated to avoid circular imports)
MODE_ALLOW_DISCHARGING = 10
MODE_LIMIT_BATTERY_CHARGE_RATE = 8
MODE_AVOID_DISCHARGING = 0
MODE_FORCE_CHARGING = -1

MODE_NAMES = {
    MODE_FORCE_CHARGING: "Force Charge from Grid",
    MODE_AVOID_DISCHARGING: "Avoid Discharge",
    MODE_LIMIT_BATTERY_CHARGE_RATE: "Limit PV Charge Rate",
    MODE_ALLOW_DISCHARGING: "Allow Discharge",
}

VALID_MODES = set(MODE_NAMES.keys())


def _format_forecast_array(arr, run_time: float, interval_minutes: int,
                           digits: int = 1) -> list:
    """Format a numpy array forecast into a list of {time, value} dicts.

    Args:
        arr: Numpy array of values.
        run_time: Current epoch timestamp.
        interval_minutes: Slot width in minutes.
        digits: Decimal places to round values to. Use 1 for power/energy (W/Wh),
                4 for prices (EUR/kWh) to preserve meaningful precision.
    """
    if arr is None:
        return []
    interval_seconds = interval_minutes * 60
    base_time = run_time - (run_time % interval_seconds)
    result = []
    for i, val in enumerate(arr):
        result.append({
            'slot': i,
            'time_start': base_time + i * interval_seconds,
            'value': round(float(val), digits),
        })
    return result


class BatcontrolMcpServer:
    """MCP server that exposes batcontrol as AI-accessible tools.

    Initialized with a reference to the live Batcontrol instance.
    """

    def __init__(self, batcontrol_instance, config: dict):
        if not MCP_AVAILABLE:
            raise ImportError(
                "MCP server requires the 'mcp' package (Python >=3.10). "
                "Install with: pip install batcontrol[mcp]"
            )
        self._bc = batcontrol_instance
        self._config = config
        self._thread: Optional[threading.Thread] = None

        self.mcp = FastMCP(
            "Batcontrol",
            instructions=(
                "Batcontrol is a home battery optimization system. It automatically "
                "charges/discharges PV batteries based on dynamic electricity prices, "
                "solar forecasts, and consumption patterns. Use these tools to inspect "
                "system state, view forecasts, understand decisions, and manage overrides."
            ),
        )
        self._register_tools()

    def _register_tools(self):
        """Register all MCP tools."""

        # ---- Read Tools ----

        @self.mcp.tool()
        def get_system_status() -> dict:
            """Get current batcontrol system status.

            Returns the current operating mode, battery state of charge,
            charge rate, override status, and last evaluation time.
            """
            bc = self._bc
            override = bc.override_manager.get_override()
            return {
                'mode': bc.last_mode,
                'mode_name': MODE_NAMES.get(bc.last_mode, "Unknown"),
                'charge_rate_w': bc.last_charge_rate,
                'soc_percent': bc.last_SOC,
                'stored_energy_wh': bc.last_stored_energy,
                'stored_usable_energy_wh': bc.last_stored_usable_energy,
                'max_capacity_wh': bc.last_max_capacity,
                'free_capacity_wh': bc.last_free_capacity,
                'reserved_energy_wh': bc.last_reserved_energy,
                'discharge_blocked': bc.discharge_blocked,
                'last_evaluation_timestamp': bc.last_run_time,
                'override': override.to_dict() if override else None,
                'time_resolution_minutes': bc.time_resolution,
            }

        @self.mcp.tool()
        def get_price_forecast() -> dict:
            """Get the electricity price forecast for the next 24-48 hours.

            Returns hourly or 15-minute prices depending on system configuration.
            Prices are in EUR/kWh.
            """
            bc = self._bc
            return {
                'interval_minutes': bc.time_resolution,
                'prices': _format_forecast_array(
                    bc.last_prices, bc.last_run_time, bc.time_resolution, digits=4),
                'current_price': round(float(bc.last_prices[0]), 4) if bc.last_prices is not None and len(bc.last_prices) > 0 else None,
            }

        @self.mcp.tool()
        def get_solar_forecast() -> dict:
            """Get the solar production forecast.

            Returns expected PV production in Watts per time interval.
            """
            bc = self._bc
            return {
                'interval_minutes': bc.time_resolution,
                'production_w': _format_forecast_array(
                    bc.last_production, bc.last_run_time, bc.time_resolution),
                'production_offset_percent': bc.production_offset_percent,
            }

        @self.mcp.tool()
        def get_consumption_forecast() -> dict:
            """Get the household consumption forecast.

            Returns expected consumption in Watts per time interval.
            """
            bc = self._bc
            return {
                'interval_minutes': bc.time_resolution,
                'consumption_w': _format_forecast_array(
                    bc.last_consumption, bc.last_run_time, bc.time_resolution),
            }

        @self.mcp.tool()
        def get_net_consumption_forecast() -> dict:
            """Get the net consumption forecast (consumption minus production).

            Positive values mean grid dependency, negative means surplus.
            """
            bc = self._bc
            return {
                'interval_minutes': bc.time_resolution,
                'net_consumption_w': _format_forecast_array(
                    bc.last_net_consumption, bc.last_run_time, bc.time_resolution),
            }

        @self.mcp.tool()
        def get_battery_info() -> dict:
            """Get detailed battery information.

            Returns state of charge, capacity, stored energy, reserved energy,
            and free capacity.
            """
            bc = self._bc
            return {
                'soc_percent': bc.last_SOC,
                'max_capacity_wh': bc.last_max_capacity,
                'stored_energy_wh': bc.last_stored_energy,
                'stored_usable_energy_wh': bc.last_stored_usable_energy,
                'free_capacity_wh': bc.last_free_capacity,
                'reserved_energy_wh': bc.last_reserved_energy,
                'always_allow_discharge_limit': bc.get_always_allow_discharge_limit(),
                'max_charging_from_grid_limit': bc.max_charging_from_grid_limit,
            }

        @self.mcp.tool()
        def get_decision_explanation() -> dict:
            """Get an explanation of why the system chose the current operating mode.

            Returns step-by-step reasoning from the last evaluation cycle,
            including price analysis, energy balance, and the final decision.
            """
            bc = self._bc
            override = bc.override_manager.get_override()
            explanation = bc.api_get_decision_explanation()

            result = {
                'mode': bc.last_mode,
                'mode_name': MODE_NAMES.get(bc.last_mode, "Unknown"),
                'explanation_steps': explanation,
                'override_active': override is not None,
            }
            if override:
                result['override_info'] = (
                    "Manual override active: %s for %.1f more minutes. Reason: %s" % (
                        MODE_NAMES.get(override.mode, "Unknown"),
                        override.remaining_minutes,
                        override.reason or "not specified"
                    )
                )
            return result

        @self.mcp.tool()
        def get_configuration() -> dict:
            """Get current runtime configuration parameters.

            Returns battery control thresholds, price settings, and
            operational parameters that can be adjusted.
            """
            bc = self._bc
            return {
                'always_allow_discharge_limit': bc.get_always_allow_discharge_limit(),
                'max_charging_from_grid_limit': bc.max_charging_from_grid_limit,
                'min_price_difference': bc.min_price_difference,
                'min_price_difference_rel': bc.min_price_difference_rel,
                'production_offset_percent': bc.production_offset_percent,
                'time_resolution_minutes': bc.time_resolution,
                'limit_battery_charge_rate': bc._limit_battery_charge_rate,
            }

        @self.mcp.tool()
        def get_override_status() -> dict:
            """Get the current override status.

            Returns details about any active manual override including
            mode, remaining time, and reason. Returns null if no override is active.
            """
            override = self._bc.override_manager.get_override()
            if override is None:
                return {'active': False, 'override': None}
            return {
                'active': True,
                'override': override.to_dict(),
                'mode_name': MODE_NAMES.get(override.mode, "Unknown"),
            }

        # ---- Write Tools ----

        @self.mcp.tool()
        def set_mode_override(mode: int, duration_minutes: float = 30,
                              reason: str = "") -> dict:
            """Override the battery control mode for a specified duration.

            The override persists across evaluation cycles and auto-expires.
            Normal autonomous logic resumes after expiry.

            Args:
                mode: Inverter mode.
                    -1 = Force charge from grid
                     0 = Avoid discharge (protect battery)
                     8 = Limit PV charge rate
                    10 = Allow discharge (normal operation)
                duration_minutes: How long the override lasts (default 30 min)
                reason: Human-readable reason for the override
            """
            if mode not in VALID_MODES:
                return {'error': "Invalid mode %s. Valid: %s" % (mode, sorted(VALID_MODES))}
            if duration_minutes <= 0 or duration_minutes > 1440:
                return {'error': "duration_minutes must be between 1 and 1440 (24h)"}

            bc = self._bc
            override = bc.override_manager.set_override(
                mode=mode,
                duration_minutes=duration_minutes,
                reason=reason or "MCP set_mode_override",
            )
            bc._apply_override(override)

            return {
                'success': True,
                'override': override.to_dict(),
                'mode_name': MODE_NAMES.get(mode, "Unknown"),
            }

        @self.mcp.tool()
        def clear_mode_override() -> dict:
            """Clear any active override and resume autonomous battery control.

            The next evaluation cycle will recalculate the optimal mode.
            """
            bc = self._bc
            was_active = bc.override_manager.is_active()
            bc.override_manager.clear_override()
            return {
                'success': True,
                'was_active': was_active,
                'message': "Override cleared. Autonomous logic will resume at next evaluation."
            }

        @self.mcp.tool()
        def set_charge_rate(charge_rate_w: int, duration_minutes: float = 30,
                            reason: str = "") -> dict:
            """Force charge the battery at a specific rate for a duration.

            This sets the mode to Force Charge (-1) with the given charge rate.

            Args:
                charge_rate_w: Charge rate in Watts (must be > 0)
                duration_minutes: How long to charge (default 30 min)
                reason: Human-readable reason
            """
            if charge_rate_w <= 0:
                return {'error': "charge_rate_w must be positive"}
            if duration_minutes <= 0 or duration_minutes > 1440:
                return {'error': "duration_minutes must be between 1 and 1440 (24h)"}

            bc = self._bc
            override = bc.override_manager.set_override(
                mode=MODE_FORCE_CHARGING,
                charge_rate=charge_rate_w,
                duration_minutes=duration_minutes,
                reason=reason or "MCP set_charge_rate",
            )
            bc._apply_override(override)

            return {
                'success': True,
                'override': override.to_dict(),
                'effective_charge_rate_w': bc.last_charge_rate,
            }

        @self.mcp.tool()
        def set_parameter(parameter: str, value: float) -> dict:
            """Adjust a runtime configuration parameter.

            Changes are temporary and will not be written to the config file.
            The next evaluation cycle will use the new value.

            Args:
                parameter: Parameter name. One of:
                    - always_allow_discharge_limit (0.0-1.0)
                    - max_charging_from_grid_limit (0.0-1.0)
                    - min_price_difference (>= 0.0, EUR)
                    - min_price_difference_rel (>= 0.0)
                    - production_offset (0.0-2.0, multiplier)
                value: New value for the parameter
            """
            bc = self._bc
            handlers = {
                'always_allow_discharge_limit': bc.api_set_always_allow_discharge_limit,
                'max_charging_from_grid_limit': bc.api_set_max_charging_from_grid_limit,
                'min_price_difference': bc.api_set_min_price_difference,
                'min_price_difference_rel': bc.api_set_min_price_difference_rel,
                'production_offset': bc.api_set_production_offset,
            }

            if parameter not in handlers:
                return {
                    'error': "Unknown parameter '%s'. Valid: %s" % (
                        parameter, sorted(handlers.keys()))
                }

            handlers[parameter](value)
            return {
                'success': True,
                'parameter': parameter,
                'new_value': value,
            }

    def start_http(self, host: str = "0.0.0.0", port: int = 8081):
        """Start the MCP server with Streamable HTTP transport in a background thread."""
        def _run():
            logger.info("Starting MCP server on %s:%d", host, port)
            try:
                self.mcp.run(transport="streamable-http", host=host, port=port)
            except Exception as e:
                logger.error("MCP server error: %s", e, exc_info=True)

        self._thread = threading.Thread(
            target=_run,
            name="MCPServerThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("MCP server thread started")

    def run_stdio(self):
        """Run the MCP server with stdio transport (blocking)."""
        logger.info("Starting MCP server with stdio transport")
        self.mcp.run(transport="stdio")

    def shutdown(self):
        """Request MCP server shutdown.

        Note: The MCP SDK (FastMCP/uvicorn) does not expose a programmatic
        stop API. The HTTP server runs as a daemon thread and will be cleaned
        up automatically when the process exits. If you need clean mid-process
        shutdown, use a dedicated process/subprocess for the MCP server instead.
        """
        logger.info("MCP server shutdown requested (daemon thread will exit with process)")
