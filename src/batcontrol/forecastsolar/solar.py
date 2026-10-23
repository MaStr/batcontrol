""" Factory for solar forecast providers """

import logging
from pathlib import Path
from typing import Optional

from ..value_utils import get_config_value, parse_positive_number
from .forecastsolar_interface import ForecastSolarInterface
from .fcsolar import FCSolar
from .evcc_solar import EvccSolar
from .forecast_homeassistant_ml import ForecastSolarHomeAssistantML, PROVIDER_NAME
from .solcast import Solcast

logger = logging.getLogger(__name__)

HA_ML_PROVIDER = PROVIDER_NAME
HA_ML_DEFAULT_URL = 'ws://homeassistant.local:8123'
DOC_URL = 'https://mastr.github.io/batcontrol/configuration/solar-forecast/'


class ForecastSolar:
    """ Factory for solar forecast providers """
    @staticmethod
    def create_solar_provider(config: dict,
                              timezone,
                              min_time_between_api_calls,
                              api_delay=0,
                              requested_provider='fcsolarapi',
                              target_resolution: int = 60,
                              persistent_cache_directory=None) -> ForecastSolarInterface:
        """ Select and configure a solar forecast provider based on the given configuration

        Args:
            config: PV installations configuration (pvinstallations)
            timezone: Timezone for forecast data
            min_time_between_api_calls: Minimum seconds between API calls
            api_delay: Delay for API evaluation
            requested_provider: Provider name ('fcsolarapi', 'evcc-solar',
                                'homeassistant-solar-forecast-ml', 'solcast')
            target_resolution: Target resolution in minutes (15 or 60)
            persistent_cache_directory: Optional directory for restart-safe raw data

        Raises:
            RuntimeError: If provider is unknown or its configuration is incomplete
            ValueError: If configuration is invalid for the provider
        """
        provider = None
        cache_options = {}
        if persistent_cache_directory is not None:
            cache_options['persistent_cache_directory'] = (
                Path(persistent_cache_directory) / requested_provider.lower()
            )
        if requested_provider.lower() == 'fcsolarapi':
            provider = FCSolar(
                config,
                timezone,
                min_time_between_api_calls,
                api_delay,
                target_resolution,
                **cache_options,
            )
        elif requested_provider.lower() == 'evcc-solar':
            provider = EvccSolar(config, timezone, min_time_between_api_calls,
                                 api_delay, target_resolution, **cache_options)
        elif requested_provider.lower() == 'solcast':
            provider = Solcast(config, timezone, min_time_between_api_calls,
                               api_delay, target_resolution, **cache_options)
        elif requested_provider.lower() == HA_ML_PROVIDER:
            provider = _create_homeassistant_ml_provider(
                config,
                timezone,
                min_time_between_api_calls,
                api_delay,
                target_resolution,
                **cache_options,
            )
        else:
            raise RuntimeError(f'[ForecastSolar] Unknown provider {requested_provider}')
        return provider


def _create_homeassistant_ml_provider(
        config: list,
        timezone,
        min_time_between_api_calls,
        api_delay,
        target_resolution: int,
        persistent_cache_directory=None) -> ForecastSolarInterface:
    """Build the HomeAssistant Solar Forecast ML provider from pvinstallations

    Picks the installation entry that belongs to this provider, reads its
    connection details (accepting the deprecated 'base_url'/'apikey'
    spellings) and refuses to continue when a mandatory key is missing.
    """
    installation = _select_installation(config, HA_ML_PROVIDER)
    if installation is None:
        raise RuntimeError(
            f'[ForecastSolar] No pvinstallations entry matches provider '
            f'{HA_ML_PROVIDER}. Add "type: {HA_ML_PROVIDER}" to the entry '
            f'holding url, api_token and entity_id, and remove the entries '
            f'of the other providers. See {DOC_URL}'
        )
    name = installation.get('name', '<unnamed>')
    context = f'pvinstallations[{name}]'

    url = get_config_value(installation, 'url', None, context)
    if not url:
        url = HA_ML_DEFAULT_URL
        logger.warning(
            '[ForecastSolar] No "url" configured for installation %s, '
            'falling back to %s',
            name, HA_ML_DEFAULT_URL
        )

    api_token = get_config_value(installation, 'api_token', None, context)
    _require(api_token, 'api_token', name)

    entity_id = get_config_value(installation, 'entity_id', None, context)
    _require(entity_id, 'entity_id', name)

    cache_ttl_hours = get_config_value(installation, 'cache_ttl_hours', None, context)
    if cache_ttl_hours is not None:
        cache_ttl_hours = parse_positive_number(
            cache_ttl_hours, f'{context}.cache_ttl_hours')

    kwargs = {
        'pvinstallations': [installation],
        'timezone': timezone,
        'base_url': url,
        'api_token': api_token,
        'entity_id': entity_id,
        'min_time_between_api_calls': min_time_between_api_calls,
        'delay_evaluation_by_seconds': api_delay,
        'sensor_unit': get_config_value(installation, 'sensor_unit', 'auto', context),
        'target_resolution': target_resolution,
    }
    if cache_ttl_hours is not None:
        kwargs['cache_ttl_hours'] = cache_ttl_hours
    if persistent_cache_directory is not None:
        kwargs['persistent_cache_directory'] = persistent_cache_directory

    return ForecastSolarHomeAssistantML(**kwargs)


def _require(value, key: str, name: str) -> None:
    """Raise a RuntimeError naming the key and the installation it belongs to

    The message spells out the deprecated alternatives, because reusing the
    key name of another provider is what sent users down this path (#451).
    """
    if value:
        return
    aliases = {
        'api_token': ' Note: "apikey"/"apitoken" are the deprecated spellings '
                     'of this key - "apikey" is also the key name used by '
                     'fcsolarapi and solcast.',
        'entity_id': ' Use the sensor of the Solar Forecast ML integration, '
                     'for example sensor.solar_forecast_ml_evcc_solar_prognose.',
    }
    hint = aliases.get(key, '')
    raise RuntimeError(
        f'[ForecastSolar] {HA_ML_PROVIDER} requires "{key}" in your '
        f'pvinstallations configuration (installation "{name}").{hint} '
        f'See {DOC_URL}'
    )


def _select_installation(config: list, requested_provider: str) -> Optional[dict]:
    """Pick the pvinstallations entry that belongs to the requested provider

    Selection order:
      1. entry whose 'type' matches the requested provider
      2. entry carrying 'entity_id', which only this provider uses
         ('url' is no discriminator - evcc-solar uses the same key)
      3. the single entry, if the list holds exactly one

    Reading only the first entry used to silently ignore a correctly
    configured entry further down the list (#451). With several entries and
    no match there is nothing to guess: naming an unrelated entry in the
    following error message would send the user to the wrong place.

    Args:
        config: List of pvinstallations dicts
        requested_provider: Provider name from solar_forecast_provider

    Returns:
        The selected installation dict, or None if no entry can be identified
    """
    if not isinstance(config, list):
        return None

    entries = [entry for entry in config if isinstance(entry, dict)]
    if not entries:
        return None

    for entry in entries:
        entry_type = entry.get('type')
        if isinstance(entry_type, str) and entry_type.lower() == requested_provider:
            return entry

    for entry in entries:
        if entry.get('entity_id'):
            if entry is not entries[0]:
                logger.info(
                    '[ForecastSolar] Using pvinstallations entry "%s" for %s',
                    entry.get('name', '<unnamed>'), requested_provider
                )
            return entry

    if len(entries) == 1:
        return entries[0]

    return None
