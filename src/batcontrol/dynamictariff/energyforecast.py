"""Energyforecast.de Class

This module implements the energyforecast.de API v2 to retrieve dynamic electricity prices.
It inherits from the DynamicTariffBaseclass.

Classes:
    Energyforecast: A class to interact with the energyforecast.de API v2
                    and process electricity prices.

Methods:
    __init__(self,
                timezone,
                token,
                min_time_between_API_calls=0,
                delay_evaluation_by_seconds=0,
                target_resolution=60,
                market_zone='DE'):

        Initializes the Energyforecast class with the specified parameters.

    get_raw_data_from_provider(self):
        Fetches raw data from the energyforecast.de API v2.

    _get_prices_native(self):
        Processes the raw data to extract and calculate electricity prices.
"""
import datetime
import logging
import requests
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)

# API v2 always returns quarter-hourly data; use 4-hour refresh floor so that
# 6 daily calls cover the full day with headroom for the forced 12:30 UTC fetch.
_PROVIDER_MIN_INTERVAL = 4 * 60 * 60  # 14400 s

# Convenience aliases: DE and LU are both served by the DE-LU market zone.
_MARKET_ZONE_ALIASES = {'DE': 'DE-LU', 'LU': 'DE-LU'}


class Energyforecast(DynamicTariffBaseclass):
    """ Implement energyforecast.de API v2 to get dynamic electricity prices
        Inherits from DynamicTariffBaseclass

        API v2 delivers complete calendar days (plan-dependent horizon).
        Data is always quarter-hourly; no resolution parameter is needed.

        Supported market zones: DE (default, normalized to DE-LU), LU (normalized to DE-LU),
        AT, FR, NL, BE, PL, DK1, DK2

        use_total_price=True: use the API-calculated total_ct_kwh field (which already
        includes dynamic network fees and VAT) instead of price_ct_kwh. In this mode
        no local fees/markup/vat calculation is applied and the API is called without
        vat/fixed_cost_cent overrides so the API computes the full price.
    """

    def __init__(self, timezone, token, min_time_between_API_calls=0,
                 delay_evaluation_by_seconds=0, target_resolution: int = 60,
                 market_zone: str = 'DE', use_total_price: bool = False):
        """ Initialize Energyforecast class with parameters """
        # Enforce provider-specific minimum refresh interval.
        effective_interval = max(min_time_between_API_calls, _PROVIDER_MIN_INTERVAL)

        # API v2 always delivers quarter-hourly data.
        super().__init__(
            timezone,
            effective_interval,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=15
        )
        self.url = 'https://www.energyforecast.de/api/v2/forecast'
        self.token = token
        normalized = market_zone.strip().upper()
        self.market_zone = _MARKET_ZONE_ALIASES.get(normalized, normalized)
        self.use_total_price = use_total_price
        self.vat = 0
        self.price_fees = 0
        self.price_markup = 0
        self.network_fees_fetcher = None

        logger.info(
            'Energyforecast: Configured for market_zone=%s, refresh every %d s, '
            'price_mode=%s',
            self.market_zone,
            effective_interval,
            'total_ct_kwh' if use_total_price else 'price_ct_kwh+local'
        )

    def set_price_parameters(
            self, vat: float, price_fees: float, price_markup: float):
        """ Set the extra price parameters for the tariff calculation """
        self.vat = vat
        self.price_fees = price_fees
        self.price_markup = price_markup

    def set_network_fees_fetcher(self, fetcher):
        """Attach a NetworkFeesFetcher to add dynamic para. 14a network fees per interval."""
        self.network_fees_fetcher = fetcher

    def get_raw_data_from_provider(self):
        """ Get raw data from energyforecast.de API v2 and return parsed json """
        logger.debug('Requesting price forecast from energyforecast.de API v2 (zone=%s)',
                     self.market_zone)
        if not self.token:
            raise RuntimeError('[Energyforecast] API token is required')
        try:
            params = {'token': self.token, 'market_zone': self.market_zone}
            if not self.use_total_price:
                # Suppress API-side calculation so we can apply fees/markup/vat locally,
                # consistent with the other locally-calculating providers (awattar).
                params['vat'] = 0
                params['fixed_cost_cent'] = 0
            response = requests.get(self.url, params=params, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f'[Energyforecast] API request failed: {e}') from e

        return response.json()

    def _get_prices_native(self) -> dict[int, float]:
        """Get hour-aligned prices at native (15-min) resolution.

        Expected API v2 response format:
           {
             "generated_at": "...",
             "valid_until": "...",
             "data": [
               {
                 "start": "2025-11-11T06:00:00+01:00",
                 "end": "2025-11-11T06:15:00+01:00",
                 "price_ct_kwh": 12.3456,
                 "total_ct_kwh": 27.8901,
                 "price_origin": "market"
               }
             ]
           }

        Prices from the API are in ct/kWh; we convert to EUR/kWh (/100) to
        stay consistent with the existing fees/markup/vat config values.

        Returns:
            Dict mapping interval index to price value (EUR/kWh)
            Index 0 = start of current hour
        """
        raw_data = self.get_raw_data()
        data = raw_data.get('data', [])
        now = datetime.datetime.now(self.timezone)
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        prices = {}

        interval_seconds = self.native_resolution * 60  # 900 s

        for item in data:
            # Python <3.11 does not support 'Z' in fromisoformat().
            timestamp = datetime.datetime.fromisoformat(
                item['start'].replace('Z', '+00:00')
            ).astimezone(self.timezone)

            diff = timestamp - current_hour_start
            rel_interval = int(diff.total_seconds() / interval_seconds)

            if rel_interval >= 0:
                if self.use_total_price:
                    # total_ct_kwh already includes dynamic network fees, markup,
                    # and VAT as calculated by the API. No local calculation needed.
                    end_price = item['total_ct_kwh'] / 100
                else:
                    # price_ct_kwh is in ct/kWh; convert to EUR/kWh and apply
                    # fees/markup/vat locally, consistent with awattar.
                    base_price = item['price_ct_kwh'] / 100
                    network_fee = 0.0
                    if self.network_fees_fetcher is not None:
                        network_fee = self.network_fees_fetcher.get_fee_at(timestamp)
                    end_price = (
                        (base_price * (1 + self.price_markup) +
                         self.price_fees + network_fee)
                        * (1 + self.vat)
                    )
                prices[rel_interval] = end_price

        logger.debug(
            'Energyforecast: Retrieved %d prices at %d-min resolution (hour-aligned)',
            len(prices),
            self.native_resolution
        )
        return prices
