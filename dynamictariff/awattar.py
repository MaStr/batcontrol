import datetime
import math
import requests
from .baseclass import DynamicTariffBaseclass

class Awattar(DynamicTariffBaseclass):
    def __init__(self, timezone ,country:str, price_fees:float, price_markup:float, vat:float,  min_time_between_API_calls=0):
        super().__init__(timezone,min_time_between_API_calls)
        country= country.lower()
        if country in ['at','de']:
            self.url=f'https://api.awattar.{country}/v1/marketdata'
        else:
            raise RuntimeError(f'[Awattar] Country Code {country} not known')

        self.vat=vat
        self.price_fees=price_fees
        self.price_markup=price_markup

    def get_raw_data_from_provider(self):
        response=requests.get(self.url)

        if response.status_code != 200:
           raise RuntimeError(f'[Awattar_AT] API returned {response}')

        raw_data=response.json()
        return raw_data


    def get_prices_from_raw_data(self):
        data=self.raw_data['data']
        now=datetime.datetime.now().astimezone(self.timezone)
        prices={}
        for item in data:
            timestamp=datetime.datetime.fromtimestamp(item['start_timestamp']/1000).astimezone(self.timezone)
            diff=timestamp-now
            rel_hour=math.ceil(diff.total_seconds()/3600)
            if rel_hour >=0:
                end_price=(item['marketprice']/1000*(1+self.price_markup)+self.price_fees)*(1+self.vat)
                prices[rel_hour]=end_price
        return prices