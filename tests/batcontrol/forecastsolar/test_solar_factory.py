"""Tests for the solar forecast factory, HomeAssistant Solar Forecast ML branch.

Regression tests for issue #451: a user put the long-lived access token into
'apikey' and the websocket URL into 'url' - both valid keys of *other* solar
providers - so 'api_token' and 'base_url' stayed unset. batcontrol silently
sent '{"type": "auth", "access_token": null}' to HomeAssistant and died with
"expected str at 'access_token'. Got None" instead of naming the config key.
"""

from unittest.mock import patch

import pytest
import pytz

from batcontrol.forecastsolar.solar import ForecastSolar, _select_installation
from batcontrol.forecastsolar.forecast_homeassistant_ml import (
    ForecastSolarHomeAssistantML
)
from batcontrol.value_utils import reset_alias_warnings

PROVIDER = 'homeassistant-solar-forecast-ml'


@pytest.fixture
def timezone():
    """Return Berlin timezone for testing"""
    return pytz.timezone('Europe/Berlin')


@pytest.fixture(autouse=True)
def _clear_alias_warnings():
    """Alias deprecation warnings are emitted once per process"""
    reset_alias_warnings()
    yield
    reset_alias_warnings()


@pytest.fixture
def ha_ml_installation():
    """A complete HA Solar Forecast ML installation entry"""
    return {
        'name': 'HA Solar ML',
        'url': 'ws://homeassistant:8123',
        'api_token': 'token-123',
        'entity_id': 'sensor.solar_forecast_ml_evcc_solar_prognose',
    }


@pytest.fixture
def fcsolar_installation():
    """An fcsolarapi entry as shipped in the dummy config"""
    return {
        'name': 'Demo PV System',
        'type': 'fcsolarapi',
        'lat': 48.4334480,
        'lon': 8.7654968,
        'declination': 30,
        'azimuth': 0,
        'kWp': 10.0,
    }


def _create(config, timezone):
    """Create the provider without touching the network

    sensor_unit defaults to 'auto', which makes __init__ open a WebSocket to
    detect the unit. The factory tests only care about config handling.
    """
    with patch.object(ForecastSolarHomeAssistantML,
                      '_check_sensor_unit', return_value=1.0):
        return ForecastSolar.create_solar_provider(
            config, timezone, 21600, 0,
            requested_provider=PROVIDER
        )


class TestInstallationSelection:
    """The entry belonging to the provider is used, not blindly the first one"""

    def test_single_entry(self, ha_ml_installation, timezone):
        provider = _create([ha_ml_installation], timezone)
        assert provider.api_token == 'token-123'
        assert provider.base_url == 'ws://homeassistant:8123'

    def test_entry_behind_an_fcsolarapi_entry_is_found(
            self, fcsolar_installation, ha_ml_installation, timezone):
        """Regression: only pvinstallations[0] used to be read (#451).

        The shipped dummy config has the fcsolarapi entry first and the
        HA-ML block as a commented second entry, so uncommenting it put
        the HA-ML keys out of reach.
        """
        provider = _create(
            [fcsolar_installation, ha_ml_installation], timezone)
        assert provider.api_token == 'token-123'
        assert provider.entity_id == (
            'sensor.solar_forecast_ml_evcc_solar_prognose')

    def test_type_key_selects_the_entry(self, timezone):
        """'type' was documented but read by nothing before"""
        typed = {
            'name': 'Typed HA ML',
            'type': PROVIDER,
            'url': 'ws://typed:8123',
            'api_token': 'typed-token',
            'entity_id': 'sensor.typed',
        }
        other = {
            'name': 'Other HA ML',
            'url': 'ws://other:8123',
            'api_token': 'other-token',
            'entity_id': 'sensor.other',
        }
        provider = _create([other, typed], timezone)
        assert provider.api_token == 'typed-token'

    def test_type_match_is_case_insensitive(self, ha_ml_installation, timezone):
        ha_ml_installation['type'] = PROVIDER.upper()
        provider = _create([ha_ml_installation], timezone)
        assert provider.api_token == 'token-123'

    def test_single_entry_is_used_even_without_markers(
            self, fcsolar_installation):
        """One entry leaves nothing to guess - the error names it correctly"""
        assert _select_installation(
            [fcsolar_installation], PROVIDER) is fcsolar_installation

    @pytest.mark.parametrize("config", [[], None, 'nonsense', [None]])
    def test_select_installation_without_usable_entries(self, config):
        assert _select_installation(config, PROVIDER) is None

    def test_several_entries_without_a_match_are_not_guessed(
            self, fcsolar_installation):
        """Naming an unrelated entry would send the user to the wrong place"""
        second = {'name': 'Garage', 'lat': 48.0, 'lon': 8.0,
                  'declination': 30, 'azimuth': 0, 'kWp': 5.0}

        assert _select_installation(
            [fcsolar_installation, second], PROVIDER) is None

    def test_several_entries_without_a_match_raise(
            self, fcsolar_installation, timezone):
        second = {'name': 'Garage', 'lat': 48.0, 'lon': 8.0,
                  'declination': 30, 'azimuth': 0, 'kWp': 5.0}

        with pytest.raises(RuntimeError, match='No pvinstallations entry'):
            _create([fcsolar_installation, second], timezone)

    def test_provider_only_gets_its_own_entry(
            self, fcsolar_installation, ha_ml_installation, timezone):
        """refresh_data() fetches once per entry - unrelated entries would
        each trigger another full get_states dump for the same sensor"""
        provider = _create(
            [fcsolar_installation, ha_ml_installation], timezone)

        assert provider.pvinstallations == [ha_ml_installation]
        assert list(provider.cache_list) == ['HA Solar ML']


class TestDeprecatedKeySpellings:
    """'apikey' and 'base_url' keep working, with a warning"""

    def test_apikey_is_accepted(self, ha_ml_installation, timezone, caplog):
        del ha_ml_installation['api_token']
        ha_ml_installation['apikey'] = 'legacy-token'

        with caplog.at_level('WARNING'):
            provider = _create([ha_ml_installation], timezone)

        assert provider.api_token == 'legacy-token'
        assert any('deprecated' in r.getMessage() for r in caplog.records)

    def test_base_url_is_accepted(self, ha_ml_installation, timezone):
        del ha_ml_installation['url']
        ha_ml_installation['base_url'] = 'ws://legacy:8123'

        provider = _create([ha_ml_installation], timezone)

        assert provider.base_url == 'ws://legacy:8123'

    def test_issue_451_configuration_works(self, timezone, caplog):
        """The exact entry from issue #451 now starts up"""
        installation = {
            'name': 'Haus',
            'url': 'ws://homeassistant:8123',
            'apikey': 'eyJhbGciOi-long-lived-token',
            'entity_id': 'sensor.solar_forecast_ml_evcc_solar_prognose',
        }

        with caplog.at_level('WARNING'):
            provider = _create([installation], timezone)

        assert provider.api_token == 'eyJhbGciOi-long-lived-token'
        assert provider.base_url == 'ws://homeassistant:8123'


class TestMandatoryKeys:
    """Missing keys fail before the first WebSocket connect"""

    @pytest.mark.parametrize("missing", ['api_token', 'entity_id'])
    def test_missing_key_raises_with_key_name(
            self, ha_ml_installation, timezone, missing):
        del ha_ml_installation[missing]

        with pytest.raises(RuntimeError, match=missing):
            _create([ha_ml_installation], timezone)

    @pytest.mark.parametrize("empty", [None, ''])
    def test_empty_token_raises(self, ha_ml_installation, timezone, empty):
        """The add-on delivers a cleared field as '' and an unset one as null"""
        ha_ml_installation['api_token'] = empty

        with pytest.raises(RuntimeError, match='api_token'):
            _create([ha_ml_installation], timezone)

    def test_error_names_the_installation_and_the_deprecated_key(
            self, ha_ml_installation, timezone):
        del ha_ml_installation['api_token']

        with pytest.raises(RuntimeError) as excinfo:
            _create([ha_ml_installation], timezone)

        message = str(excinfo.value)
        assert 'HA Solar ML' in message
        assert 'apikey' in message

    def test_no_connection_attempt_on_missing_token(
            self, ha_ml_installation, timezone):
        """The factory must fail before any network I/O"""
        del ha_ml_installation['api_token']

        with patch('batcontrol.forecastsolar.forecast_homeassistant_ml.connect'
                   ) as mock_connect:
            with pytest.raises(RuntimeError):
                ForecastSolar.create_solar_provider(
                    [ha_ml_installation], timezone, 21600, 0,
                    requested_provider=PROVIDER
                )

        mock_connect.assert_not_called()

    def test_missing_url_falls_back_to_default_with_warning(
            self, ha_ml_installation, timezone, caplog):
        del ha_ml_installation['url']

        with caplog.at_level('WARNING'):
            provider = _create([ha_ml_installation], timezone)

        assert provider.base_url == 'ws://homeassistant.local:8123'
        assert any('url' in r.getMessage() for r in caplog.records)


class TestCacheTtlHours:
    """cache_ttl_hours was documented since #257 but never read"""

    def test_configured_ttl_reaches_the_cache(
            self, ha_ml_installation, timezone):
        ha_ml_installation['cache_ttl_hours'] = 24.0

        provider = _create([ha_ml_installation], timezone)

        assert provider.cache_list['HA Solar ML'].ttl_seconds == 24 * 3600

    def test_default_ttl_is_unchanged(self, ha_ml_installation, timezone):
        provider = _create([ha_ml_installation], timezone)

        assert provider.cache_list['HA Solar ML'].ttl_seconds == 12 * 3600

    @pytest.mark.parametrize("invalid", [0, -1, 'abc'])
    def test_invalid_ttl_raises(self, ha_ml_installation, timezone, invalid):
        ha_ml_installation['cache_ttl_hours'] = invalid

        with pytest.raises(ValueError, match='cache_ttl_hours'):
            _create([ha_ml_installation], timezone)


class TestUnknownProvider:
    """Unchanged behaviour of the factory's else branch"""

    def test_unknown_provider_raises(self, timezone):
        with pytest.raises(RuntimeError, match='Unknown provider'):
            ForecastSolar.create_solar_provider(
                [{'name': 'x'}], timezone, 21600, 0,
                requested_provider='does-not-exist'
            )


@patch("batcontrol.forecastsolar.solar.FCSolar")
def test_fcsolar_receives_persistent_cache_directory(mock_fcsolar, tmp_path):
    """Only forecast.solar receives the restart-cache directory."""
    installations = [{"name": "roof"}]
    timezone = pytz.timezone("Europe/Warsaw")

    ForecastSolar.create_solar_provider(
        installations,
        timezone,
        min_time_between_api_calls=900,
        requested_provider="fcsolarapi",
        persistent_cache_directory=tmp_path,
    )

    mock_fcsolar.assert_called_once_with(
        installations,
        timezone,
        900,
        0,
        60,
        persistent_cache_directory=tmp_path,
    )
