"""
DynamicTariff class to select and configure a dynamic tariff provider based
     on the given configuration.

Args:
    config (dict): Configuration dictionary containing the provider type and necessary parameters.
    timezone (str): Timezone information.
    min_time_between_API_calls (int): Minimum time interval between API calls.
    target_resolution (int): Target resolution in minutes (15 or 60).

Returns:
    selected_tariff: An instance of the selected tariff provider class (Awattar, Tibber, or Evcc).

Raises:
    RuntimeError: If required fields are missing in the configuration
                     or if the provider type is unknown.
"""
from .awattar import Awattar
from .tibber import Tibber
from .evcc import Evcc
from .energyforecast import Energyforecast
from .tariffzones import TariffZones
from .dynamictariff_interface import TariffInterface


class DynamicTariff:
    """ DynamicTariff factory"""
    @staticmethod
    def create_tarif_provider(config: dict, timezone,
                              min_time_between_api_calls,
                              delay_evaluation_by_seconds,
                              target_resolution: int = 60
                              ) -> TariffInterface:
        """ Select and configure a dynamic tariff provider based on the given configuration

        Args:
            config: Utility configuration (utility section from config file)
            timezone: Timezone for price data
            min_time_between_api_calls: Minimum seconds between API calls
            delay_evaluation_by_seconds: Random delay before API calls
            target_resolution: Target resolution in minutes (15 or 60)
        """
        selected_tariff = None
        provider = config.get('type')

        if provider.lower() == 'awattar_at':
            required_fields = ['vat', 'markup', 'fees']
            for field in required_fields:
                if field not in config.keys():
                    raise RuntimeError(
                        f'[DynTariff] Please include {field} in your configuration file'
                    )
            vat = float(config.get('vat', 0))
            markup = float(config.get('markup', 0))
            fees = float(config.get('fees', 0))
            selected_tariff = Awattar(
                timezone, 'at',
                min_time_between_api_calls,
                delay_evaluation_by_seconds,
                target_resolution=target_resolution
            )
            selected_tariff.set_price_parameters(vat, fees, markup)

        elif provider.lower() == 'awattar_de':
            required_fields = ['vat', 'markup', 'fees']
            for field in required_fields:
                if field not in config.keys():
                    raise RuntimeError(
                        f'[DynTariff] Please include {field} in your configuration file'
                    )
            vat = float(config.get('vat', 0))
            markup = float(config.get('markup', 0))
            fees = float(config.get('fees', 0))
            selected_tariff = Awattar(
                timezone, 'de',
                min_time_between_api_calls,
                delay_evaluation_by_seconds,
                target_resolution=target_resolution
            )
            selected_tariff.set_price_parameters(vat, fees, markup)

        elif provider.lower() == 'tibber':
            if 'apikey' not in config.keys():
                raise RuntimeError(
                    '[Dynamic Tariff] Tibber requires an API token. '
                    'Please provide "apikey :YOURKEY" in your configuration file'
                )
            token = config.get('apikey')
            selected_tariff = Tibber(
                timezone,
                token,
                min_time_between_api_calls,
                delay_evaluation_by_seconds,
                target_resolution=target_resolution
            )

        elif provider.lower() == 'evcc':
            if 'url' not in config.keys():
                raise RuntimeError(
                    '[Dynamic Tariff] evcc requires an URL. '
                    'Please provide "url" in your configuration file, '
                    'like http://evcc.local/api/tariff/grid'
                )
            selected_tariff = Evcc(
                timezone,
                config.get('url'),
                min_time_between_api_calls,
                target_resolution=target_resolution
            )

        elif provider.lower() == 'energyforecast' or provider.lower() == 'energyforecast_96':
            required_fields = ['vat', 'markup', 'fees', 'apikey']
            for field in required_fields:
                if field not in config.keys():
                    raise RuntimeError(
                        f'[DynTariff] Please include {field} in your configuration file'
                    )
            vat = float(config.get('vat', 0))
            markup = float(config.get('markup', 0))
            fees = float(config.get('fees', 0))
            token = config.get('apikey')
            selected_tariff = Energyforecast(
                timezone,
                token,
                min_time_between_api_calls,
                delay_evaluation_by_seconds,
                target_resolution=target_resolution
            )
            selected_tariff.set_price_parameters(vat, fees, markup)
            if provider.lower() == 'energyforecast_96':
                selected_tariff.upgrade_48h_to_96h()

        elif provider.lower() == 'tariff_zones':
            # Only tariff_zone_1 is strictly required. A single-zone
            # configuration acts as a static flat price for all 24 hours.
            if 'tariff_zone_1' not in config:
                raise RuntimeError(
                    '[DynTariff] Please include tariff_zone_1 in your configuration file'
                )
            # zone_2 and zone_3 are optional, but price and hours must be
            # provided together for each additional zone.
            for zone in (2, 3):
                price_key = f'tariff_zone_{zone}'
                hours_key = f'zone_{zone}_hours'
                if (price_key in config) != (hours_key in config):
                    raise RuntimeError(
                        f'[DynTariff] {hours_key} and {price_key} must both be '
                        'set or both omitted'
                    )
            # Once any additional zone is configured, zone_1_hours must be
            # provided explicitly (the all-24-hours default only applies in
            # single-zone static mode). Fail fast at factory time instead of
            # deferring to the first price fetch.
            extra_zone_configured = any(
                f'tariff_zone_{z}' in config for z in (2, 3)
            )
            if extra_zone_configured and 'zone_1_hours' not in config:
                raise RuntimeError(
                    '[DynTariff] zone_1_hours must be set when additional '
                    'zones (tariff_zone_2 or tariff_zone_3) are configured'
                )
            zone_1_hours = config.get('zone_1_hours')
            tariff_zone_2 = config.get('tariff_zone_2')
            zone_2_hours = config.get('zone_2_hours')
            tariff_zone_3 = config.get('tariff_zone_3')
            zone_3_hours = config.get('zone_3_hours')
            selected_tariff = TariffZones(
                timezone,
                min_time_between_api_calls,
                delay_evaluation_by_seconds,
                target_resolution=target_resolution,
                tariff_zone_1=float(config['tariff_zone_1']),
                zone_1_hours=zone_1_hours,
                tariff_zone_2=float(tariff_zone_2) if tariff_zone_2 is not None else None,
                zone_2_hours=zone_2_hours,
                tariff_zone_3=float(tariff_zone_3) if tariff_zone_3 is not None else None,
                zone_3_hours=zone_3_hours,
            )

        else:
            raise RuntimeError(f'[DynamicTariff] Unknown provider {provider}')
        return selected_tariff
