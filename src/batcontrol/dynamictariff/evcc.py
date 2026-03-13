"""
This module defines the Evcc class, which is used to interact with the evcc API to fetch
dynamic tariff data.

Classes:
    Evcc: A class to interact with the evcc API and process dynamic tariff data.

Methods:
    __init__(self, timezone, url, min_time_between_API_calls=60):
        Initializes the Evcc instance with the given timezone, API URL,
        and minimum time between API calls.

    get_raw_data_from_provider(self):
        Fetches raw data from the evcc API and returns it as a JSON object.

    _get_prices_native(self):
        Processes the raw data from the evcc API and returns a dictionary of prices
        indexed by the relative interval.

    test():
        A test function to run the Evcc class with a provided URL and print the fetched prices.


"""
import datetime
import logging
import requests
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)


class Evcc(DynamicTariffBaseclass):
    """ Implement evcc API to get dynamic electricity prices
        Inherits from DynamicTariffBaseclass

        Native resolution: 15 minutes
        evcc provides 15-minute price data natively.
        Baseclass handles averaging to hourly if target_resolution=60.
    """

    def __init__(self, timezone, url, min_time_between_API_calls=60,
                 target_resolution: int = 60):
        # evcc provides native 15-minute data
        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds=0,
            target_resolution=target_resolution,
            native_resolution=15
        )
        self.url = url

    def get_raw_data_from_provider(self) -> dict:
        logger.debug('Requesting price forecast from evcc API: %s', self.url)
        try:
            response = requests.get(self.url, timeout=30)
            response.raise_for_status()
            if response.status_code != 200:
                raise ConnectionError(f'[evcc] API returned {response}')
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f'[evcc] API request failed: {e}') from e

        # {"result":
        #     { "rates": [
        #            {
        #                "start":"2024-06-20T08:00:00+02:00",
        #                "end":"2024-06-20T09:00:00+02:00",
        #                "price":0.35188299999999995
        #             },
        #            {
        #               "start":"2024-06-20T09:00:00+02:00",
        #                "end":"2024-06-20T10:00:00+02:00",
        #                "price":0.3253459999999999"
        #            }
        #        ]
        #     }
        # }

        raw_data = response.json()
        return raw_data

    def _get_prices_native(self) -> dict[int, float]:
        """Get hour-aligned prices at native (15-minute) resolution.

        Returns:
            Dict mapping 15-min interval index to price value
            Index 0 = start of current hour (first 15-min interval)
            Indices 0-3 represent the 4 quarters of the current hour
        """
        data = self.get_raw_data().get('rates', None)
        if data is None:
            # prior to evcc 0.207.0 the rates were in the 'result' field
            data = self.get_raw_data().get('result', {}).get('rates', None)

        now = datetime.datetime.now().astimezone(self.timezone)
        # Align to start of current hour
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        prices = {}

        for item in data:
            # "start":"2024-06-20T08:00:00+02:00" to timestamp
            timestamp = datetime.datetime.fromisoformat(
                item['start']).astimezone(self.timezone)

            # Calculate relative 15-min interval from start of current hour
            diff = timestamp - current_hour_start
            rel_interval = int(diff.total_seconds() / 900)  # 900 seconds = 15 minutes

            if rel_interval >= 0:
                # since evcc 0.203.0 value is the name of the price field.
                if item.get('value', None) is not None:
                    price = item['value']
                else:
                    price = item['price']

                prices[rel_interval] = price

        logger.debug(
            'evcc: Retrieved %d prices at 15-min resolution (hour-aligned)',
            len(prices)
        )
        return prices
