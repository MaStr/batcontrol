"""HomeAssistant API based consumption forecasting

This module provides consumption forecasting using historical data from HomeAssistant.
It fetches historical consumption data for configured time periods and calculates weighted
statistics for each hour to predict future consumption.
"""

import datetime
import json
import logging
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np
from cachetools import TTLCache
from websockets.asyncio.client import connect

from ..async_utils import managed_event_loop, run_coroutine
from .baseclass import ForecastConsumptionBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module')

MAX_FORECAST_HOURS = 48


logger_ha_details = logging.getLogger(
    "batcontrol.forecastconsumption.forecast_homeassistant.details")
logger_ha_communication = logging.getLogger(
    "batcontrol.forecastconsumption.forecast_homeassistant.communication")


# pylint: disable=too-many-instance-attributes
class ForecastConsumptionHomeAssistant(ForecastConsumptionBaseclass):
    """Forecasts consumption based on historical data from HomeAssistant API

    This class fetches historical consumption data from HomeAssistant for specified
    time periods (e.g., last 7, 14, 21 days) and calculates weighted averages
    for each hour of the week to predict future consumption.

    Attributes:
        base_url: HomeAssistant base URL
        api_token: HomeAssistant API access token
        entity_id: Entity ID to fetch consumption data for
        history_days: List of days to look back (e.g., [-7, -14, -21])
        history_weights: Weights for each history period (e.g., [1, 1, 1])
        timezone: Timezone for data processing
        consumption_cache: TTLCache storing consumption values by weekday_hour key
        cache_ttl_hours: TTL for cached data in hours
    """

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        base_url: str,
        api_token: str,
        entity_id: str,
        timezone,
        history_days: Optional[List[int]] = None,
        history_weights: Optional[List[int]] = None,
        cache_ttl_hours: float = 48.0,
        multiplier: float = 1.0,
        target_resolution: int = 60,
        sensor_unit: Optional[str] = "auto"
    ) -> None:
        """Initialize HomeAssistant consumption forecaster

        Args:
            base_url: HomeAssistant base URL (e.g., "http://192.168.1.100:8123")
            api_token: HomeAssistant Long-Lived Access Token
            entity_id: Entity ID to fetch data for (e.g., "sensor.energy_consumption")
            timezone: Timezone object for data processing
            history_days: List of negative day offsets to fetch (e.g., [-7, -14, -21])
            history_weights: Weight for each history period (1-10), same length as history_days
            cache_ttl_hours: Time-to-live for cached statistics in hours (default: 48)
            multiplier: Multiplier applied to all forecast values (default: 1.0)
                       Use >1.0 to increase forecast, <1.0 to decrease
            sensor_unit: Optional sensor unit ('auto', 'Wh', or 'kWh').
                        If set to 'Wh' or 'kWh', skips auto-detection and uses the specified unit.
                        If set to 'auto' , queries Home Assistant to detect the unit.
                        Default: auto (auto-detect)
            target_resolution: Target resolution in minutes (15 or 60)
        """
        # Initialize baseclass with 60-minute native resolution
        super().__init__(
            timezone,
            target_resolution=target_resolution,
            native_resolution=60)

        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.entity_id = entity_id

        # Default to last 7, 14, 21 days with equal weights
        self.history_days = history_days if history_days else [-7, -14, -21]
        self.history_weights = history_weights if history_weights else [
            1, 1, 1]
        self.multiplier = multiplier

        # Validate configuration
        if len(self.history_days) != len(self.history_weights):
            raise ValueError(
                f"Length of history_days ({len(self.history_days)}) must match "
                f"history_weights ({len(self.history_weights)})"
            )

        for weight in self.history_weights:
            if not 1 <= weight <= 10:
                raise ValueError(f"History weights must be between 1 and 10, got {weight}")

        # Validate sensor_unit parameter
        if sensor_unit is not None:
            sensor_unit_lower = sensor_unit.lower()
            if sensor_unit_lower not in ['auto', 'wh', 'kwh']:
                raise ValueError(
                    f"Invalid sensor_unit '{sensor_unit}'. "
                    f"Allowed values: 'auto', 'Wh', 'kWh'"
                )
            self.sensor_unit = sensor_unit_lower
        else:
            raise ValueError(
                f"Invalid sensor_unit '{sensor_unit}'. "
                f"Allowed values: 'auto', 'Wh', 'kWh'"
            )

        # Initialize cache with TTL
        # Cache key format: "weekday_hour" (e.g., "0_14" for Monday 14:00)
        # Cache stores consumption value in Wh for each hour slot
        # maxsize: 168 = 7 days * 24 hours (one week of hourly data)
        self.cache_ttl_hours = cache_ttl_hours
        cache_ttl_seconds = int(cache_ttl_hours * 3600)
        self.consumption_cache: TTLCache = TTLCache(
            maxsize=168, ttl=cache_ttl_seconds)
        self._cache_lock = threading.Lock()

        # Query sensor to determine unit and set conversion factor (if not explicitly configured)
        if self.sensor_unit and self.sensor_unit != 'auto':
            # User explicitly configured the unit, skip discovery
            if self.sensor_unit == 'wh':
                self.unit_conversion_factor = 1.0
                logger.info(
                    "Using configured sensor unit: Wh (conversion factor: 1.0)"
                )
            elif self.sensor_unit == 'kwh':
                self.unit_conversion_factor = 1000.0
                logger.info(
                    "Using configured sensor unit: kWh (conversion factor: 1000.0)"
                )
        else:
            # Auto-detect unit from Home Assistant
            logger.info("Auto-detecting sensor unit from Home Assistant...")
            self.unit_conversion_factor = self._check_sensor_unit()

        logger.info(
            "Initialized HomeAssistant consumption forecaster: "
            "entity_id=%s, history_days=%s, weights=%s, cache_ttl=%0.1fh, "
            "multiplier=%0.2f, sensor_unit=%s, unit_conversion_factor=%0.1f",
            entity_id, self.history_days, self.history_weights,
            cache_ttl_hours, multiplier, self.sensor_unit or 'auto', self.unit_conversion_factor
        )

    def _check_sensor_unit(self) -> float:
        """Check sensor's unit_of_measurement and return conversion factor

        Queries the sensor via WebSocket to get its unit_of_measurement attribute.
        Returns the appropriate conversion factor to convert to Wh.

        Returns:
            float: Conversion factor (1.0 for Wh, 1000.0 for kWh)

        Raises:
            ValueError: If unit_of_measurement is neither Wh nor kWh
            RuntimeError: If sensor cannot be queried
        """
        return run_coroutine(self._check_sensor_unit_async())

    async def _check_sensor_unit_async(self) -> float:
        """Async implementation of sensor unit check

        Returns:
            float: Conversion factor (1.0 for Wh, 1000.0 for kWh)

        Raises:
            ValueError: If unit_of_measurement is neither Wh nor kWh
            RuntimeError: If sensor cannot be queried
        """
        logger.debug(
            "Checking unit_of_measurement for entity: %s",
            self.entity_id)

        websocket, message_id = await self._websocket_connect()

        try:
            # Request all states to find our entity
            states_request = {
                "id": message_id,
                "type": "get_states"
            }
            logger_ha_communication.debug(
                "Sending get_states request: %s", states_request)
            await websocket.send(json.dumps(states_request))

            # Receive states response
            states_response = await websocket.recv()
            states_result = json.loads(states_response)
            logger_ha_communication.debug(
                "Received states response: id=%s, type=%s, success=%s",
                states_result.get("id"),
                states_result.get("type"),
                states_result.get("success"))

            if not states_result.get("success"):
                error_msg = states_result.get(
                    "error", {}).get(
                    "message", "Unknown error")
                logger.error("get_states request failed: %s", error_msg)
                raise RuntimeError(f"Failed to get sensor states: {error_msg}")

            # Find our entity in the results
            states = states_result.get("result", [])
            entity_state = None
            for state in states:
                if state.get("entity_id") == self.entity_id:
                    entity_state = state
                    break

            if entity_state is None:
                raise RuntimeError(
                    f"Entity '{self.entity_id}' not found in HomeAssistant. "
                    f"Please check the entity_id."
                )

            # Get unit_of_measurement from attributes
            attributes = entity_state.get("attributes", {})
            unit = attributes.get("unit_of_measurement")

            logger.info(
                "Entity '%s' has unit_of_measurement: %s",
                self.entity_id, unit
            )

            # Determine conversion factor based on unit
            if unit == "Wh":
                logger.debug("Unit is Wh, no conversion needed")
                return 1.0
            if unit == "kWh":
                logger.info("Unit is kWh, will multiply values by 1000 to convert to Wh")
                return 1000.0

            raise ValueError(
                f"Unsupported unit_of_measurement '{unit}' for entity "
                f"'{self.entity_id}'. Only 'Wh' and 'kWh' are supported.")

        finally:
            await self._websocket_disconnect(websocket)

    def _get_cache_key(self, weekday: int, hour: int) -> str:
        """Generate cache key from weekday and hour

        Args:
            weekday: Day of week (0=Monday, 6=Sunday)
            hour: Hour of day (0-23)

        Returns:
            Cache key in format "weekday_hour" (e.g., "0_14" for Monday 14:00)
        """
        return f"{weekday}_{hour}"

    async def _websocket_connect(self):
        """Connect to HomeAssistant WebSocket API and authenticate

        Returns:
            Tuple of (websocket connection, message_id counter)

        Raises:
            RuntimeError: If connection or authentication fails
        """
        # Build WebSocket URL
        ws_url = self.base_url.replace(
            'http://', 'ws://').replace('https://', 'wss://')
        ws_url = f"{ws_url}/api/websocket"

        logger_ha_communication.debug(
            "Connecting to HomeAssistant WebSocket: %s", ws_url)

        # Set max_size to 4MB to handle large Home Assistant instances
        # Default is 1MB which causes crashes for installations with many entities
        # See: https://github.com/MaStr/batcontrol/issues/241
        websocket = await connect(ws_url, max_size=4 * 1024 * 1024)

        # Step 1: Receive auth_required message
        auth_required = await websocket.recv()
        auth_msg = json.loads(auth_required)
        logger_ha_communication.debug(
            "Received auth_required message: %s", auth_msg)

        if auth_msg.get("type") != "auth_required":
            await websocket.close()
            raise RuntimeError(f"Unexpected message: {auth_msg}")

        # Step 2: Send authentication
        auth_payload = {
            "type": "auth",
            "access_token": self.api_token
        }
        logger_ha_communication.debug("Sending authentication message")
        await websocket.send(json.dumps(auth_payload))

        # Step 3: Receive auth response
        auth_response = await websocket.recv()
        auth_result = json.loads(auth_response)
        logger_ha_communication.debug(
            "Received auth response: %s", auth_result)

        if auth_result.get("type") != "auth_ok":
            logger.error(
                "WebSocket authentication failed: %s", auth_result
            )
            await websocket.close()
            raise RuntimeError(
                f"Authentication failed: "
                f"{auth_result.get('message', 'Unknown error')}"
            )

        logger_ha_communication.debug("WebSocket authentication successful")

        return websocket, 1  # Return websocket and initial message_id

    async def _websocket_disconnect(self, websocket):
        """Disconnect from HomeAssistant WebSocket API

        Args:
            websocket: WebSocket connection to close
        """
        try:
            await websocket.close()
            logger_ha_communication.debug("WebSocket connection closed")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger_ha_communication.warning(
                "Error closing WebSocket connection: %s", e)

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    # pylint: disable=too-many-nested-blocks
    async def _fetch_hourly_statistics_async(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        websocket=None,
        message_id: int = 1
    ) -> float:
        """Fetch hourly statistics from HomeAssistant WebSocket API

        Uses the WebSocket API to get pre-aggregated hourly consumption data.
        This is the modern, more efficient way to communicate with HomeAssistant.

        Args:
            start_time: Start of time range (will be aligned to hour boundary)
            end_time: End of time range (will be aligned to hour boundary)
            websocket: Optional existing websocket connection to reuse
            message_id: Message ID for WebSocket request (default: 1)

        Returns:
            Dict mapping (weekday, hour) to consumption in Wh

        Raises:
            RuntimeError: If WebSocket connection or API request fails
        """
        # Align to hour boundaries for statistics API
        start_time = start_time.replace(minute=0, second=0, microsecond=0)
        end_time = end_time.replace(minute=0, second=0, microsecond=0)

        # Format timestamps for HomeAssistant API (ISO format)
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()

        logger_ha_details.debug(
            "Fetching hourly statistics via WebSocket: entity=%s, start=%s, end=%s",
            self.entity_id,
            start_iso,
            end_iso)

        # Track if we need to manage the websocket connection
        should_disconnect = False

        try:
            if websocket is None:
                websocket, message_id = await self._websocket_connect()
                should_disconnect = True

            try:
                # Request statistics
                stats_request = {
                    "id": message_id,
                    "type": "recorder/statistics_during_period",
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "statistic_ids": [self.entity_id],
                    "period": "hour"
                }
                logger_ha_details.debug(
                    "Sending statistics request: %s", stats_request)
                await websocket.send(json.dumps(stats_request))

                # Receive statistics response
                stats_response = await websocket.recv()
                stats_result = json.loads(stats_response)
                logger_ha_details.debug(
                    "Received statistics response: id=%s, type=%s, success=%s",
                    stats_result.get("id"),
                    stats_result.get("type"),
                    stats_result.get("success"))

                if not stats_result.get("success"):
                    error_msg = stats_result.get(
                        "error", {}
                    ).get("message", "Unknown error")
                    logger.error("Statistics request failed: %s", error_msg)
                    raise RuntimeError(
                        f"Statistics request failed: {error_msg}"
                    )

                data = stats_result.get("result", {})
                logger_ha_details.debug(
                    "Statistics result contains %d entities", len(data))

                # HomeAssistant statistics API returns dict with entity_id as
                # key
                if not data or self.entity_id not in data:
                    logger_ha_details.warning(
                        "No statistics data returned for entity %s. "
                        "Make sure the entity has long-term statistics enabled.", self.entity_id)
                    return -1

                entity_stats = data[self.entity_id]
                logger_ha_details.debug(
                    "Fetched %d hourly statistics", len(entity_stats))

                # Process statistics into hourly buckets by weekday and hour
                hourly_data: Dict[Tuple[int, int], float] = {}

                for stat in entity_stats:
                    # Parse start timestamp
                    start_ts_value = stat.get('start')
                    if not start_ts_value:
                        logger_ha_details.debug(
                            "Skipping stat entry with no 'start' field: %s", stat)
                        continue

                    # Handle both timestamp formats: Unix timestamp (int/float)
                    # or ISO string
                    if isinstance(start_ts_value, (int, float)):
                        # Unix timestamp (seconds or milliseconds)
                        if start_ts_value > 10000000000:  # Likely milliseconds
                            start_ts = datetime.datetime.fromtimestamp(
                                start_ts_value / 1000.0,
                                tz=self.timezone
                            )
                            logger_ha_details.debug(
                                "Parsed millisecond timestamp %s -> %s",
                                start_ts_value, start_ts
                            )
                        else:  # Likely seconds
                            start_ts = datetime.datetime.fromtimestamp(
                                start_ts_value,
                                tz=self.timezone
                            )
                            logger_ha_details.debug(
                                "Parsed second timestamp %s -> %s",
                                start_ts_value, start_ts
                            )
                    else:
                        # ISO format string
                        start_ts = datetime.datetime.fromisoformat(
                            str(start_ts_value).replace('Z', '+00:00')
                        )
                        logger_ha_details.debug(
                            "Parsed ISO timestamp '%s' -> %s",
                            start_ts_value, start_ts
                        )

                        # Convert to configured timezone
                        if start_ts.tzinfo is None:
                            start_ts = start_ts.replace(tzinfo=self.timezone)
                        else:
                            start_ts = start_ts.astimezone(self.timezone)

                    weekday = start_ts.weekday()
                    hour = start_ts.hour

                    # Get consumption value - use 'sum' for cumulative sensors
                    # 'sum' represents the total change during this hour
                    consumption = stat.get('change')
                    logger_ha_details.debug(
                        "Raw consumption value for %s: change=%s",
                        start_ts.strftime("%Y-%m-%d %H:%M"),
                        stat.get('change'))

                    if consumption is not None:
                        try:
                            consumption = float(consumption)

                            # Apply unit conversion factor (e.g., kWh -> Wh)
                            consumption = consumption * self.unit_conversion_factor

                            if consumption < 0:
                                logger.debug(
                                    "Skipping negative consumption at %s: %.2f Wh",
                                    start_ts, consumption)
                                continue

                            key = (weekday, hour)
                            hourly_data[key] = consumption

                            logger_ha_details.debug(
                                "Stored: weekday=%d, hour=%d (%s): %.2f Wh",
                                weekday,
                                hour,
                                start_ts.strftime("%Y-%m-%d %H:%M"),
                                consumption)
                        except (ValueError, TypeError) as e:
                            logger_ha_details.debug(
                                "Skipping non-numeric consumption: %s (error: %s)", consumption, e)
                            continue

                logger_ha_details.debug(
                    "Processed %d hourly statistics buckets",
                    len(hourly_data))

                # Store summary of collected hourly consumption data and return average consumption
                # value.
                if hourly_data:
                    values = list(hourly_data.values())
                    avg_consumption = sum(values) / len(values)
                    return avg_consumption

                return -1.0

            finally:
                # Only disconnect if we created the connection
                if should_disconnect:
                    await self._websocket_disconnect(websocket)

        except Exception as e:
            logger.error(
                "Failed to fetch statistics from HomeAssistant: %s", e)
            raise RuntimeError(
                f"HomeAssistant WebSocket request failed: {e}") from e

    def _fetch_hourly_statistics(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime
    ) -> float:
        """Synchronous wrapper for async statistics fetch

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Float
        """
        # Runs on a private event loop that is closed again afterwards.
        # run_coroutine() refuses to run from an async context, which this
        # method must not be called from.
        return run_coroutine(
            self._fetch_hourly_statistics_async(start_time, end_time)
        )

    def _update_cache_with_statistics(
        self,
        now: datetime.datetime,
        history_periods: Dict[int, float]
    ) -> int:
        """Calculate weighted statistics and update cache

        Args:
            now: Current timestamp (full hour) used as reference for hour offsets
            history_periods: Dict mapping hour offset to consumption value in Wh

        Returns:
            Number of cache entries updated
        """
        # Calculate weighted average for each hour and store in cache
        with self._cache_lock:
            logger.debug(
                "Updating cache with calculated statistics for %d periods",
                len(history_periods)
            )
            updated_count = 0
            for hour_offset, consumption in history_periods.items():
                future_time = now + datetime.timedelta(hours=hour_offset)
                weekday = future_time.weekday()
                hour = future_time.hour
                cache_key = self._get_cache_key(weekday, hour)

                # Apply multiplier
                adjusted_consumption = consumption * self.multiplier

                # Update cache
                self.consumption_cache[cache_key] = adjusted_consumption
                updated_count += 1

                logger.debug(
                    "Updated cache: key=%s (weekday=%d, hour=%d), "
                    "consumption=%.2f Wh (adjusted: %.2f Wh)",
                    cache_key, weekday, hour, consumption, adjusted_consumption
                )

        logger.info("Updated %d cache entries", updated_count)
        return updated_count

    def _get_reference_slots(self) -> Dict[int, int]:
        """Returns a dict, which is a mapping against
                  self.history_days and self.history_weights
        """
        reference_slots = {}
        for idx, day_offset in enumerate(self.history_days):
            reference_slots[day_offset] = self.history_weights[idx]
        return reference_slots

    # pylint: disable=too-many-locals,too-many-branches
    def _collect_history_periods(
        self,
        loop,
        missing_periods,
        now: datetime.datetime,
        reference_slots: Dict[int, int],
        history_periods: Dict[int, float]
    ) -> None:
        """Fetch history data for all missing hour slots over one connection.

        Opens a single WebSocket connection, queries every missing hour slot
        for every configured history day, and writes the weighted result into
        history_periods.

        Args:
            loop: Event loop used to drive the coroutines. Owned by the caller.
            missing_periods: Hour offsets that are not cached yet.
            now: Reference timestamp, aligned to a full hour.
            reference_slots: Mapping of history day offset to weight.
            history_periods: Output dict, filled with hour offset to Wh.
        """
        websocket = None

        try:
            websocket, message_id = loop.run_until_complete(
                self._websocket_connect())
            logger_ha_communication.debug(
                "WebSocket connected for bulk data fetch")

            for fetch_hour in missing_periods:
                # start time is now + fetch_hour
                basis_start_time = now + datetime.timedelta(hours=fetch_hour)
                basis_end_time = basis_start_time + datetime.timedelta(hours=1)

                # Now fetch each history_days as offset on basis_start +
                # endtime
                logger_ha_details.debug(
                    "Fetching history data for hour offset %d (basis time %s)",
                    fetch_hour,
                    basis_start_time)
                slot_results = {}
                for history_day in reference_slots:
                    start_time = basis_start_time + datetime.timedelta(
                        days=history_day
                    )
                    end_time = basis_end_time + datetime.timedelta(
                        days=history_day
                    )

                    try:
                        hourly_data = loop.run_until_complete(
                            self._fetch_hourly_statistics_async(
                                start_time, end_time, websocket, message_id
                            )
                        )
                        message_id += 1  # Increment for next request

                        if hourly_data > -1:
                            logger_ha_details.debug(
                                "Fetched history data for %d days offset: %s",
                                history_day, hourly_data)
                            slot_results[history_day] = hourly_data
                        else:
                            logger_ha_details.warning(
                                "No data fetched for hour offset %d with day offset %d",
                                fetch_hour,
                                history_day)
                    except (RuntimeError, ValueError) as e:
                        logger.error(
                            "Failed to fetch statistics for %d days offset: %s", history_day, e)
                        # Continue with other periods even if one fails
                        continue

                # Now calculate weighted statistics for this hour slot
                if slot_results:
                    weight_sum = 0
                    summary_results = 0
                    for history_day in self.history_days:
                        if history_day in slot_results:
                            weight_sum += reference_slots[history_day]
                            summary_results += (
                                slot_results[history_day] *
                                reference_slots[history_day]
                            )

                    if weight_sum > 0:
                        # Store with actual hour offset as key
                        history_periods[fetch_hour] = summary_results / weight_sum

                else:
                    # No data fetched for this hour
                    # Stop processing further
                    logger.warning(
                        "No statistics data fetched for hour offset %d, ending collect",
                        fetch_hour)
                    break

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error during bulk data fetch: %s", e)
        finally:
            # Disconnect websocket
            if websocket is not None:
                loop.run_until_complete(self._websocket_disconnect(websocket))

    def refresh_data_with_limit(self, hours: int) -> None:
        """Refresh historical data with specified hour limit

        Args:
            hours: Number of hours to refresh (typically up to 48)
        """
        logger.info("Refreshing consumption forecast data from HomeAssistant")

        now = datetime.datetime.now(tz=self.timezone)

        # always have the next 48 hours in the forecast
        # Create a list of cache_keys to ensure they are present
        cache_keys = [
            self._get_cache_key(
                (now + datetime.timedelta(hours=h)).weekday(),
                (now + datetime.timedelta(hours=h)).hour
            )
            for h in range(hours)
        ]

        # Create a list of missing history data periods
        missing_periods = []
        for h in range(hours):
            if cache_keys[h] not in self.consumption_cache:
                missing_periods.append(h)

        if missing_periods:
            logger.info(
                "Collecting data for missing hours: %s",
                missing_periods)
        else:
            logger.debug(
                "All forecast hours present in cache, no refresh needed")
            return

        # now as full hour
        now = now.replace(minute=0, second=0, microsecond=0)

        reference_slots = self._get_reference_slots()
        history_periods = {}  # Dict mapping hour offset to consumption value

        # Connect to WebSocket once for all requests. The loop is reused for
        # every request on that connection and closed when the block exits.
        with managed_event_loop() as loop:
            self._collect_history_periods(
                loop, missing_periods, now, reference_slots, history_periods
            )

        if not history_periods:
            logger.error(
                "No statistics data could be fetched, forecast unavailable")
            return

        # Update cache using dict with hour offsets as keys
        updated_count = self._update_cache_with_statistics(now, history_periods)

        logger.info(
            "Successfully updated consumption forecast cache with %d hour slots",
            updated_count)

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements

    def refresh_data(self) -> None:
        """Refresh historical data and update cache

        Fetches hourly statistics from HomeAssistant for configured time periods,
        calculates weighted averages, and updates the cache.
        """
        self.refresh_data_with_limit(MAX_FORECAST_HOURS)

    def _get_forecast_native(self, hours: int) -> Dict[int, float]:
        """Get hour-aligned forecast at native (60-minute) resolution.

        Args:
            hours: Number of hours to forecast

        Returns:
            Dict mapping hour index to energy value (Wh per hour)
            Index 0 = start of current hour
        """
        # Check if cache has all required keys for the forecast hours
        # Calculate now inside the lock to avoid race conditions around hour
        # boundaries
        missing_keys = False
        with self._cache_lock:
            now = datetime.datetime.now(tz=self.timezone)
            # Align to start of current hour
            now_aligned = now.replace(minute=0, second=0, microsecond=0)
            for h in range(hours):
                future_time = now_aligned + datetime.timedelta(hours=h)
                cache_key = self._get_cache_key(
                    future_time.weekday(), future_time.hour)
                if cache_key not in self.consumption_cache:
                    missing_keys = True
                    break

        if missing_keys:
            logger.info(
                "Cache missing required keys, refreshing consumption forecast data")
            self.refresh_data_with_limit(hours)

        # Generate hour-aligned forecast for requested hours
        prediction = {}

        for h in range(hours):
            future_time = now_aligned + datetime.timedelta(hours=h)
            weekday = future_time.weekday()
            hour = future_time.hour
            cache_key = self._get_cache_key(weekday, hour)

            # Get consumption from cache
            with self._cache_lock:
                consumption = self.consumption_cache.get(cache_key)

            if consumption is not None:
                prediction[h] = consumption
            else:
                logger_ha_details.warning(
                    "No cached data for %s (weekday=%d, hour=%d)",
                    cache_key, weekday, hour
                )
                # Break here.
                # Cache miss - we will handle missing keys later
                break

        if prediction:
            logger.debug(
                "Generated %d hour forecast (hour-aligned): avg=%.1f Wh, min=%.1f Wh, max=%.1f Wh",
                hours,
                np.mean(list(prediction.values())),
                min(prediction.values()),
                max(prediction.values())
            )
        else:
            logger.error("Generated empty forecast")
            raise RuntimeError("No consumption forecast data available")

        return prediction
