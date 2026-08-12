"""Tests for the forecast.solar provider."""

import datetime
import traceback

from cachetools import TTLCache
import pytest
import pytz
import requests

from batcontrol.fetcher.relaxed_caching import CacheMissError, RelaxedCaching
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


class MutableClock:
    """Controllable monotonic clock for replaying the cache boundary."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def set_cache_clock(instance, clock):
    """Replace the provider cache with one driven by the replay clock."""
    cache = RelaxedCaching()
    cache.cache_store = TTLCache(
        maxsize=cache.max_entries,
        ttl=cache.ttl_seconds,
        timer=clock,
    )
    instance.cache_list['roof'] = cache


def july_24_raw_response(timezone):
    """Last good forecast.solar response before the July 24 outage."""
    production = [
        0, 0, 18, 265, 537, 836, 1377, 2496, 3688, 3994, 3114, 2234,
        2029, 1913, 1592, 1145, 782, 414, 71, 0, 0, 0, 0, 0, 0, 0,
        16, 259, 529, 824, 1137, 1494, 1995, 2467, 2440, 2143, 1935,
        1695, 1400, 1039, 643, 332, 68, 0, 0, 0,
    ]
    current_hour = timezone.localize(datetime.datetime(2026, 7, 24, 2))
    result = {
        (current_hour + datetime.timedelta(hours=index + 1)).isoformat(): value
        for index, value in enumerate(production)
    }
    return {
        'message': {'info': {'time': '2026-07-24T02:03:53+02:00'}},
        'result': result,
    }


def forecast_from_cached_response(instance, mocker, fixed_now):
    """Read FCSolar's native forecast with its wall clock fixed for replay."""
    real_datetime = datetime.datetime
    mocked_datetime = mocker.patch(
        'batcontrol.forecastsolar.fcsolar.datetime.datetime')
    mocked_datetime.now.return_value = fixed_now

    def local_datetime(*args, **kwargs):
        value = real_datetime(*args, **kwargs)
        if value.tzinfo is None:
            return fixed_now.tzinfo.localize(value)
        return value

    mocked_datetime.side_effect = local_datetime
    mocked_datetime.fromisoformat.side_effect = real_datetime.fromisoformat
    forecast = instance.get_forecast_from_raw_data()
    mocker.stop(mocked_datetime)
    return forecast


def test_july_24_network_failure_keeps_unexpired_forecast_available(
        instance, mocker, caplog):
    """The 02:03 cache must remain available through the 05:00 decision."""
    clock = MutableClock()
    set_cache_clock(instance, clock)
    instance.store_raw_data(
        'roof', july_24_raw_response(instance.timezone))
    clock.now = 3 * 3600
    mocker.patch(
        'batcontrol.forecastsolar.fcsolar.requests.get',
        side_effect=requests.exceptions.ConnectionError(
            'request failed for https://api.forecast.solar/secret-key/estimate'),
    )

    instance.refresh_data()
    fixed_now = instance.timezone.localize(datetime.datetime(2026, 7, 24, 5))
    production = forecast_from_cached_response(instance, mocker, fixed_now)

    # The cached 02:03 response is reinterpreted at 05:00: index 0 is the
    # 05:00-06:00 interval and later production remains available to policy.
    assert production[0] == 265
    assert production[1] == 537
    assert production[4] == 2496
    assert len(production) == 43
    assert 'secret-key' not in caplog.text


def test_july_24_cache_boundary_is_usable_before_twelve_hours(instance):
    """The existing cache remains usable strictly before its 12-hour TTL."""
    clock = MutableClock()
    set_cache_clock(instance, clock)
    cached_response = july_24_raw_response(instance.timezone)
    instance.store_raw_data('roof', cached_response)

    clock.now = 12 * 3600 - 0.001

    assert instance.get_raw_data('roof') == cached_response


def test_july_24_cache_boundary_expires_at_twelve_hours(instance, mocker):
    """At exactly 12 hours, existing no-data behavior remains unchanged."""
    clock = MutableClock()
    set_cache_clock(instance, clock)
    instance.store_raw_data(
        'roof', july_24_raw_response(instance.timezone))
    mocker.patch(
        'batcontrol.forecastsolar.fcsolar.requests.get',
        side_effect=requests.exceptions.ConnectionError('network unavailable'),
    )

    clock.now = 12 * 3600
    instance.refresh_data()

    with pytest.raises(CacheMissError):
        instance.get_forecast_from_raw_data()
