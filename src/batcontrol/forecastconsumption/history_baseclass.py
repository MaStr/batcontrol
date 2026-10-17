"""Base class for consumption forecasts built from historical hourly data.

The hourly profile is the same for every source: for each missing hour slot
of the forecast horizon, the energy of the same hour on the configured
history days (history_days) is read, averaged with history_weights and stored
in a TTL cache keyed by weekday and hour. Only reading one hour of history is
source specific and left to the subclasses via two hooks:

    _history_session(earliest_start, latest_end)
        Context manager wrapped around one refresh, e.g. to open a connection
        or to preload the whole time range with a single request.
    _fetch_slot_value(start_time, end_time)
        Energy in Wh of the hour start_time to end_time, -1 without data.
"""

import contextlib
import datetime
import logging
import threading
from abc import abstractmethod
from typing import Dict, List, Optional

import numpy as np
from cachetools import TTLCache

from .baseclass import ForecastConsumptionBaseclass

logger = logging.getLogger(__name__)
logger_details = logging.getLogger(
    "batcontrol.forecastconsumption.history_baseclass.details")

MAX_FORECAST_HOURS = 48


# pylint: disable=too-many-instance-attributes
class ForecastConsumptionHistoryBaseclass(ForecastConsumptionBaseclass):
    """Hourly consumption profile from history, source independent part.

    Attributes:
        history_days: List of days to look back (e.g., [-7, -14, -21])
        history_weights: Weights for each history period (e.g., [1, 1, 1])
        multiplier: Multiplier applied to all forecast values
        consumption_cache: TTLCache storing consumption values by weekday_hour key
        cache_ttl_hours: TTL for cached data in hours
    """

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        timezone,
        history_days: Optional[List[int]] = None,
        history_weights: Optional[List[int]] = None,
        cache_ttl_hours: float = 48.0,
        multiplier: float = 1.0,
        target_resolution: int = 60
    ) -> None:
        """Initialize the history based forecaster

        Args:
            timezone: Timezone object for data processing
            history_days: List of negative day offsets to fetch (e.g., [-7, -14, -21])
            history_weights: Weight for each history period (1-10), same length as history_days
            cache_ttl_hours: Time-to-live for cached statistics in hours (default: 48)
            multiplier: Multiplier applied to all forecast values (default: 1.0)
            target_resolution: Target resolution in minutes (15 or 60)
        """
        # Hourly history, 60-minute native resolution
        super().__init__(
            timezone,
            target_resolution=target_resolution,
            native_resolution=60)

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
                raise ValueError(
                    f"History weights must be between 1 and 10, got {weight}")

        # Initialize cache with TTL
        # Cache key format: "weekday_hour" (e.g., "0_14" for Monday 14:00)
        # Cache stores consumption value in Wh for each hour slot
        # maxsize: 168 = 7 days * 24 hours (one week of hourly data)
        self.cache_ttl_hours = cache_ttl_hours
        cache_ttl_seconds = int(cache_ttl_hours * 3600)
        self.consumption_cache: TTLCache = TTLCache(
            maxsize=168, ttl=cache_ttl_seconds)
        self._cache_lock = threading.Lock()

    @abstractmethod
    def _history_session(
        self,
        earliest_start: datetime.datetime,
        latest_end: datetime.datetime
    ) -> contextlib.AbstractContextManager:
        """Context manager around one refresh of the history data.

        Subclasses implement this as a generator decorated with
        contextlib.contextmanager.

        Args:
            earliest_start: Start of the earliest hour _fetch_slot_value may ask for
            latest_end: End of the latest hour _fetch_slot_value may ask for
        """

    @abstractmethod
    def _fetch_slot_value(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime
    ) -> float:
        """Read the energy of one hour from the source.

        Called inside _history_session.

        Returns:
            Energy in Wh, -1 if the source has no data for this hour

        Raises:
            RuntimeError, ValueError: If the request fails. The slot is
                skipped, the other slots are still read.
        """

    def _get_cache_key(self, weekday: int, hour: int) -> str:
        """Generate cache key from weekday and hour

        Args:
            weekday: Day of week (0=Monday, 6=Sunday)
            hour: Hour of day (0-23)

        Returns:
            Cache key in format "weekday_hour" (e.g., "0_14" for Monday 14:00)
        """
        return f"{weekday}_{hour}"

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

    def _weighted_slot_average(
        self,
        slot_results: Dict[int, float],
        reference_slots: Dict[int, int]
    ) -> Optional[float]:
        """Weighted average over the history days that returned data

        Args:
            slot_results: Dict mapping history day offset to consumption in Wh
            reference_slots: Mapping of history day offset to weight

        Returns:
            Weighted average, None if no history day has data
        """
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
            return summary_results / weight_sum
        return None

    def _collect_history_periods(
        self,
        missing_periods,
        now: datetime.datetime,
        reference_slots: Dict[int, int],
        history_periods: Dict[int, float]
    ) -> None:
        """Read the history for all missing hour slots.

        Queries every missing hour slot for every configured history day and
        writes the weighted result into history_periods. Must run inside
        _history_session.

        Args:
            missing_periods: Hour offsets that are not cached yet.
            now: Reference timestamp, aligned to a full hour.
            reference_slots: Mapping of history day offset to weight.
            history_periods: Output dict, filled with hour offset to Wh.
        """
        for fetch_hour in missing_periods:
            # start time is now + fetch_hour
            basis_start_time = now + datetime.timedelta(hours=fetch_hour)
            basis_end_time = basis_start_time + datetime.timedelta(hours=1)

            # Now fetch each history_days as offset on basis_start +
            # endtime
            logger_details.debug(
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
                    hourly_data = self._fetch_slot_value(start_time, end_time)

                    if hourly_data > -1:
                        logger_details.debug(
                            "Fetched history data for %d days offset: %s",
                            history_day, hourly_data)
                        slot_results[history_day] = hourly_data
                    else:
                        logger_details.warning(
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
                average = self._weighted_slot_average(
                    slot_results, reference_slots)
                if average is not None:
                    # Store with actual hour offset as key
                    history_periods[fetch_hour] = average
            else:
                # No data fetched for this hour
                # Stop processing further
                logger.warning(
                    "No statistics data fetched for hour offset %d, ending collect",
                    fetch_hour)
                break

    def refresh_data_with_limit(self, hours: int) -> None:
        """Refresh historical data with specified hour limit

        Args:
            hours: Number of hours to refresh (typically up to 48)
        """
        logger.info("Refreshing consumption forecast data from history")

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

        # Same arithmetic as in _collect_history_periods
        earliest_start = (
            now + datetime.timedelta(hours=min(missing_periods))
            + datetime.timedelta(days=min(self.history_days)))
        latest_end = (
            now + datetime.timedelta(hours=max(missing_periods) + 1)
            + datetime.timedelta(days=max(self.history_days)))

        try:
            with self._history_session(earliest_start, latest_end):
                self._collect_history_periods(
                    missing_periods, now, reference_slots, history_periods
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error during bulk data fetch: %s", e)

        if not history_periods:
            logger.error(
                "No statistics data could be fetched, forecast unavailable")
            return

        # Update cache using dict with hour offsets as keys
        updated_count = self._update_cache_with_statistics(
            now, history_periods)

        logger.info(
            "Successfully updated consumption forecast cache with %d hour slots",
            updated_count)

    def refresh_data(self) -> None:
        """Refresh historical data and update cache

        Fetches the history for the configured time periods, calculates
        weighted averages, and updates the cache.
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
        if hours > MAX_FORECAST_HOURS:
            logger.warning(
                "Requested %d forecast hours, limiting to %d hours",
                hours, MAX_FORECAST_HOURS)
            hours = MAX_FORECAST_HOURS

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
                logger_details.warning(
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
