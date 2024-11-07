import datetime
import time
import math
import json
import logging
import requests

logger = logging.getLogger('__main__')
logger.info(f'[FCSolar] loading module ')

class ForecastSolar(object):
    def __init__(self, pvinstallations, timezone) -> None:
        self.pvinstallations = pvinstallations
        self.results = {}
        self.last_update = 0
        self.seconds_between_updates = 900
        self.timezone=timezone

    def get_forecast(self):
        t0 = time.time()
        dt = t0-self.last_update
        if dt > self.seconds_between_updates:
            self.get_raw_forecast()
            self.last_update = t0
        prediction = {}
        for hour in range(48+1):
            prediction[hour] = 0

        # return empty prediction if results have not been obtained
        if self.results == {}:
            logger.warning(f'[FCSolar] No results from FC Solar API available')
            raise RuntimeWarning('[FCSolar] No results from FC Solar API available')


        prediction={}
        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour = datetime.datetime(
            now.year, now.month, now.day, now.hour).astimezone(self.timezone)
        result = next(iter(self.results.values()))
        response_time_string = result['message']['info']['time']
        response_time = datetime.datetime.fromisoformat(response_time_string)
        response_timezone = response_time.tzinfo
        for name, result in self.results.items():
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
        #complete hours without production with 0 values
        max_hour=max(prediction.keys())
        for h in range(max_hour+1):
            if h not in prediction.keys():
                prediction[h]=0
        #sort output
        output=dict(sorted(prediction.items()))

        return output

    def get_raw_forecast(self):
        unit: dict
        for unit in self.pvinstallations:
            name = unit['name']
            lat = unit['lat']
            lon = unit['lon']
            dec = unit['declination']  # declination
            az = unit['azimuth']  # 90 =W -90 = E
            kwp = unit['kWp']

            apikey_urlmod=''
            if 'apikey' in unit.keys() and unit['apikey'] is not None:
                apikey_urlmod = unit['apikey'] +"/"# ForecastSolar api
            #legacy naming in config file
            elif 'api' in unit.keys() and unit['api'] is not None:
                apikey_urlmod = unit['api'] +"/" # ForecastSolar api

            url = f"https://api.forecast.solar/{apikey_urlmod}estimate/watthours/period/{lat}/{lon}/{dec}/{az}/{kwp}"
            logger.info(
                f'[FCSolar] Requesting Information for PV Installation {name}')


            response = requests.get(url)
            if response.status_code == 200:
                self.results[name] = json.loads(response.text)
            else:
                logger.warning(
                    f'[ForecastSolar] forecast solar API returned {response.status_code} - {response.text}')


if __name__ == '__main__':
    pvinstallations = [{'name': 'Nordhalle',
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
    fcs=ForecastSolar(pvinstallations)
    print (fcs.get_forecast())