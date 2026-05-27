""" Module to get forecast from Forecast solarprognose API

https://www.solarprognose.de/web/de/solarprediction/page/api

"""

import datetime
import json
import logging
import requests
from .baseclass import ForecastSolarBaseclass, RateLimitException

logger = logging.getLogger(__name__)
logger.info('Loading module')

STATUS_OK = 0
STATUS_ERROR_INVALID_ACCESS_TOKEN = -2
STATUS_ERROR_MISSING_PARAMETER_ACCESS_TOKEN = -3
STATUS_ERROR_EMPTY_PARAMETER_ACCESS_TOKEN = -4
STATUS_ERROR_INVALID_TYPE = -5
STATUS_ERROR_MISSING_TYPE = -6
STATUS_ERROR_INVALID_ID = -7
STATUS_ERROR_ACCESS_DENIED = -8
STATUS_ERROR_INVALID_ITEM = -9
STATUS_ERROR_INVALID_TOKEN = -10
STATUS_ERROR_NO_SOLAR_DATA_AVAILABLE = -11
STATUS_ERROR_NO_DATA = -12
STATUS_ERROR_INTERNAL_ERROR = -13
STATUS_ERROR_UNKNOWN_ERROR = -14
STATUS_ERROR_INVALID_START_DAY = -15
STATUS_ERROR_INVALID_END_DAY = -16
STATUS_ERROR_INVALID_DAY = -17
STATUS_ERROR_INVALID_WEATHER_SERVICE_ID = -18
STATUS_ERROR_DAILY_QUOTA_EXCEEDED = -19
STATUS_ERROR_INVALID_OR_MISSING_ELEMENT_ITEM = -20
STATUS_ERROR_NO_PARAMETER = -21
STATUS_ERROR_INVALID_PERIOD = -22
STATUS_ERROR_INVALID_START_EPOCH_TIME = -23
STATUS_ERROR_INVALID_END_EPOCH_TIME = -24
STATUS_ERROR_ACCESS_DENIED_TO_ITEM_DUE_TO_LIMIT = -25
STATUS_ERROR_NO_CLEARSKY_VALUES = -26
STATUS_ERROR_MISSING_INPUT_ID_AND_TOKEN = -27
STATUS_ERROR_INVALID_ALGORITHM = -28
STATUS_ERROR_FAILED_TO_LOAD_WEATHER_LOCATION_ITEM = -29


class SolarPrognose(ForecastSolarBaseclass):
    """ Provider to get data from solarprognose API 

    Returns hourly data at native resolution (60 minutes).
    Baseclass handles conversion to 15-min if needed.
    """

    def __init__(self, pvinstallations, timezone, min_time_between_api_calls,
                 delay_evaluation_by_seconds, target_resolution=60) -> None:
        """ Initialize the SolarPrognose class 

        Args:
            pvinstallations: List of PV installation configurations
            timezone: Timezone for forecast data
            min_time_between_api_calls: Minimum seconds between API calls
            delay_evaluation_by_seconds: Delay for API evaluation
            target_resolution: Target resolution in minutes (15 or 60)
        """
        # Provider allows 20 calls/day; use 18 to leave 2 in reserve (~80 min).
        # Integer arithmetic avoids float rounding; max() respects larger caller values.
        _provider_min = 24 * 60 * 60 // 18  # 4800 s
        super().__init__(pvinstallations, timezone,
                         max(min_time_between_api_calls, _provider_min), delay_evaluation_by_seconds,
                         target_resolution=target_resolution,
                         native_resolution=60)  # SolarPrognose provides hourly data

    def get_forecast_from_raw_data(self) -> dict:
        """ Get hourly forecast from provider """
        prediction = {}
        for hour in range(48+1):
            prediction[hour] = 0

        # return empty prediction if results have not been obtained
        results = self.get_all_raw_data()

        prediction = {}

        now = datetime.datetime.now().astimezone(self.timezone)
        now_ts = now.timestamp()
        for _, result in results.items():
            for key in result['data']:
                timestamp = int(key)
                value = result['data'][key][0]
                if int(timestamp) < now_ts:
                    continue
                diff = timestamp - now_ts
                rel_hour = int(diff / 3600)
                if rel_hour >= 0:
                    # API delivers values in kW, we need W
                    if rel_hour in prediction:
                        prediction[rel_hour] += value * 1000
                    else:
                        prediction[rel_hour] = value * 1000

        max_hour = max(prediction.keys())

        # complete hours without production with 0 values
        for h in range(max_hour+1):
            if h not in prediction:
                prediction[h] = 0
        # sort output
        output = dict(sorted(prediction.items()))

        return output

    def get_raw_data_from_provider(self, pvinstallation_name) -> dict:
        """ Get raw data from Solar Prognose API """

        unit = None
        for installation in self.pvinstallations:
            if installation['name'] == pvinstallation_name:
                unit = installation
                break

        if unit is None:
            raise RuntimeError(f'[FCSolar] PV Installation {pvinstallation_name} not found')

        name = unit['name']
        apikey = unit.get('apikey', None)
        if apikey is None:
            logger.error(
                "No API key provided for installation %s", name)
            raise ValueError(
                f'No API key provided for installation {name}')

        algorithm = unit.get('algorithm', 'mosmix')
        # Optional
        item_querymod = ""
        if unit.get('item'):
            item = unit['item']  # inverter, plant, location
            # id is from the web interface
            # token is from the web interface
            item_id = unit.get('id', None)
            item_token = unit.get('token', None)
            item_querymod = f"&item={item}"
            if item_id is not None:
                item_querymod += f"&id={item_id}"
            elif item_token is not None:
                item_querymod += f"&token={item_token}"
            else:
                logger.error(
                    "No item id or token provided for installation %s",
                    name)
                raise ValueError(
                    'No item id or token provided for installation ',
                    f'{name}')

        url = "https://www.solarprognose.de/web/solarprediction/api/v1"
        url += f"?access-token={apikey}"
        if unit.get('project', None) is not None:
            url += f"&project={name}"
        url += f'&algorithm={algorithm}'
        url += f'{item_querymod}'
        url += '&type=hourly'
        # url += '&start_day=0&end_day=+2'
        url += '&_format=json'

        logger.info(
            'Requesting Information for PV Installation %s', name)

        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            response_data = json.loads(response.text)
            status_code = response_data['status']
            if status_code == STATUS_OK:
                # ok
                self.__get_and_store_retry(response)
                return json.loads(response.text)

            elif status_code == STATUS_ERROR_DAILY_QUOTA_EXCEEDED or \
                    status_code == STATUS_ERROR_ACCESS_DENIED_TO_ITEM_DUE_TO_LIMIT:
                logger.error(
                    'Limit exceeded for installation %s - %s',
                    name, status_code)
                self.__get_and_store_retry(response)
                raise RateLimitException(
                    f'Limit exceeded for installation {name} - {status_code}')
            else:
                logger.error(
                    'API returned status code %s',
                    status_code)
                raise RuntimeError(
                    f'API returned status code {status_code}')
        elif response.status_code == 401:
            logger.error(
                'API returned 401 - Unauthorized , apikey correct?')
            raise RuntimeError(
                'API returned 401 - Unauthorized')
        elif response.status_code == 429:
            self.__get_and_store_retry(response)
            raise RateLimitException(
                'SolarPrognose solar API rate limit exceeded')
        else:
            logger.warning(
                'SolarPrognose solar API returned %s - %s',
                response.status_code, response.text)

    def __get_and_store_retry(self, response):
        retry_after_timestamp = 0
        response_data = json.loads(response.text)
        if 'preferredNextApiRequestAt' in response_data:
            if 'epochTimeUtc' in response_data['preferredNextApiRequestAt']:
                retry_after_timestamp = response_data['preferredNextApiRequestAt']['epochTimeUtc']
        if retry_after_timestamp > 0:
            self.rate_limit_blackout_window_ts = retry_after_timestamp
        else:
            logger.warning(
                'SolarPrognose solar API rate limit exceeded [%s]. '
                'No retry after information available, dumping headers',
                response.text
            )
            for header, value in response.headers.items():
                logger.debug('Header: %s = %s', header, value)
