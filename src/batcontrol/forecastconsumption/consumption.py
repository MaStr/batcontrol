""" Factory for creating consumption forecasters. """
import logging
from ..value_utils import get_config_value
from .forecastconsumption_interface import ForecastConsumptionInterface
from .forecast_csv import ForecastConsumptionCsv
from .forecast_evcc_home import ForecastConsumptionEvccHome
from .forecast_homeassistant import ForecastConsumptionHomeAssistant

logger = logging.getLogger(__name__)
logger.info('Loading module')

DEFAULT_CSV_FILE = 'default_load_profile.csv'

# pylint: disable=too-few-public-methods


class Consumption:
    """ Factory for consumption forecast providers """

    @staticmethod
    def create_consumption(
            tz,
            config: dict,
            target_resolution: int = 60) -> ForecastConsumptionInterface:
        """ Select and configure a consumption forecast provider based on
            the given configuration segment consumption_forecast in the config file.

        Args:
            tz: Timezone for forecast data
            config: Consumption forecast configuration (consumption_forecast)
            target_resolution: Target resolution in minutes (15 or 60)
        """
        consumption = None

        consumption_type = config.get('type', 'csv').lower()

        # csv is the default.
        if consumption_type == 'csv':
            consumption = _create_csv_forecast(tz, config, target_resolution)
        elif consumption_type == 'homeassistant-api':
            consumption = _create_homeassistant_forecast(
                tz, config, target_resolution)
        elif consumption_type == 'evcc-home':
            consumption = _create_evcc_home_forecast(
                tz, config, target_resolution)
        else:
            raise ValueError(
                f"Unknown consumption forecast type: '{consumption_type}'. "
                f"Supported types: 'csv', 'homeassistant-api', 'evcc-home'"
            )

        return consumption


def _read_history_settings(provider_config: dict):
    """ Read the history settings shared by all history based providers

    Returns:
        Tuple of history_days, history_weights, cache_ttl_hours, multiplier
    """
    history_days = provider_config.get('history_days', [-7, -14, -21])
    history_weights = provider_config.get('history_weights', [1, 1, 1])

    # Configure String -1;-2;-3 to a list and remove spaces
    if isinstance(history_days, str):
        history_days = [x.strip() for x in history_days.split(';')]
    if isinstance(history_weights, str):
        history_weights = [x.strip() for x in history_weights.split(';')]

    # Convert string lists to int/float lists (HomeAssistant config quirk)
    if isinstance(history_days, list):
        history_days = [int(x) for x in history_days]
    if isinstance(history_weights, list):
        history_weights = [int(x) for x in history_weights]

    cache_ttl_hours = provider_config.get('cache_ttl_hours', 48.0)
    multiplier = provider_config.get('multiplier', 1.0)
    return history_days, history_weights, cache_ttl_hours, multiplier


def _create_evcc_home_forecast(
        tz,
        config,
        target_resolution: int = 60) -> ForecastConsumptionInterface:
    """ Create and configure evcc (group home) consumption forecast provider """
    # Same as for HomeAssistant: block evcc_home or flat in consumption_forecast
    if 'evcc_home' in config:
        evcc_config = config['evcc_home']
    else:
        evcc_config = config

    url = get_config_value(evcc_config, 'url', None, 'consumption_forecast')
    if not url:
        raise ValueError(
            "evcc-home consumption forecast requires 'url' "
            "in consumption_forecast configuration"
        )

    history_days, history_weights, cache_ttl_hours, multiplier = (
        _read_history_settings(evcc_config))

    logger.info(
        "Creating evcc-home consumption forecast: "
        "url=%s, history_days=%s, weights=%s, "
        "multiplier=%0.2f, target_resolution=%d min",
        url,
        history_days,
        history_weights,
        multiplier,
        target_resolution)

    return ForecastConsumptionEvccHome(
        url=url,
        timezone=tz,
        history_days=history_days,
        history_weights=history_weights,
        cache_ttl_hours=cache_ttl_hours,
        multiplier=multiplier,
        target_resolution=target_resolution
    )


def _create_homeassistant_forecast(
        tz,
        config,
        target_resolution: int = 60) -> ForecastConsumptionInterface:
    """ Create and configure HomeAssistant consumption forecast provider """
    ha_config = {}
    # HomeAssistant schema validation can't handle 3rd level nesting
    if 'homeassistant_api' in config:
        ha_config = config['homeassistant_api']
    else:
        ha_config = config

    # Get configuration, accepting the deprecated 'base_url'/'apitoken' keys
    context = 'consumption_forecast'
    base_url = get_config_value(ha_config, 'url', None, context)
    api_token = get_config_value(ha_config, 'api_token', None, context)
    entity_id = get_config_value(ha_config, 'entity_id', None, context)

    # Validate required parameters. Emptiness, not presence: the add-on
    # delivers a field the user cleared as '' and an unset one as null,
    # both of which used to reach HomeAssistant as a broken auth message.
    for param, value in (
            ('url', base_url),
            ('api_token', api_token),
            ('entity_id', entity_id)):
        if not value:
            raise ValueError(
                f"HomeAssistant consumption forecast requires '{param}' "
                f"in consumption_forecast configuration"
            )

    history_days, history_weights, cache_ttl_hours, multiplier = (
        _read_history_settings(ha_config))
    sensor_unit = ha_config.get('sensor_unit', "auto")

    logger.info(
        "Creating HomeAssistant consumption forecast: "
        "entity_id=%s, history_days=%s, weights=%s, "
        "multiplier=%0.2f, sensor_unit=%s, target_resolution=%d min",
        entity_id,
        history_days,
        history_weights,
        multiplier,
        sensor_unit,
        target_resolution)

    consumption = ForecastConsumptionHomeAssistant(
        base_url=base_url,
        api_token=api_token,
        entity_id=entity_id,
        timezone=tz,
        history_days=history_days,
        history_weights=history_weights,
        cache_ttl_hours=cache_ttl_hours,
        multiplier=multiplier,
        sensor_unit=sensor_unit,
        target_resolution=target_resolution
    )
    return consumption


def _create_csv_forecast(
        tz,
        config,
        target_resolution: int = 60) -> ForecastConsumptionInterface:
    """ Create and configure CSV consumption forecast provider """
    csv_config = {}
    # Homeassistant schema validation can't handle 3rd level nesting
    if 'csv' in config:
        csv_config = config['csv']
    else:
        csv_config['annual_consumption'] = config.get('annual_consumption', 0)
        csv_config['load_profile'] = config.get('load_profile', None)

    if csv_config.get('load_profile', None) is None:
        logger.error(
            "No load profile specified, using default: %s",
            DEFAULT_CSV_FILE
        )
        csv_config['load_profile'] = DEFAULT_CSV_FILE

    consumption = ForecastConsumptionCsv(
        'config/' + csv_config['load_profile'],
        tz,
        csv_config.get('annual_consumption', 0),
        target_resolution=target_resolution
    )
    return consumption
