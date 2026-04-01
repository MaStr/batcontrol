"""HomeAssistant Solar Forecast ML integration

This module provides solar forecasting using ML-based forecast data from
HomeAssistant Solar Forecast ML integration (HACS).

Based on HACS integration: https://zara-toorox.github.io/
Supported sensors:
  - sensor.solar_forecast_ml_prognose_nachste_stunde  (hours_list / hour_N format)
  - sensor.solar_forecast_ml_evcc_solar_prognose      (forecast list with start/end/value)

"""

import asyncio
import datetime
import json
import logging
from typing import Dict, Optional

from websockets.asyncio.client import connect
from .baseclass import ForecastSolarBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module')

logger_ha_details = logging.getLogger(
    "batcontrol.forecastsolar.forecast_homeassistant_ml.details")
logger_ha_communication = logging.getLogger(
    "batcontrol.forecastsolar.forecast_homeassistant_ml.communication")


# pylint: disable=too-many-instance-attributes
class ForecastSolarHomeAssistantML(ForecastSolarBaseclass):
    """Provides solar forecast from HomeAssistant Solar Forecast ML integration

    This class fetches solar forecast data from HomeAssistant Solar Forecast ML
    integration sensor (typically sensor.solar_forecast_ml_prognose_nachste_stunde).
    The sensor provides hourly ML-based predictions in its attributes.

    Attributes:
        base_url: HomeAssistant base URL
        api_token: HomeAssistant API access token
        entity_id: Entity ID of the sensor providing forecast
        timezone: Timezone for data processing
        sensor_unit: Unit of the sensor ('wh', 'kwh', or 'auto')
        unit_conversion_factor: Factor to convert sensor values to Wh
    """

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        pvinstallations: list,
        timezone,
        base_url: str,
        api_token: str,
        entity_id: str,
        min_time_between_api_calls: int = 21600,
        delay_evaluation_by_seconds: int = 300,
        sensor_unit: Optional[str] = "auto",
        target_resolution: int = 60
    ) -> None:
        """Initialize HomeAssistant solar forecast provider

        Args:
            pvinstallations: List of PV installation dicts (for compatibility with baseclass)
                Each dict should contain 'name' key for logging
            timezone: Timezone object for data processing
            base_url: HomeAssistant base URL (e.g., "ws://192.168.1.100:8123")
            api_token: HomeAssistant Long-Lived Access Token
            entity_id: Entity ID of forecast sensor (e.g., "sensor.solar_forecast_ml_...")
            min_time_between_api_calls: Minimum seconds between API calls (default: 6 hours)
            delay_evaluation_by_seconds: Delay before first evaluation (default: 5 min)
            sensor_unit: Optional sensor unit ('auto', 'Wh', or 'kWh').
                        If set to 'Wh' or 'kWh', skips auto-detection.
                        If set to 'auto', queries Home Assistant to detect unit.
                        Default: auto (auto-detect)
            target_resolution: Target resolution in minutes (15 or 60)
        """
        # Initialize baseclass with native 60-minute resolution
        super().__init__(
            pvinstallations,
            timezone,
            min_time_between_api_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=60
        )

        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.entity_id = entity_id

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

        # Query sensor to determine unit and set conversion factor
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
            "Initialized HomeAssistant Solar Forecast ML provider: "
            "entity_id=%s, sensor_unit=%s, "
            "unit_conversion_factor=%0.1f",
            entity_id, self.sensor_unit or 'auto',
            self.unit_conversion_factor
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
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No event loop in current thread, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self._check_sensor_unit_async())

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
                logger.info(
                    "Unit is kWh, will multiply values by 1000 to convert to Wh")
                return 1000.0

            if unit is None:
                logger.warning(
                    "Entity '%s' has no unit_of_measurement. "
                    "Assuming values are already in Wh "
                    "(typical for evcc Solar-Prognose sensor). "
                    "Set 'sensor_unit: Wh' in config to suppress this warning.",
                    self.entity_id
                )
                return 1.0

            raise ValueError(
                f"Unsupported unit_of_measurement '{unit}' for entity "
                f"'{self.entity_id}'. Only 'Wh' and 'kWh' are supported.")

        finally:
            await self._websocket_disconnect(websocket)

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

    def get_raw_data_from_provider(self, pvinstallation_name: str) -> dict:
        """Fetch raw entity state from HomeAssistant via WebSocket API

        Args:
            pvinstallation_name: Name of PV installation (for baseclass compatibility)

        Returns:
            Dict with entity state including attributes

        Raises:
            RuntimeError: If WebSocket connection or API request fails
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self._fetch_entity_state_async())

    async def _fetch_entity_state_async(self) -> dict:
        """Async fetch of entity state from HomeAssistant

        Returns:
            Dict with entity state and attributes

        Raises:
            RuntimeError: If entity not found or request fails
        """
        logger_ha_details.debug(
            "Fetching entity state for: %s", self.entity_id)

        websocket, message_id = await self._websocket_connect()

        try:
            # Request all states to find our entity
            states_request = {
                "id": message_id,
                "type": "get_states"
            }
            logger_ha_details.debug(
                "Sending get_states request for entity: %s", self.entity_id)
            await websocket.send(json.dumps(states_request))

            # Receive states response
            states_response = await websocket.recv()
            states_result = json.loads(states_response)
            logger_ha_details.debug(
                "Received states response: id=%s, type=%s, success=%s",
                states_result.get("id"),
                states_result.get("type"),
                states_result.get("success"))

            if not states_result.get("success"):
                error_msg = states_result.get(
                    "error", {}
                ).get("message", "Unknown error")
                logger.error("Get states request failed: %s", error_msg)
                raise RuntimeError(
                    f"Get states request failed: {error_msg}"
                )

            # Find our entity in the results
            states = states_result.get("result", [])
            entity_state = None
            for state in states:
                if state.get("entity_id") == self.entity_id:
                    entity_state = state
                    break

            if entity_state is None:
                raise RuntimeError(
                    f"Entity '{self.entity_id}' not found in HomeAssistant"
                )

            logger_ha_details.debug(
                "Found entity state: %s", json.dumps(entity_state, indent=2))

            return entity_state

        finally:
            await self._websocket_disconnect(websocket)

    def get_forecast_from_raw_data(self) -> Dict[int, float]:
        """Parse forecast from cached raw data

        Returns hour-aligned forecast at native 60-minute resolution.
        Baseclass will handle conversion to 15-min if needed.

        Returns:
            Dict mapping hour index (0, 1, 2, ...) to generation in Wh
            Index 0 = start of current hour

        Raises:
            RuntimeError: If no valid forecast data available
        """
        # Get raw data from cache (stored by baseclass)
        raw_data = self.get_raw_data(self.pvinstallations[0]['name'])

        if not raw_data:
            logger.warning('No raw data available from cache')
            return {}

        # Parse forecast data from attributes
        attributes = raw_data.get("attributes", {})

        try:
            forecast_dict = self._parse_forecast_from_attributes(attributes)

            if forecast_dict:
                values = list(forecast_dict.values())
                logger.debug(
                    "Parsed %d hour forecast: avg=%.1f Wh, min=%.1f Wh, max=%.1f Wh",
                    len(forecast_dict),
                    sum(values) / len(values) if values else 0,
                    min(values) if values else 0,
                    max(values) if values else 0
                )
            else:
                logger.error("Parsed empty forecast from attributes")
                raise RuntimeError(
                    "No solar forecast data available in entity attributes")

            return forecast_dict

        except Exception as e:
            logger.error("Failed to parse forecast from attributes: %s", e)
            raise RuntimeError(f"Failed to parse forecast: {e}") from e

    def _parse_forecast_evcc_entry(
        self,
        entry: dict,
        current_hour: "datetime.datetime"
    ) -> Optional[tuple]:
        """Parse a single evcc-style forecast entry dict.

        Args:
            entry: Dict with at least 'start' and 'value' keys.
            current_hour: Timezone-aware datetime truncated to the current hour.

        Returns:
            (hour_offset, wh_value) tuple, or None if the entry should be skipped.
        """
        start_str = entry.get("start")
        value = entry.get("value")

        if start_str is None or value is None:
            return None

        # Normalize UTC 'Z' suffix for Python 3.9/3.10 fromisoformat compatibility
        if isinstance(start_str, str) and start_str.endswith("Z"):
            start_str = start_str[:-1] + "+00:00"

        entry_start = datetime.datetime.fromisoformat(start_str)
        # Naive timestamps are assumed to be in the local timezone
        if entry_start.tzinfo is None:
            entry_start = self.timezone.localize(entry_start)
        else:
            entry_start = entry_start.astimezone(self.timezone)

        delta = entry_start - current_hour
        # Floor division keeps negative deltas negative (avoids int() truncation pitfall)
        hour_offset = int(delta.total_seconds() // 3600)

        if hour_offset < 0:
            return None  # Past hour — skip

        wh_value = float(value) * self.unit_conversion_factor
        return hour_offset, wh_value

    def _parse_forecast_format1(
        self,
        forecast_list: list
    ) -> Dict[int, float]:
        """Parse Format 1: evcc 'forecast' list with {start, end, value} entries.

        Entries whose 'start' timestamp is before the current hour are skipped.
        Timestamps are converted to hour offsets relative to the current hour.

        Args:
            forecast_list: List of dicts from the 'forecast' attribute.

        Returns:
            Dict mapping hour offset (0 = current hour) to generation in Wh.
            Empty dict if no future entries are found.
        """
        forecast_dict: Dict[int, float] = {}
        now = datetime.datetime.now(self.timezone)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        logger_ha_details.debug(
            "Parsing forecast from 'forecast' list (%d entries)", len(forecast_list)
        )

        for entry in forecast_list:
            if not isinstance(entry, dict):
                continue
            try:
                result = self._parse_forecast_evcc_entry(entry, current_hour)
            except (ValueError, TypeError, OverflowError) as exc:
                logger_ha_details.debug(
                    "Skipping entry with start=%s: %s", entry.get("start"), exc
                )
                continue
            if result is None:
                continue
            hour_offset, wh_value = result
            forecast_dict[hour_offset] = wh_value
            logger_ha_details.debug(
                "Offset %d (start=%s): %.2f Wh", hour_offset, entry.get("start"), wh_value
            )

        if forecast_dict:
            # Fill missing offsets with 0.0 so the dict has consecutive keys
            # 0..max_offset. This ensures the baseclass length validation
            # (requires >= 12 consecutive intervals) works correctly.
            max_offset = max(forecast_dict.keys())
            for gap in range(max_offset + 1):
                if gap not in forecast_dict:
                    forecast_dict[gap] = 0.0

            values = list(forecast_dict.values())
            logger.debug(
                "Parsed %d slots from 'forecast' list: avg=%.1f Wh, min=%.1f Wh, max=%.1f Wh",
                len(forecast_dict),
                sum(values) / len(values),
                min(values),
                max(values),
            )
        else:
            logger_ha_details.warning("'forecast' list present but no valid future entries found")

        return forecast_dict

    def _parse_forecast_hours_list(self, hours_list: list) -> Dict[int, float]:
        """Parse Format 2: hours_list array with {time, kwh} entries.

        Args:
            hours_list: List of dicts from the 'hours_list' attribute.

        Returns:
            Dict mapping hour index (0-based) to generation in Wh.
            Empty dict if no valid entries found.
        """
        forecast_dict: Dict[int, float] = {}
        logger_ha_details.debug(
            "Parsing forecast from hours_list (%d entries)", len(hours_list))

        for hour_idx, entry in enumerate(hours_list):
            if not isinstance(entry, dict):
                logger_ha_details.debug(
                    "Skipping non-dict entry in hours_list: %s", entry)
                continue

            kwh_value = entry.get("kwh")
            if kwh_value is None:
                logger_ha_details.debug(
                    "Skipping entry without 'kwh' key: %s", entry)
                continue

            try:
                kwh_value = float(kwh_value)
                wh_value = kwh_value * self.unit_conversion_factor
                forecast_dict[hour_idx] = wh_value
                logger_ha_details.debug(
                    "Hour %d: %.2f kWh -> %.2f Wh", hour_idx, kwh_value, wh_value)
            except (ValueError, TypeError) as exc:
                logger_ha_details.debug(
                    "Skipping invalid kWh value in hours_list: %s (error: %s)",
                    kwh_value, exc)

        if not forecast_dict:
            logger_ha_details.warning("hours_list present but no valid entries parsed")
        return forecast_dict

    def _parse_forecast_hour_n(self, attributes: dict) -> Dict[int, float]:
        """Parse Format 3 (fallback): hour_1, hour_2, ... attributes.

        Args:
            attributes: Full sensor attributes dict.

        Returns:
            Dict mapping hour index (0-based) to generation in Wh.
        """
        forecast_dict: Dict[int, float] = {}
        logger_ha_details.debug("Trying fallback hour_N attribute format")
        hour_idx = 1
        while True:
            hour_key = f"hour_{hour_idx}"
            if hour_key not in attributes:
                break

            kwh_value = attributes.get(hour_key)
            if kwh_value is None:
                logger_ha_details.debug("Skipping missing %s", hour_key)
                hour_idx += 1
                continue

            try:
                kwh_value = float(kwh_value)
                wh_value = kwh_value * self.unit_conversion_factor
                # hour_idx is 1-based in attributes, 0-based in forecast_dict
                hour_time_key = f"hour_{hour_idx}_time"
                forecast_dict[hour_idx - 1] = wh_value
                logger_ha_details.debug(
                    "Hour %d (%s): %.2f kWh -> %.2f Wh",
                    hour_idx - 1, attributes.get(hour_time_key, "?"),
                    kwh_value, wh_value)
            except (ValueError, TypeError) as exc:
                logger_ha_details.debug(
                    "Skipping invalid kWh value for %s: %s (error: %s)",
                    hour_key, kwh_value, exc)

            hour_idx += 1
        return forecast_dict

    def _parse_forecast_from_attributes(
        self,
        attributes: dict
    ) -> Dict[int, float]:
        """Parse forecast data from sensor attributes

        Supports multiple formats:
        1. Primary: forecast array with {start, end, value} objects (evcc Solar Forecast style)
           - Uses absolute timestamps; values are mapped to hour offsets from now
           - Typically used with sensor.solar_forecast_ml_evcc_solar_prognose
        2. Secondary: hours_list array with {time, kwh} objects
           - Used with sensor.solar_forecast_ml_prognose_nachste_stunde
        3. Fallback: hour_1, hour_2, ... attributes

        Args:
            attributes: Sensor attributes dict from HomeAssistant

        Returns:
            Dict mapping hour index (0, 1, 2, ...) to generation in Wh

        Raises:
            ValueError: If no valid forecast data found
        """
        # Format 1: forecast list with {start, end, value} - evcc Solar Forecast style
        forecast_list = attributes.get("forecast")
        if forecast_list and isinstance(forecast_list, list):
            has_expected_entry = any(
                isinstance(entry, dict) and "start" in entry and "value" in entry
                for entry in forecast_list
            )
            if has_expected_entry:
                result = self._parse_forecast_format1(forecast_list)
                if result:
                    return result

        # Format 2: hours_list (existing primary format)
        hours_list = attributes.get("hours_list")
        if hours_list and isinstance(hours_list, list) and len(hours_list) > 0:
            result = self._parse_forecast_hours_list(hours_list)
            if result:
                return result

        # Format 3 (fallback): hour_1, hour_2, ... attributes
        forecast_dict = self._parse_forecast_hour_n(attributes)

        if not forecast_dict:
            raise ValueError(
                "Could not parse any forecast data from sensor attributes. "
                "Expected one of: "
                "(1) 'forecast' list with {start, value} entries "
                "(evcc Solar-Prognose format, e.g. sensor.solar_forecast_ml_evcc_solar_prognose); "
                "(2) 'hours_list' array with {kwh} entries; "
                "(3) 'hour_N' attributes. "
                "If using the evcc format, check that the 'forecast' list contains "
                "future entries (past-only entries are skipped)."
            )

        return forecast_dict
