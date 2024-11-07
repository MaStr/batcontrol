import datetime
import math
import requests
from .baseclass import DynamicTariffBaseclass

class Evcc(DynamicTariffBaseclass):

    def __init__(self, timezone , url , min_time_between_API_calls=60):
        super().__init__(timezone,min_time_between_API_calls)
        self.url=url

    def get_raw_data_from_provider(self):
        response=requests.get(self.url)

        if response.status_code != 200:
           raise RuntimeError(f'[EVCC] API returned {response}')
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


        raw_data=response.json()
        return raw_data


    def get_prices_from_raw_data(self):
        data=self.raw_data['result']['rates']
        now=datetime.datetime.now().astimezone(self.timezone)
        prices={}


        for item in data:
            # "start":"2024-06-20T08:00:00+02:00" to timestamp
            timestamp=datetime.datetime.fromisoformat(item['start']).astimezone(self.timezone)
            diff=timestamp-now
            rel_hour=math.ceil(diff.total_seconds()/3600)
            if rel_hour >=0:
                prices[rel_hour]=item['price']
        return prices

def test():
    import sys
    import json
    import pytz
    if len(sys.argv) != 2:
        print("Usage: python evcc.py <url>")
        sys.exit(1)

    url = sys.argv[1]
    evcc = Evcc(pytz.timezone('Europe/Berlin'), url)  # Assuming the Evcc constructor takes a URL

    prices = evcc.get_prices()
    print(json.dumps(prices, indent=4))

if __name__ == "__main__":
    test()