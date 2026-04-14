"""Base class for dynamic tariff providers with resolution handling.

This module provides the base class that all dynamic tariff providers inherit from.
It implements automatic resolution conversion and current-interval alignment similar to
solar and consumption forecasts.

Key Design:
- Providers declare their native_resolution (15 or 60 minutes)
- Baseclass handles automatic upsampling/downsampling
- For prices: replication (hourly->15min) or averaging (15min->hourly)
- Baseclass shifts indices to current-interval alignment
"""

import datetime
import logging
import random
import threading
import time
from abc import abstractmethod
from .dynamictariff_interface import TariffInterface
from ..fetcher.relaxed_caching import RelaxedCaching
from ..scheduler import schedule_once
from ..interval_utils import average_to_hourly

logger = logging.getLogger(__name__)


class DynamicTariffBaseclass(TariffInterface):
    """Base class for dynamic tariff providers with resolution handling.

    Provides automatic resolution handling:
    - Providers declare their native_resolution (15 or 60 minutes)
    - Baseclass converts between resolutions automatically
    - For prices: uses replication (60->15) or averaging (15->60)
    - Baseclass shifts indices to current-interval alignment

    Subclasses must:
    1. Set self.native_resolution in __init__
    2. Implement _get_prices_native() to return hour-aligned price data
    3. Implement get_raw_data_from_provider() to fetch data from API
    """

    # pylint: disable=invalid-name
    def __init__(self, timezone, min_time_between_API_calls,
                 delay_evaluation_by_seconds,
                 target_resolution: int = 60,
                 native_resolution: int = 60) -> None:
        """Initialize tariff baseclass with resolution handling.

        Args:
            timezone: Timezone for timestamp handling
            min_time_between_API_calls: Minimum seconds between API calls
            delay_evaluation_by_seconds: Random delay before API calls
            target_resolution: Target resolution in minutes (what core.py expects: 15 or 60)
            native_resolution: Native resolution in minutes (what provider returns: 15 or 60)
        """
        self.timezone = timezone
        self.min_time_between_updates = min_time_between_API_calls
        self.delay_evaluation_by_seconds = delay_evaluation_by_seconds
        self.target_resolution = target_resolution
        self.native_resolution = native_resolution

        self.raw_data = {}
        self.next_update_ts = 0
        self.cache = RelaxedCaching()
        self._refresh_data_lock = threading.Lock()

        logger.info(
            '%s: native_resolution=%d min, target_resolution=%d min',
            self.__class__.__name__,
            self.native_resolution,
            self.target_resolution
        )

    def schedule_next_refresh(self) -> None:
        """Schedule the next data refresh just after next_update_ts."""
        hhmm = time.strftime('%H:%M:%S', time.localtime(self.next_update_ts + 10))
        schedule_once(hhmm, self.refresh_data, 'utility-tariff-refresh')

    def get_raw_data(self) -> dict:
        """Get raw data from cache."""
        return self.cache.get_last_entry()

    def store_raw_data(self, data: dict) -> None:
        """Store raw data in cache."""
        self.cache.store_new_entry(data)

    def refresh_data(self) -> None:
        """Refresh data from provider if needed."""
        with self._refresh_data_lock:
            now = time.time()
            if now > self.next_update_ts:
                # Not on initial call
                if self.next_update_ts > 0 and self.delay_evaluation_by_seconds > 0:
                    sleeptime = random.randrange(0, self.delay_evaluation_by_seconds, 1)
                    logger.debug(
                        'Waiting for %d seconds before requesting new data',
                        sleeptime)
                    time.sleep(sleeptime)
                try:
                    self.store_raw_data(self.get_raw_data_from_provider())
                    self.next_update_ts = now + self.min_time_between_updates
                    self.schedule_next_refresh()
                except (ConnectionError, TimeoutError) as e:
                    logger.error('Error getting raw tariff data: %s', e)
                    logger.warning('Using cached raw tariff data')

    def get_prices(self) -> dict[int, float]:
        """Get prices with automatic resolution handling.

        Returns:
            Dict where [0] = current interval, [1] = next interval, etc.
            Ready for core.py to factorize [0] based on elapsed time.
        """
        if not self._refresh_data_lock.locked():
            self.refresh_data()

        # Get hour-aligned prices at native resolution
        native_prices = self._get_prices_native()

        if not native_prices:
            logger.warning(
                '%s: No data returned from _get_prices_native',
                self.__class__.__name__)
            return {}

        # Convert resolution if needed
        converted_prices = self._convert_resolution(native_prices)

        # Shift indices to start from CURRENT interval
        current_aligned_prices = self._shift_to_current_interval(converted_prices)

        return current_aligned_prices

    @abstractmethod
    def _get_prices_native(self) -> dict[int, float]:
        """Get prices at native resolution, hour-aligned.

        Returns:
            Dict mapping interval index to price value
            Index 0 = start of current hour

        Note:
            This method should return hour-aligned data. The baseclass will:
            1. Convert resolution if needed
            2. Shift indices to current interval
        """

    @abstractmethod
    def get_raw_data_from_provider(self) -> dict:
        """Fetch raw data from provider API and return parsed response."""

    def _convert_resolution(self, prices: dict[int, float]) -> dict[int, float]:
        """Convert prices between resolutions if needed.

        For prices:
        - 60->15: Replicate (same price for all 4 quarters of an hour)
        - 15->60: Average (mean of 4 quarters)

        Args:
            prices: Hour-aligned price data at native resolution

        Returns:
            Price data at target resolution (still hour-aligned)
        """
        if self.native_resolution == self.target_resolution:
            return prices

        if self.native_resolution == 60 and self.target_resolution == 15:
            logger.debug(
                '%s: Replicating hourly prices -> 15min (same price per quarter)',
                self.__class__.__name__)
            return self._replicate_hourly_to_15min(prices)

        if self.native_resolution == 15 and self.target_resolution == 60:
            logger.debug('%s: Averaging 15min prices -> hourly',
                         self.__class__.__name__)
            return average_to_hourly(prices)

        logger.error('%s: Cannot convert %d min -> %d min',
                     self.__class__.__name__,
                     self.native_resolution,
                     self.target_resolution)
        return prices

    def _replicate_hourly_to_15min(self, hourly: dict[int, float]) -> dict[int, float]:
        """Replicate each hourly price to 4 quarters.

        For prices, we replicate rather than interpolate because:
        - Price is the same throughout the hour
        - Each 15-min interval has the same hourly price

        Args:
            hourly: Dict mapping hour index to price

        Returns:
            Dict mapping 15-min interval index to price
        """
        prices_15min = {}
        for hour, price in hourly.items():
            for quarter in range(4):
                prices_15min[hour * 4 + quarter] = price
        return prices_15min

    def _shift_to_current_interval(self, prices: dict[int, float]) -> dict[int, float]:
        """Shift hour-aligned indices to current-interval alignment.

        At time 10:20, if target resolution is 15 min:
        - Provider returns: [0]=10:00-10:15, [1]=10:15-10:30, ...
        - We're in interval 1 (10:15-10:30)
        - Output: [0]=10:15-10:30, [1]=10:30-10:45, ... (interval 0 dropped)

        Args:
            prices: Hour-aligned prices at target resolution

        Returns:
            Current-interval aligned prices
        """
        now = datetime.datetime.now(datetime.timezone.utc).astimezone(self.timezone)
        current_minute = now.minute

        # Find which interval we're in within the current hour
        current_interval_in_hour = current_minute // self.target_resolution

        logger.debug('%s: Current time %s, shifting by %d intervals',
                     self.__class__.__name__,
                     now.strftime('%H:%M:%S'),
                     current_interval_in_hour)

        # Shift indices: drop past intervals, renumber from 0
        shifted_prices = {}
        for idx, value in prices.items():
            if idx >= current_interval_in_hour:
                new_idx = idx - current_interval_in_hour
                shifted_prices[new_idx] = value

        return shifted_prices
