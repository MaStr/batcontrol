""" Parent Class for implementing different solar forecast providers"""
from abc import ABCMeta
import datetime
import threading
import time
import random
import logging
from .forecastsolar_interface import ForecastSolarInterface
from ..fetcher.relaxed_caching import RelaxedCaching, CacheMissError
from ..scheduler import schedule_once
from ..interval_utils import upsample_forecast, downsample_to_hourly

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Exception raised when there's an error with the forecast provider."""
    pass


class RateLimitException(ProviderError):
    """Exception raised when the provider's rate limit is exceeded."""
    pass


class ForecastSolarBaseclass(ForecastSolarInterface):
    """ Parent Class for implementing different solar forecast providers
        # min_time_between_API_calls: Minimum time between API calls in seconds

        Supports Full-Hour Alignment strategy:
        - Providers return hour-aligned data (index 0 = start of current hour)
        - Baseclass handles resolution conversion (hourly <-> 15-min)
        - Baseclass shifts indices to current-interval alignment
        - Core receives data where [0] = current interval
    """

    def __init__(self, pvinstallations, timezone, min_time_between_API_calls,
                 delay_evaluation_by_seconds, target_resolution=60, native_resolution=60) -> None:
        self.pvinstallations = pvinstallations
        self.next_update_ts = 0
        self.min_time_between_updates = min_time_between_API_calls
        self.timezone = timezone
        self.delay_evaluation_by_seconds = delay_evaluation_by_seconds
        self.cache_list = {}
        self.rate_limit_blackout_window_ts = 0
        self._refresh_data_lock = threading.Lock()

        # Resolution configuration
        self.target_resolution = target_resolution  # What core.py expects (15 or 60)
        self.native_resolution = native_resolution  # What provider returns (15 or 60)

        logger.info(
            '%s: native_resolution=%d min, target_resolution=%d min',
            self.__class__.__name__,
            self.native_resolution,
            self.target_resolution
        )

        try:
            for unit in pvinstallations:
                name = unit['name']
                self.cache_list[name] = RelaxedCaching()
        except KeyError as e:
            raise ValueError("Each PV installation must have a 'name' key") from e

    def get_raw_data(self, pvinstallation_name) -> dict:
        """ Get raw data from cache or provider """
        return self.cache_list[pvinstallation_name].get_last_entry()

    def get_all_raw_data(self) -> dict:
        """ Get raw data for all installations from cache """
        all_data = {}
        for unit in self.pvinstallations:
            name = unit['name']
            all_data[name] = self.get_raw_data(name)
        return all_data

    def store_raw_data(self, pvinstallation_name, data: dict) -> None:
        """ Store raw data in cache """
        self.cache_list[pvinstallation_name].store_new_entry(data)

    def schedule_next_refresh(self) -> None:
        """ Schedule the next data refresh just after next_update_ts """
        hhmm = time.strftime('%H:%M:%S', time.localtime(self.next_update_ts+10))
        schedule_once(hhmm, self.refresh_data, 'solar-forecast-refresh')

    def refresh_data(self) -> None:
        """ Refresh data from provider if needed """
        with self._refresh_data_lock:
            now = time.time()

            if now > self.next_update_ts:
                if self.rate_limit_blackout_window_ts > now:
                    logger.info(
                        'Rate limit blackout window in place until %s (another %d seconds)',
                        self.rate_limit_blackout_window_ts,
                        self.rate_limit_blackout_window_ts - now
                    )
                    self.next_update_ts = self.rate_limit_blackout_window_ts
                    self.schedule_next_refresh()
                    return

                # Not on initial call
                if self.next_update_ts > 0 and self.delay_evaluation_by_seconds > 0:
                    sleeptime = random.randrange(0, self.delay_evaluation_by_seconds, 1)
                    logger.debug(
                        'Waiting for %d seconds before requesting new data',
                        sleeptime)
                    time.sleep(sleeptime)
                try:
                    for unit in self.pvinstallations:
                        name = unit['name']
                        result = self.get_raw_data_from_provider(name)
                        try:
                            # Store raw data only if no CacheMissError occurred
                            self.store_raw_data(name, result)
                        except RateLimitException:
                            logger.warning(
                                'Rate limit exceeded. Setting blackout window until %s',
                                self.rate_limit_blackout_window_ts)
                    self.next_update_ts = now + self.min_time_between_updates
                    self.schedule_next_refresh()
                except (ConnectionError, TimeoutError, ProviderError) as e:
                    logger.error('Error getting raw solar forecast data: %s', e)
                    logger.warning('Using cached raw solar forecast data')

    def get_forecast(self) -> dict[int, float]:
        """
        Get forecast with automatic resolution handling and current-interval alignment.

        Returns:
            Dict where [0] = current interval, [1] = next interval, etc.
            Ready for core.py to factorize [0] based on elapsed time.
        """
        if not self._refresh_data_lock.locked():
            self.refresh_data()

        # Get hour-aligned forecast from provider at native resolution
        native_forecast = self.get_forecast_from_raw_data()

        if not native_forecast:
            logger.warning('%s: No data returned from get_forecast_from_raw_data',
                           self.__class__.__name__)
            return {}

        # Convert resolution if needed
        converted_forecast = self._convert_resolution(native_forecast)

        # Shift indices to start from CURRENT interval
        current_aligned_forecast = self._shift_to_current_interval(converted_forecast)

        # Validate minimum forecast length
        if self.target_resolution == 60:
            min_intervals = 12  # 12 hours
        else:  # 15 minutes
            min_intervals = 48  # 12 hours * 4 = 48 intervals

        num_intervals = len(current_aligned_forecast)
        if num_intervals < min_intervals:
            logger.error('Less than 12 hours of forecast data. Got %d intervals, need %d.',
                         num_intervals, min_intervals)
            raise RuntimeError('Less than 12 hours of forecast data.')

        return current_aligned_forecast

    def _convert_resolution(self, forecast: dict[int, float]) -> dict[int, float]:
        """
        Convert forecast between resolutions if needed.

        Args:
            forecast: Hour-aligned forecast data at native resolution

        Returns:
            Forecast data at target resolution (still hour-aligned)
        """
        if self.native_resolution == self.target_resolution:
            return forecast

        if self.native_resolution == 60 and self.target_resolution == 15:
            logger.debug('%s: Upsampling 60min -> 15min using linear interpolation',
                         self.__class__.__name__)
            return upsample_forecast(forecast, target_resolution=15, method='linear')

        if self.native_resolution == 15 and self.target_resolution == 60:
            logger.debug('%s: Downsampling 15min -> 60min by summing quarters',
                         self.__class__.__name__)
            return downsample_to_hourly(forecast)

        logger.error('%s: Cannot convert %d min -> %d min',
                     self.__class__.__name__,
                     self.native_resolution,
                     self.target_resolution)
        return forecast

    def _shift_to_current_interval(self, forecast: dict[int, float]) -> dict[int, float]:
        """
        Shift hour-aligned indices to current-interval alignment.

        At time 10:20, if target resolution is 15 min:
        - Provider returns: [0]=10:00-10:15, [1]=10:15-10:30, [2]=10:30-10:45, ...
        - We're in interval 1 (10:15-10:30)
        - Output: [0]=10:15-10:30, [1]=10:30-10:45, ... (interval 0 dropped)

        Args:
            forecast: Hour-aligned forecast at target resolution

        Returns:
            Current-interval aligned forecast
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
        shifted_forecast = {}
        for idx, value in forecast.items():
            if idx >= current_interval_in_hour:
                new_idx = idx - current_interval_in_hour
                shifted_forecast[new_idx] = value

        return shifted_forecast

    def get_raw_data_from_provider(self, pvinstallation_name) -> dict:
        """ Prototype for get_raw_data_from_provider and store in cache """
        raise RuntimeError("[Forecast Solar Base Class] Function "
                           "'get_raw_data_from_provider' not implemented"
                           )

    def get_forecast_from_raw_data(self) -> dict[int, float]:
        """ Prototype for get_forecast_from_raw_data """
        raise RuntimeError("[Forecast Solar Base Class] Function "
                           "'get_forecast_from_raw_data' not implemented"
                           )
