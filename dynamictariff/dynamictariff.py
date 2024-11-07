
from .awattar import Awattar
from .tibber import Tibber
from .evcc import Evcc

class DynamicTariff(object):
    def __new__(cls,  config:dict, timezone,min_time_between_API_calls):
        selected_tariff=None
        provider=config['type']

        if provider.lower()=='awattar_at':
            required_fields=['vat', 'markup', 'fees']
            for field in required_fields:
                if not field in config.keys():
                    raise RuntimeError(f'[DynTariff] Please include {field} in your configuration file')
            vat = float(config['vat'])
            markup = float(config['markup'])
            fees = float(config['fees'])
            selected_tariff= Awattar(timezone,'at',fees,markup,vat,min_time_between_API_calls)

        elif provider.lower()=='awattar_de':
            required_fields=['vat', 'markup', 'fees']
            for field in required_fields:
                if not field in config.keys():
                    raise RuntimeError(f'[DynTariff] Please include {field} in your configuration file')
            vat = float(config['vat'])
            markup = float(config['markup'])
            fees = float(config['fees'])
            selected_tariff= Awattar(timezone,'de',fees,markup,vat,min_time_between_API_calls)

        elif provider.lower()=='tibber':
            if not 'apikey' in config.keys() :
                raise RuntimeError (f'[Dynamic Tariff] Tibber requires an API token. Please provide "apikey :YOURKEY" in your configuration file')
            token = config['apikey']
            selected_tariff=Tibber(timezone,token,min_time_between_API_calls)

        elif provider.lower()=='evcc':
            if not 'url' in config.keys() :
                raise RuntimeError (f'[Dynamic Tariff] EVCC requires an URL. Please provide "url" in your configuration file, like http://evcc.local/api/tariff/grid')
            selected_tariff= Evcc(timezone,config['url'],min_time_between_API_calls)
        else:
            raise RuntimeError(f'[DynamicTariff] Unkown provider {provider}')
        return selected_tariff
