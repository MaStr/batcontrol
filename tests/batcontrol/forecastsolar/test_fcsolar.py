"""Tests for the forecast.solar provider."""

import traceback

import pytest
import pytz
import requests

from batcontrol.forecastsolar.baseclass import ProviderError
from batcontrol.forecastsolar.fcsolar import FCSolar


@pytest.fixture
def instance():
    """Create a forecast.solar provider with one PV installation."""
    return FCSolar(
        [{
            'name': 'roof',
            'lat': 52.17,
            'lon': 21.25,
            'declination': 30,
            'azimuth': 34,
            'kWp': 10.0,
        }],
        pytz.timezone('Europe/Warsaw'),
        min_time_between_api_calls=900,
    )


def test_request_error_is_wrapped_without_sensitive_details(instance, mocker):
    """ProviderError must not expose an API key from the request URL."""
    mocker.patch(
        'batcontrol.forecastsolar.fcsolar.requests.get',
        side_effect=requests.exceptions.ConnectionError(
            'request failed for https://api.forecast.solar/secret-key/estimate'),
    )

    with pytest.raises(ProviderError) as error_info:
        instance.get_raw_data_from_provider('roof')

    assert str(error_info.value) == 'Forecast solar API request failed'
    formatted_error = ''.join(traceback.format_exception(
        type(error_info.value), error_info.value, error_info.value.__traceback__))
    assert 'secret-key' not in formatted_error


def test_refresh_keeps_cached_data_on_request_error(instance, mocker, caplog):
    """A transient request failure must leave the last good response cached."""
    cached_response = {'result': 'last-known-good'}
    instance.store_raw_data('roof', cached_response)
    mocker.patch(
        'batcontrol.forecastsolar.fcsolar.requests.get',
        side_effect=requests.exceptions.ConnectionError(
            'request failed for https://api.forecast.solar/secret-key/estimate'),
    )

    instance.refresh_data()

    assert instance.get_raw_data('roof') == cached_response
    assert 'secret-key' not in caplog.text
