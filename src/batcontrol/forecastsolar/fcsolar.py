""" Module to get forecast from Forecast Solar API

See https://forecast.solar/ for more information

"""

import datetime
import math
import json
import logging
import requests
from .baseclass import ForecastSolarBaseclass, ProviderError, RateLimitException


logger = logging.getLogger(__name__)
logger.info('Loading module')


class FCSolar(ForecastSolarBaseclass):
    """ Provider to get data from https://forecast.solar/ 

    Returns hourly data at native resolution (60 minutes).
    Baseclass handles conversion to 15-min if needed.
    """

    def __init__(self, pvinstallations, timezone,
                 min_time_between_api_calls, api_delay=0, target_resolution=60):
        """ Initialize the FCSolar class 

        Args:
            pvinstallations: List of PV installation configurations
            timezone: Timezone for forecast data
            min_time_between_api_calls: Minimum seconds between API calls
            api_delay: Delay for API evaluation
            target_resolution: Target resolution in minutes (15 or 60)
        """
        super().__init__(pvinstallations, timezone,
                         min_time_between_api_calls, api_delay,
                         target_resolution=target_resolution,
                         native_resolution=60)  # FCSolar provides hourly data

    def get_forecast_from_raw_data(self) -> dict:
        """ Get hourly forecast from previously fetched raw data """
        results = self.get_all_raw_data()

        prediction = {}
        for hour in range(48+1):
            prediction[hour] = 0

        prediction = {}
        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour = datetime.datetime(
            now.year, now.month, now.day, now.hour).astimezone(self.timezone)
        result = next(iter(results.values()))
        response_time_string = result['message']['info']['time']
        response_time = datetime.datetime.fromisoformat(response_time_string)
        response_timezone = response_time.tzinfo
        for _, result in results.items():
            for isotime, value in result['result'].items():
                timestamp = datetime.datetime.fromisoformat(
                    isotime).astimezone(response_timezone)
                diff = timestamp-current_hour
                rel_hour = math.ceil(diff.total_seconds()/3600)-1
                if rel_hour >= 0:
                    if rel_hour in prediction.keys():
                        prediction[rel_hour] += value
                    else:
                        prediction[rel_hour] = value

        # complete hours without production with 0 values
        max_hour = max(prediction.keys())
        for h in range(max_hour+1):
            if h not in prediction.keys():
                prediction[h] = 0

        # complete hours without production with 0 values        #sort output
        output = dict(sorted(prediction.items()))

        return output

    def get_raw_data_from_provider(self, pvinstallation_name) -> dict:
        """ Get raw data from Forecast Solar API """

        unit = None
        for installation in self.pvinstallations:
            if installation['name'] == pvinstallation_name:
                unit = installation
                break

        if unit is None:
            raise RuntimeError(f'[FCSolar] PV Installation {pvinstallation_name} not found')

        name = unit['name']
        lat = unit['lat']
        lon = unit['lon']
        dec = unit['declination']  # declination
        az = unit['azimuth']  # 90 =W -90 = E
        kwp = unit['kWp']

        apikey_urlmod = ''
        if 'apikey' in unit.keys() and unit['apikey'] is not None:
            apikey_urlmod = unit['apikey'] + "/"  # ForecastSolar api
        # legacy naming in config file
        elif 'api' in unit.keys() and unit['api'] is not None:
            apikey_urlmod = unit['api'] + "/"  # ForecastSolar api

        horizon_querymod = ''
        if 'horizon' in unit.keys() and unit['horizon'] is not None:
            horizon_querymod = "?horizon=" + unit['horizon']  # ForecastSolar api

        url = (f"https://api.forecast.solar/{apikey_urlmod}estimate/"
               f"watthours/period/{lat}/{lon}/{dec}/{az}/{kwp}{horizon_querymod}")
        logger.info(
            'Requesting Information for PV Installation %s', name)

        try:
            response = requests.get(url, timeout=60)
        except requests.exceptions.RequestException:
            raise ProviderError(
                'Forecast solar API request failed'
            ) from None
        if response.status_code == 200:
            return json.loads(response.text)
        elif response.status_code == 429:
            retry_after = response.headers.get('X-Ratelimit-Retry-At')
            if retry_after:
                retry_after_timestamp = datetime.datetime.fromisoformat(retry_after)
                now = datetime.datetime.now().astimezone(self.timezone)
                retry_seconds = (retry_after_timestamp - now).total_seconds()
                self.rate_limit_blackout_window_ts = retry_after_timestamp.timestamp()
                logger.warning(
                    'Forecast solar API rate limit exceeded [%s]. '
                    'Retry after %d seconds at %s',
                    response.text,
                    retry_seconds,
                    retry_after_timestamp
                )
            else:
                logger.warning(
                    'Forecast solar API rate limit exceeded [%s]. '
                    'No retry after information available, dumping headers',
                    response.text
                )
                for header, value in response.headers.items():
                    logger.debug('Header: %s = %s', header, value)
            raise RateLimitException(
                'Forecast solar API rate limit exceeded')

        else:
            logger.warning(
                'Forecast solar API returned %s - %s',
                response.status_code, response.text)
            raise ProviderError(
                f'Forecast solar API returned {response.status_code} - {response.text}')


if __name__ == '__main__':
    test_pvinstallations = [{'name': 'Nordhalle',
                             'lat': '49.632461',
                             'lon': '8.617459',
                             'declination': '15',
                             'azimuth': '-1',
                             'kWp': '75.695'},
                            {'name': 'Suedhalle',
                             'lat': '49.6319',
                             'lon': '8.6175',
                             'declination': '20',
                             'azimuth': '7',
                             'kWp': '25.030'}]
    fcs = FCSolar(test_pvinstallations, 'Europe/Berlin', 10)
    fcs.refresh_data()
    print(fcs.get_forecast())
