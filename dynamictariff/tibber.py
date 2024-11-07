import datetime
import math
import requests
from .baseclass import DynamicTariffBaseclass

class Tibber(DynamicTariffBaseclass):
    def __init__(self, timezone , token, min_time_between_API_calls=0):
        super().__init__(timezone,min_time_between_API_calls)
        self.access_token=token
        self.url="https://api.tibber.com/v1-beta/gql"

    def get_raw_data_from_provider(self):
        if not self.access_token:
            raise RuntimeError
        headers={"Authorization":"Bearer " + self.access_token,
                "Content-Type":"application/json"}
        data="""{ "query":
        "{viewer {homes {currentSubscription {priceInfo { current {total startsAt } today {total startsAt } tomorrow {total startsAt }}}}}}" }
        """
        response=requests.post(self.url,data,headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f'[Tibber] Tibber Api responded with Error {response}')
        raw_data=response.json()
        return raw_data


    def get_prices_from_raw_data(self,homeid=0):
        rawdata=self.raw_data['data']
        now=datetime.datetime.now().astimezone(self.timezone)
        prices={}
        for day in ['today', 'tomorrow']:
            dayinfo=rawdata['viewer']['homes'][homeid]['currentSubscription']['priceInfo'][day]
            for item in dayinfo:
                timestamp=datetime.datetime.fromisoformat(item['startsAt'])
                diff=timestamp-now
                rel_hour=math.ceil(diff.total_seconds()/3600)
                if rel_hour >=0:
                    prices[rel_hour]=item['total']
        return prices