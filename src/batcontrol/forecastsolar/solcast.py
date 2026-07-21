""" Module to get solar forecasts from the Solcast rooftop API

https://docs.solcast.com.au/
GET https://api.solcast.com.au/rooftop_sites/{resource_id}/forecasts

The rooftop site (location, tilt, azimuth, capacity) is configured in the
Solcast web toolkit, not in batcontrol. Each installation entry only needs
the site's resource_id and the account's apikey.

The free hobbyist plan allows 10 API requests per day. Each configured
installation costs one request per refresh, so the minimum time between
refreshes is scaled with the number of installations (see __init__).
"""

import datetime
import logging
import time
import requests
from .baseclass import ForecastSolarBaseclass, ProviderError, RateLimitException

logger = logging.getLogger(__name__)
logger.info('Loading module')

API_BASE_URL = 'https://api.solcast.com.au/rooftop_sites'
FORECAST_HOURS = 72
PERCENTILE_FIELD_MAP = {10: 'pv_estimate10', 50: 'pv_estimate', 90: 'pv_estimate90'}


class Solcast(ForecastSolarBaseclass):
    """ Provider to get data from the Solcast rooftop API

    Returns 30-minute data at native resolution.
    Baseclass handles conversion to 15-min (linear power interpolation)
    or 60-min (bucket pair sum) as needed.
    """

    def __init__(self, pvinstallations, timezone, min_time_between_api_calls,
                 delay_evaluation_by_seconds, target_resolution=60) -> None:
        """ Initialize the Solcast class

        Args:
            pvinstallations: List of PV installation configurations.
                             Each entry needs 'name', 'resource_id' and 'apikey';
                             'percentile' (10, 50 or 90) is optional.
            timezone: Timezone for forecast data
            min_time_between_api_calls: Minimum seconds between API calls
            delay_evaluation_by_seconds: Delay for API evaluation
            target_resolution: Target resolution in minutes (15 or 60)
        """
        # Free hobbyist tier allows 10 API requests/day; each installation
        # costs one request per refresh. Budget 8 refresh cycles/day to keep
        # 2 requests in reserve; scale with the number of installations.
        self._provider_min = 24 * 60 * 60 * max(1, len(pvinstallations)) // 8
        super().__init__(pvinstallations, timezone,
                         max(min_time_between_api_calls, self._provider_min),
                         delay_evaluation_by_seconds,
                         target_resolution=target_resolution,
                         native_resolution=30)  # Solcast provides 30-minute data

        for unit in pvinstallations:
            name = unit.get('name')
            if not unit.get('resource_id'):
                raise ValueError(
                    f'[Solcast] No resource_id provided for installation {name}')
            if not unit.get('apikey'):
                raise ValueError(
                    f'[Solcast] No API key provided for installation {name}')
            percentile = unit.get('percentile', 50)
            if percentile not in PERCENTILE_FIELD_MAP:
                raise ValueError(
                    f'[Solcast] percentile must be one of 10, 50, 90 '
                    f'for installation {name}, got {percentile}')

    def get_forecast_from_raw_data(self) -> dict[int, float]:
        """ Get hour-aligned 30-minute forecast from cached raw data.

        Returns a dict mapping 30-minute interval index to energy in Wh.
        Index 0 = :00-:30 of the current hour, index 1 = :30-:00, etc.
        Baseclass converts to the target resolution.
        """
        results = self.get_all_raw_data()

        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        prediction = {}

        for name, result in results.items():
            unit = self._get_installation(name)
            field = PERCENTILE_FIELD_MAP[unit.get('percentile', 50)]

            if not result or 'forecasts' not in result:
                logger.warning(
                    'No forecasts in raw data for installation %s', name)
                continue

            for entry in result['forecasts']:
                try:
                    period_end = self._parse_period_end(entry['period_end'])
                    period_start = period_end - datetime.timedelta(minutes=30)

                    diff = period_start.astimezone(self.timezone) - current_hour_start
                    rel_interval = int(diff.total_seconds() // 1800)
                    if rel_interval < 0:
                        continue

                    value = entry.get(field, 0)
                    if value is None:
                        value = 0

                    # API delivers average power in kW for a 30-minute period:
                    # energy [Wh] = kW * 1000 [W] * 0.5 [h]
                    energy_wh = float(value) * 500.0
                    if rel_interval in prediction:
                        prediction[rel_interval] += energy_wh
                    else:
                        prediction[rel_interval] = energy_wh
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning(
                        'Error processing forecast entry %s: %s', entry, e)
                    continue

        # complete intervals without production with 0 values
        if prediction:
            max_interval = max(prediction.keys())
            for i in range(max_interval + 1):
                if i not in prediction:
                    prediction[i] = 0.0
        else:
            logger.warning('No results from Solcast API available')
            return {}

        output = dict(sorted(prediction.items()))
        logger.debug('Returning %d 30-minute intervals', len(output))
        return output

    def get_raw_data_from_provider(self, pvinstallation_name) -> dict:
        """ Get raw data from the Solcast rooftop API """
        unit = self._get_installation(pvinstallation_name)
        if unit is None:
            raise RuntimeError(
                f'[Solcast] PV Installation {pvinstallation_name} not found')

        name = unit['name']
        resource_id = unit['resource_id']
        apikey = unit['apikey']

        url = f'{API_BASE_URL}/{resource_id}/forecasts'
        url += f'?format=json&hours={FORECAST_HOURS}'

        logger.info(
            'Requesting Information for PV Installation %s', name)

        # API key goes into the Authorization header to keep it out of
        # logged URLs.
        headers = {'Authorization': f'Bearer {apikey}'}
        response = requests.get(url, headers=headers, timeout=60)

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as e:
                raise ProviderError(
                    f'[Solcast] Invalid JSON response: {e}') from e
        elif response.status_code in (401, 403):
            logger.error(
                'API returned %s - Unauthorized, apikey correct?',
                response.status_code)
            raise ProviderError(
                f'[Solcast] API returned {response.status_code} - '
                'Unauthorized, apikey correct?')
        elif response.status_code == 404:
            logger.error(
                'API returned 404 - resource_id %s not found', resource_id)
            raise ProviderError(
                f'[Solcast] API returned 404 - resource_id {resource_id} not found')
        elif response.status_code == 429:
            self.__store_retry(response)
            raise RateLimitException(
                '[Solcast] API rate limit exceeded')
        else:
            logger.warning(
                'Solcast API returned %s - %s',
                response.status_code, response.text)
            raise ProviderError(
                f'[Solcast] API returned {response.status_code}')

    def _get_installation(self, pvinstallation_name):
        """ Find installation config by name """
        for installation in self.pvinstallations:
            if installation['name'] == pvinstallation_name:
                return installation
        return None

    @staticmethod
    def _parse_period_end(timestamp_str: str) -> datetime.datetime:
        """ Parse Solcast period_end timestamps into UTC-aware datetimes.

        Solcast returns e.g. '2026-01-01T01:00:00.0000000Z'. The 7-digit
        fractional seconds and the trailing 'Z' are not supported by
        datetime.fromisoformat before Python 3.11, so normalize first.
        Periods always sit on :00/:30 boundaries, dropping the fractional
        part is lossless.
        """
        base = timestamp_str.rstrip('Z').split('.')[0]
        return datetime.datetime.fromisoformat(base).replace(
            tzinfo=datetime.timezone.utc)

    def __store_retry(self, response):
        """ Set the rate limit blackout window after a 429 response """
        retry_after = response.headers.get('Retry-After')
        blackout_ts = 0
        if retry_after is not None:
            try:
                blackout_ts = time.time() + int(retry_after)
            except ValueError:
                logger.debug('Unparseable Retry-After header: %s', retry_after)
        if blackout_ts <= 0:
            blackout_ts = time.time() + self._provider_min
        self.rate_limit_blackout_window_ts = blackout_ts
