""" Factory for solar forecast providers """

from .forecastsolar_interface import ForecastSolarInterface
from .fcsolar import FCSolar
from .evcc_solar import EvccSolar
from .forecast_homeassistant_ml import ForecastSolarHomeAssistantML
from .solcast import Solcast


class ForecastSolar:
    """ Factory for solar forecast providers """
    @staticmethod
    def create_solar_provider(config: dict,
                              timezone,
                              min_time_between_api_calls,
                              api_delay=0,
                              requested_provider='fcsolarapi',
                              target_resolution: int = 60) -> ForecastSolarInterface:
        """ Select and configure a solar forecast provider based on the given configuration

        Args:
            config: PV installations configuration (pvinstallations)
            timezone: Timezone for forecast data
            min_time_between_api_calls: Minimum seconds between API calls
            api_delay: Delay for API evaluation
            requested_provider: Provider name ('fcsolarapi', 'evcc-solar',
                                'homeassistant-solar-forecast-ml', 'solcast')
            target_resolution: Target resolution in minutes (15 or 60)

        Raises:
            RuntimeError: If provider is unknown
            ValueError: If configuration is invalid for the provider
        """
        provider = None
        if requested_provider.lower() == 'fcsolarapi':
            provider = FCSolar(config, timezone, min_time_between_api_calls,
                               api_delay, target_resolution)
        elif requested_provider.lower() == 'evcc-solar':
            provider = EvccSolar(config, timezone, min_time_between_api_calls,
                                 api_delay, target_resolution)
        elif requested_provider.lower() == 'solcast':
            provider = Solcast(config, timezone, min_time_between_api_calls,
                               api_delay, target_resolution)
        elif requested_provider.lower() == 'homeassistant-solar-forecast-ml':
            # Parse HomeAssistant Solar Forecast ML configuration from pvinstallations
            # Each installation can have type='homeassistant-solar-forecast-ml' with connection details
            provider = ForecastSolarHomeAssistantML(
                pvinstallations=config,
                timezone=timezone,
                base_url=_get_config_value(config, 'base_url', 'ws://homeassistant.local:8123'),
                api_token=_get_config_value(config, 'api_token', None),
                entity_id=_get_config_value(config, 'entity_id', None),
                min_time_between_api_calls=min_time_between_api_calls,
                delay_evaluation_by_seconds=api_delay,
                sensor_unit=_get_config_value(config, 'sensor_unit', 'auto'),
                target_resolution=target_resolution
            )
        else:
            raise RuntimeError(f'[ForecastSolar] Unknown provider {requested_provider}')
        return provider


def _get_config_value(config: list, key: str, default=None):
    """Extract configuration value from pvinstallations list

    Args:
        config: List of pvinstallations dicts
        key: Configuration key to find
        default: Default value if key not found

    Returns:
        Value from first installation's config, or default if not found
    """
    if isinstance(config, list) and len(config) > 0:
        if isinstance(config[0], dict):
            return config[0].get(key, default)
    return default
