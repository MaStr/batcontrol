"""Awattar Class

This module implements the Awattar API to retrieve dynamic electricity prices.
It inherits from the DynamicTariffBaseclass.

Classes:
    Awattar: A class to interact with the Awattar API and process electricity prices.

Methods:
    __init__(self,
                timezone, country: str,
                price_fees: float,
                price_markup: float,
                vat: float,
                min_time_between_API_calls=0):

        Initializes the Awattar class with the specified parameters.

    get_raw_data_from_provider(self):
        Fetches raw data from the Awattar API.

    _get_prices_native(self):
        Processes the raw data to extract and calculate electricity prices.
"""
import datetime
import logging
import math
import requests
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)


class Awattar(DynamicTariffBaseclass):
    """ Implement Awattar API to get dynamic electricity prices
        Inherits from DynamicTariffBaseclass

        Native resolution: 60 minutes (hourly)
        API only provides hourly data, baseclass handles 15-min replication if needed.
    """

    def __init__(self, timezone, country: str, min_time_between_API_calls=0,
                 delay_evaluation_by_seconds=0, target_resolution: int = 60):
        """ Initialize Awattar class with parameters """
        # Awattar only provides hourly data (native_resolution=60)
        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=60
        )
        country = country.lower()
        if country in ['at', 'de']:
            self.url = f'https://api.awattar.{country}/v1/marketdata'
        else:
            raise RuntimeError(f'[Awattar] Country Code {country} not known')

        self.vat = 0
        self.price_fees = 0
        self.price_markup = 0
        self.network_fees_fetcher = None

    def set_price_parameters(self, vat: float, price_fees: float, price_markup: float):
        """ Set the extra price parameters for the tariff calculation """
        self.vat = vat
        self.price_fees = price_fees
        self.price_markup = price_markup

    def set_network_fees_fetcher(self, fetcher):
        """Attach a NetworkFeesFetcher to add dynamic §14a network fees per interval."""
        self.network_fees_fetcher = fetcher

    def get_raw_data_from_provider(self):
        """ Get raw data from Awattar API and return parsed json """
        logger.debug('Requesting price forecast from Awattar API')
        try:
            response = requests.get(self.url, timeout=30)
            response.raise_for_status()
            if response.status_code != 200:
                raise ConnectionError(f'[Awattar] API returned {response}')
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f'[Awattar] API request failed: {e}') from e

        raw_data = response.json()
        return raw_data

    def _get_prices_native(self) -> dict[int, float]:
        """Get hour-aligned prices at native (60-minute) resolution.

        Returns:
            Dict mapping hour index to price value
            Index 0 = start of current hour
        """
        raw_data = self.get_raw_data()
        data = raw_data['data']
        now = datetime.datetime.now().astimezone(self.timezone)
        # Align to start of current hour
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        prices = {}

        for item in data:
            timestamp = datetime.datetime.fromtimestamp(
                item['start_timestamp'] / 1000
            ).astimezone(self.timezone)
            diff = timestamp - current_hour_start
            rel_hour = int(diff.total_seconds() / 3600)
            if rel_hour >= 0:
                network_fee = 0.0
                if self.network_fees_fetcher is not None:
                    network_fee = self.network_fees_fetcher.get_fee_at(timestamp)
                end_price = (
                    item['marketprice'] / 1000 * (1 + self.price_markup)
                    + self.price_fees + network_fee
                ) * (1 + self.vat)
                prices[rel_hour] = end_price

        logger.debug(
            'Awattar: Retrieved %d hourly prices (hour-aligned)',
            len(prices)
        )
        return prices
