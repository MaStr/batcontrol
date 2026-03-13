"""
This module defines the EvccSolar class, which is used to interact with the evcc API to fetch
solar forecast data.

Classes:
    EvccSolar: A class to interact with the evcc API and process solar forecast data.

Methods:
    __init__(self, pvinstallations, timezone, api_delay):
        Initializes the EvccSolar instance with the given timezone and configuration.

    get_forecast(self):
        Fetches solar forecast data from the evcc API and returns it as a dictionary.

Configuration:
    To use evcc-solar as your solar forecast provider, configure your batcontrol_config.yaml 
    as follows:

    solar_forecast_provider: evcc-solar
    pvinstallations:
      - url: https://your-evcc-instance.local/api/tariff/solar

    The URL should point to your evcc instance's solar tariff API endpoint.

Data Processing:
    The module handles 15-minute interval data from evcc and converts it to hourly forecasts by:
    - Grouping multiple intervals that fall within the same forecast hour
    - Calculating the average power value for each hour
    - This ensures accurate hourly forecasts regardless of the interval frequency

Usage:
    To use this module, instantiate the EvccSolar class with appropriate configuration
    and call get_forecast() to retrieve solar production forecasts.
"""
import datetime
import logging
import requests

from .baseclass import ForecastSolarBaseclass

logger = logging.getLogger(__name__)


class EvccSolar(ForecastSolarBaseclass):
    """ Implement evcc API to get solar forecast data
        Inherits from ForecastSolarBaseclass

        Returns 15-minute data at native resolution.
        Baseclass handles conversion to hourly if needed.
    """

    def __init__(self, pvinstallations, timezone, min_time_between_api_calls, api_delay, target_resolution=60):
        """
        Initialize the EvccSolar instance.

        Args:
            pvinstallations (list): List of installation configurations. For evcc-solar,
                                  this should contain a single entry with 'url' key.
            timezone: Timezone information for the forecast data
            min_time_between_API_calls (int): Minimum time between API calls in seconds
            api_delay (int): Delay in seconds for API evaluation
            target_resolution (int): Target resolution in minutes (15 or 60)
        """
        super().__init__(pvinstallations, timezone, min_time_between_api_calls, api_delay,
                         target_resolution=target_resolution,
                         native_resolution=15)  # evcc provides 15-minute data

        # Extract URL from pvinstallations config
        if not pvinstallations or not isinstance(pvinstallations, list):
            raise ValueError("[EvccSolar] pvinstallations must be a non-empty list")

        if len(pvinstallations) != 1:
            raise ValueError(
                "[EvccSolar] evcc-solar provider expects exactly one installation configuration")

        installation = pvinstallations[0]
        if 'url' not in installation:
            raise ValueError("[EvccSolar] URL must be provided in installation configuration")

        self.url = installation['url']
        logger.info('Initialized EvccSolar with URL: %s', self.url)

    def get_forecast_from_raw_data(self) -> dict[int, float]:
        """
        Process the raw data from the evcc API and return a dictionary of forecast values.

        Returns 15-minute interval data indexed by relative interval from start of current hour.
        Baseclass will handle conversion to hourly if target_resolution is 60.
        """
        # We expect only one installation for evcc-solar
        raw_data = self.get_raw_data(self.pvinstallations[0]['name'])

        # Return empty prediction if no data available
        if not raw_data:
            logger.warning('No results from evcc Solar API available')
            return {}

        # Get rates from raw data (similar to evcc tariff implementation)
        data = raw_data.get('rates', None)
        if data is None:
            # Fallback for older evcc versions
            data = raw_data.get('result', {}).get('rates', [])

        now = datetime.datetime.now().astimezone(self.timezone)

        # Calculate start of current hour for relative indexing
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        # Build 15-minute interval forecast (hour-aligned)
        # Index 0 = first 15-min interval of current hour (e.g., 10:00-10:15)
        # Index 1 = second 15-min interval (e.g., 10:15-10:30)
        # Baseclass will shift indices to current interval later
        prediction = {}

        for item in data:
            try:
                timestamp = datetime.datetime.fromisoformat(item['start']).astimezone(self.timezone)

                # Calculate relative 15-minute interval from start of current hour
                diff = timestamp - current_hour_start
                rel_interval_15min = int(diff.total_seconds() / 900)  # 900 seconds = 15 minutes

                if rel_interval_15min >= 0:
                    value = item.get('value', 0)
                    if value is None:
                        value = 0

                    # Convert power (W) to energy (Wh) for 15 minutes
                    # value is in W, we need Wh for 15 min = W * 0.25h
                    energy_wh = float(value) * 0.25
                    prediction[rel_interval_15min] = round(energy_wh, 1)

            except (KeyError, ValueError, TypeError) as e:
                logger.warning('Error processing forecast item %s: %s', item, e)
                continue

        # Fill missing intervals with 0
        if prediction:
            max_interval = max(prediction.keys())
            for i in range(max_interval + 1):
                if i not in prediction:
                    prediction[i] = 0.0
        else:
            prediction[0] = 0.0

        # Return sorted by interval index
        output = dict(sorted(prediction.items()))
        logger.debug('Returning %d 15-minute intervals', len(output))
        return output

    def get_raw_data_from_provider(self, pvinstallation) -> dict:
        """
        Fetch raw forecast data from evcc API.
        """
        try:
            logger.info('Requesting solar forecast from evcc API: %s', self.url)
            response = requests.get(self.url, timeout=30)
            response.raise_for_status()

            if response.status_code != 200:
                raise ConnectionError(f'[EvccSolar] API returned {response.status_code}')

        except requests.exceptions.RequestException as e:
            raise ConnectionError(f'[EvccSolar] API request failed: {e}') from e

        try:
            raw_data = response.json()
            logger.debug('Successfully retrieved raw forecast data')
            return raw_data
        except ValueError as e:
            raise ConnectionError(f'[EvccSolar] Invalid JSON response: {e}') from e


def test():
    """
    Test function for the EvccSolar class.

    Usage:
        python evcc_solar.py <url>
    """
    import sys
    import json
    import pytz

    if len(sys.argv) != 2:
        print("Usage: python evcc_solar.py <url>")
        sys.exit(1)

    url = sys.argv[1]

    # Create test configuration
    pvinstallations = [{'url': url}]
    timezone = pytz.timezone('Europe/Berlin')

    try:
        evcc_solar = EvccSolar(pvinstallations, timezone,
                               min_time_between_api_calls=10, api_delay=0)
        forecast = evcc_solar.get_forecast()
        print(json.dumps(forecast, indent=4))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    test()
