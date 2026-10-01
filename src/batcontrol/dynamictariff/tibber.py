""" Implement Tibber API to get dynamic electricity prices

Tibber API supports both HOURLY and QUARTER_HOURLY resolution
via the priceInfo resolution parameter.
"""

import datetime
import json
import logging
import requests
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)


class Tibber(DynamicTariffBaseclass):
    """ Implement Tibber API to get dynamic electricity prices
        Inherits from DynamicTariffBaseclass

        Tibber API supports both resolutions:
        - HOURLY: Hourly prices (60-minute intervals)
        - QUARTER_HOURLY: 15-minute prices (in supported regions)

        The native resolution is set based on target_resolution to fetch
        data at the optimal granularity from the API.
    """

    def __init__(
            self,
            timezone,
            token,
            min_time_between_API_calls=0,
            delay_evaluation_by_seconds=0,
            target_resolution: int = 60):
        # Tibber API supports both resolutions, so we fetch at target resolution
        # to avoid unnecessary conversion
        if target_resolution == 15:
            native_resolution = 15
            self.api_resolution = "QUARTER_HOURLY"
        else:
            native_resolution = 60
            self.api_resolution = "HOURLY"

        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=native_resolution
        )
        self.access_token = token
        self.url = "https://api.tibber.com/v1-beta/gql"

        logger.info(
            'Tibber: Configured to fetch %s data (resolution=%d min)',
            self.api_resolution,
            self.native_resolution
        )

    def get_raw_data_from_provider(self) -> dict:
        """ Get raw data from Tibber API """
        logger.debug('Requesting price forecast from Tibber API (resolution=%s)',
                     self.api_resolution)
        if not self.access_token:
            raise RuntimeError('[Tibber] API token is required')

        headers = {
            "Authorization": "Bearer " + self.access_token,
            "Content-Type": "application/json"
        }
        # Use configured resolution in the GraphQL query
        graphql_query = f"""{{
            viewer {{
                homes {{
                    currentSubscription {{
                        priceInfo(resolution: {self.api_resolution}) {{
                            current {{
                                total
                                startsAt
                            }}
                            today {{
                                total
                                startsAt
                            }}
                            tomorrow {{
                                total
                                startsAt
                            }}
                        }}
                    }}
                }}
            }}
        }}"""

        data = json.dumps({"query": graphql_query})
        try:
            response = requests.post(
                self.url, data, headers=headers, timeout=30)
            response.raise_for_status()
            if response.status_code != 200:
                raise ConnectionError(
                    f'[Tibber] API responded with {response}')
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f'[Tibber] API request failed: {e}') from e

        raw_data = response.json()
        return raw_data

    def _get_prices_native(self) -> dict[int, float]:
        """Get hour-aligned prices at native resolution.

        Returns:
            Dict mapping interval index to price value
            Index 0 = start of current hour
            For 15-min resolution: indices 0-3 represent the current hour
        """
        homeid = 0
        raw_data = self.get_raw_data()
        rawdata = raw_data['data']
        now = datetime.datetime.now().astimezone(self.timezone)
        # Align to start of current hour
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        prices = {}

        for day in ['today', 'tomorrow']:
            dayinfo = rawdata['viewer']['homes'][homeid]['currentSubscription']['priceInfo'][day]
            if dayinfo is None:
                continue

            for item in dayinfo:
                timestamp = datetime.datetime.fromisoformat(item['startsAt'])
                diff = timestamp - current_hour_start

                if self.native_resolution == 15:
                    # For 15-min data, calculate interval index
                    # Each interval is 15 minutes = 900 seconds
                    rel_interval = int(diff.total_seconds() / 900)
                else:
                    # For hourly data
                    rel_interval = int(diff.total_seconds() / 3600)

                if rel_interval >= 0:
                    prices[rel_interval] = item['total']

        logger.debug(
            'Tibber: Retrieved %d prices at %d-min resolution (hour-aligned)',
            len(prices),
            self.native_resolution
        )
        return prices
