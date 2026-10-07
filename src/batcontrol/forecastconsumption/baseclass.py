"""Base class for consumption forecast providers with resolution handling.

This module provides the base class that all consumption forecast providers inherit from.
It implements automatic resolution conversion and current-interval alignment similar to
solar and tariff forecasts.
"""

import datetime
import logging
import math
import threading
from abc import abstractmethod
from typing import Optional
from .forecastconsumption_interface import ForecastConsumptionInterface
from ..interval_utils import upsample_forecast, downsample_to_hourly

logger = logging.getLogger(__name__)


class ForecastConsumptionBaseclass(ForecastConsumptionInterface):
    """Base class for consumption forecast providers.

    Provides automatic resolution handling:
    - Providers declare their native_resolution (15 or 60 minutes)
    - Baseclass converts between resolutions automatically
    - Baseclass shifts indices to current-interval alignment

    Subclasses must:
    1. Set self.native_resolution in __init__
    2. Implement _get_forecast_native(hours) to return hour-aligned data

    Note:
        get_forecast() is called with a number of slots at target_resolution,
        _get_forecast_native() with the matching number of hours.
    """

    def __init__(self, timezone, target_resolution: int = 60,
                 native_resolution: int = 60) -> None:
        """Initialize consumption forecast baseclass.

        Args:
            timezone: Timezone for timestamp handling
            target_resolution: Target resolution in minutes (what core.py expects: 15 or 60)
            native_resolution: Native resolution in minutes (what provider returns: 15 or 60)
        """
        self.timezone = timezone
        self.target_resolution = target_resolution
        self.native_resolution = native_resolution
        self._forecast_lock = threading.Lock()

        logger.info(
            '%s: native_resolution=%d min, target_resolution=%d min',
            self.__class__.__name__,
            self.native_resolution,
            self.target_resolution
        )

    @abstractmethod
    def _get_forecast_native(self, hours: int) -> dict[int, float]:
        """Get forecast at native resolution, hour-aligned.

        Args:
            hours: Number of hours to forecast (at hourly resolution)

        Returns:
            Dict mapping interval index to energy value (Wh per interval)
            Index 0 = start of current hour

        Note:
            This method should return hour-aligned data. The baseclass will:
            1. Convert resolution if needed
            2. Shift indices to current interval
        """

    def refresh_data(self) -> None:
        """Refresh forecast data from source.

        Default implementation is a no-op. Providers that need to refresh
        data (e.g., from APIs) should override this method.
        """

    def get_forecast(self, requested_slots: int) -> dict[int, float]:
        """Get forecast with automatic resolution handling.

        The request is resolution-neutral: it asks for a number of slots at
        the target_resolution the provider was initialized with. Converting
        that into the amount of source data to read is done here, so each
        provider only ever sees its own native unit (hours).

        Args:
            requested_slots: Number of slots to forecast, counted at
                target_resolution and starting with the current slot.
                This matches the slot indices used by the price and
                production forecasts in core.py.

        Returns:
            Dict where [0] = current slot, [1] = next slot, etc.
            At most `requested_slots` entries, fewer if the provider has
            less data. Ready for core.py to factorize [0] based on elapsed
            time.
        """
        with self._forecast_lock:
            # The slots already elapsed in the current hour are dropped by
            # _shift_to_current_interval, so they have to be fetched on top of
            # the requested ones.
            offset = self._current_interval_in_hour()
            hours = math.ceil(
                (requested_slots + offset) * self.target_resolution / 60)

            # Get hour-aligned forecast from provider at native resolution
            native_forecast = self._get_forecast_native(hours)

            if not native_forecast:
                logger.warning(
                    '%s: No data returned from _get_forecast_native',
                    self.__class__.__name__)
                return {}

            # Convert resolution if needed
            converted_forecast = self._convert_resolution(
                native_forecast, hours)

            # Shift indices to start from CURRENT interval
            current_aligned_forecast = self._shift_to_current_interval(
                converted_forecast, offset)

            # Drop the slots fetched only to compensate the shift
            return {idx: value
                    for idx, value in current_aligned_forecast.items()
                    if idx < requested_slots}

    def _current_interval_in_hour(self) -> int:
        """Number of target-resolution intervals already elapsed in this hour.

        At 10:20 with a target resolution of 15 minutes this is 1, because the
        current interval 10:15-10:30 is the second one of the hour.
        """
        now = datetime.datetime.now(
            datetime.timezone.utc).astimezone(
            self.timezone)
        return now.minute // self.target_resolution

    # pylint: disable=unused-argument
    def _convert_resolution(
            self, forecast: dict[int, float], hours: int) -> dict[int, float]:
        """Convert forecast between resolutions if needed.

        Args:
            forecast: Hour-aligned forecast data at native resolution
            hours: Number of hours originally requested

        Returns:
            Forecast data at target resolution (still hour-aligned)
        """
        if self.native_resolution == self.target_resolution:
            return forecast

        if self.native_resolution == 60 and self.target_resolution == 15:
            logger.debug(
                '%s: Upsampling 60min -> 15min using constant distribution',
                self.__class__.__name__)
            # Use constant distribution for consumption (no interpolation)
            return upsample_forecast(
                forecast, target_resolution=15, method='constant')

        if self.native_resolution == 15 and self.target_resolution == 60:
            logger.debug('%s: Downsampling 15min -> 60min by summing quarters',
                         self.__class__.__name__)
            return downsample_to_hourly(forecast)

        logger.error('%s: Cannot convert %d min -> %d min',
                     self.__class__.__name__,
                     self.native_resolution,
                     self.target_resolution)
        return forecast

    def _shift_to_current_interval(
            self, forecast: dict[int, float],
            offset: Optional[int] = None) -> dict[int, float]:
        """Shift hour-aligned indices to current-interval alignment.

        At time 10:20, if target resolution is 15 min:
        - Provider returns: [0]=10:00-10:15, [1]=10:15-10:30, [2]=10:30-10:45, ...
        - We're in interval 1 (10:15-10:30)
        - Output: [0]=10:15-10:30, [1]=10:30-10:45, ... (interval 0 dropped)

        Args:
            forecast: Hour-aligned forecast at target resolution
            offset: Number of intervals to drop. Determined from the current
                time if not given. get_forecast() passes the same value it
                used to size its request, so both stay consistent even across
                an hour boundary.

        Returns:
            Current-interval aligned forecast
        """
        # Find which interval we're in within the current hour
        current_interval_in_hour = offset
        if current_interval_in_hour is None:
            current_interval_in_hour = self._current_interval_in_hour()

        logger.debug('%s: Shifting by %d intervals',
                     self.__class__.__name__,
                     current_interval_in_hour)

        # Shift indices: drop past intervals, renumber from 0
        shifted_forecast = {}
        for idx, value in forecast.items():
            if idx >= current_interval_in_hour:
                new_idx = idx - current_interval_in_hour
                shifted_forecast[new_idx] = value

        return shifted_forecast
