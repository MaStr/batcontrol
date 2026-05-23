"""Dynamic network fees fetcher (para. 14a EnWG).

Fetches pre-calculated NT/ST/HT time series from dyn-net.batcontrol.software
and provides per-timestamp fee lookup for energy price providers.

The API returns NET prices (excluding VAT) in EUR/kWh. These are added to raw
energy prices before applying VAT in providers that calculate fees locally
(e.g. Awattar, Energyforecast).
"""
import datetime
import logging
import requests
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)

DEFAULT_API_URL = 'https://dyn-net.batcontrol.software/api/'
_CACHE_SECONDS = 12 * 3600  # tariffs change at most quarterly


class NetworkFeesFetcher(DynamicTariffBaseclass):
    """Fetches para. 14a EnWG dynamic network fees as a time series.

    Data is cached for 12 hours. If no data is available (first start or API
    unreachable), get_fee_at() raises CacheMissError which skips the
    calculation cycle - the same behaviour as the other price providers.
    """

    def __init__(self, timezone, country: str, operator: str,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                 url: str = DEFAULT_API_URL,
                 delay_evaluation_by_seconds: int = 0):
        super().__init__(
            timezone,
            _CACHE_SECONDS,
            delay_evaluation_by_seconds,
            target_resolution=60,
            native_resolution=60
        )
        self.country = country.lower()
        self.operator = operator.lower()
        self.api_url = url.rstrip('/')
        logger.info(
            'NetworkFeesFetcher: country=%s operator=%s url=%s',
            self.country, self.operator, self.api_url
        )

    def get_raw_data_from_provider(self) -> list:
        """Fetch raw slot list from the network fees API."""
        endpoint = (
            f'{self.api_url}?country={self.country}'
            f'&operator={self.operator}&next_hours=168'
        )
        logger.debug('Requesting network fees from %s', endpoint)
        try:
            response = requests.get(endpoint, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise ConnectionError(
                f'[NetworkFees] API request failed: {e}') from e
        data = response.json()
        if not isinstance(data, list):
            raise ConnectionError(
                '[NetworkFees] Unexpected response format from API: '
                f'{type(data).__name__}'
            )
        logger.info('NetworkFees: Fetched %d slots from API', len(data))
        return data

    def _get_prices_native(self) -> dict:
        """Return hour-aligned fee dict (NET EUR/kWh). Index 0 = start of current hour."""
        raw_data = self.get_raw_data()  # raises CacheMissError if no data
        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        prices = {}
        for slot in raw_data:
            slot_start = datetime.datetime.fromisoformat(
                slot['start'].replace('Z', '+00:00')
            ).astimezone(self.timezone)
            slot_end = datetime.datetime.fromisoformat(
                slot['end'].replace('Z', '+00:00')
            ).astimezone(self.timezone)
            value = float(slot['value'])
            # Expand multi-hour slot (e.g. NT 00:00-06:00) into individual
            # hours
            t = slot_start.replace(minute=0, second=0, microsecond=0)
            while t < slot_end:
                rel_hour = int((t - current_hour_start).total_seconds() / 3600)
                if rel_hour >= 0:
                    prices[rel_hour] = value
                t += datetime.timedelta(hours=1)
        logger.debug('NetworkFees: %d hourly fee entries built', len(prices))
        return prices

    def get_fee_at(self, ts: datetime.datetime) -> float:
        """Return the NET network fee (EUR/kWh) valid at the given timestamp.

        Triggers a cache refresh if data is stale. Raises CacheMissError if no
        data is available yet (first start with unreachable API). Returns 0.0
        with a warning if the timestamp falls outside the available 168h window.
        """
        self.refresh_data()
        raw_data = self.get_raw_data()  # raises CacheMissError if still empty
        ts_local = ts.astimezone(self.timezone)
        for slot in raw_data:
            slot_start = datetime.datetime.fromisoformat(
                slot['start'].replace('Z', '+00:00')
            ).astimezone(self.timezone)
            slot_end = datetime.datetime.fromisoformat(
                slot['end'].replace('Z', '+00:00')
            ).astimezone(self.timezone)
            if slot_start <= ts_local < slot_end:
                return float(slot['value'])
        logger.warning(
            'NetworkFees: No fee slot found for %s, using 0.0', ts_local
        )
        return 0.0
