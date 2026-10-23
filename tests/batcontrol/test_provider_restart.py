"""Exercise persisted raw responses through the real provider factories."""

import datetime
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
import pytz
import requests

from batcontrol.dynamictariff.dynamictariff import DynamicTariff
from batcontrol.fetcher.relaxed_caching import CacheMissError
from batcontrol.forecastsolar.forecast_homeassistant_ml import (
    ForecastSolarHomeAssistantML, _unit_to_wh_factor,
)
from batcontrol.forecastsolar.solar import ForecastSolar

SOLAR_PROVIDERS = [
    'fcsolarapi', 'evcc-solar', 'solcast', 'homeassistant-solar-forecast-ml',
]
TARIFF_PROVIDERS = [
    'awattar_at', 'awattar_de', 'tibber', 'evcc',
    'energyforecast', 'energyforecast_96', 'energyforecast_total_price',
]
SECRET = 'test-secret-do-not-log'


@pytest.mark.parametrize('unit,expected', [('Wh', 1.0), ('kWh', 1000.0), (None, 1.0)])
def test_unit_conversion_is_a_pure_value_mapping(unit, expected):
    """Live and cached metadata use the same conversion without network I/O."""
    assert _unit_to_wh_factor(unit) == expected


def test_unsupported_unit_is_rejected():
    """An unsupported unit must never silently change the energy scale."""
    with pytest.raises(ValueError, match='Unsupported unit_of_measurement'):
        _unit_to_wh_factor('MW')


@pytest.fixture
def now(monkeypatch):
    """Keep providers, timestamps and interval alignment on one fixed hour."""
    real_datetime = datetime.datetime
    reference = real_datetime(2026, 9, 23, 5, tzinfo=datetime.timezone.utc)

    class FrozenDatetime(real_datetime):
        """A fixed wall clock with normal timestamp parsing."""

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return reference.astimezone().replace(tzinfo=None)
            return reference.astimezone(tz)

    monkeypatch.setattr(datetime, 'datetime', FrozenDatetime)
    try:
        with monkeypatch.context() as environment:
            environment.setenv('TZ', 'UTC')
            time.tzset()
            yield reference
    finally:
        time.tzset()


def solar_installation(provider):
    """A fully configured roof with no real credentials or network endpoint."""
    return {
        'name': 'roof', 'type': provider, 'lat': 50, 'lon': 8,
        'declination': 30, 'azimuth': 0, 'kWp': 10,
        'url': 'http://provider.invalid', 'api_token': SECRET,
        'entity_id': 'sensor.solar', 'resource_id': 'site',
    }


def solar_response(provider, now):
    """Each provider receives its own timestamped raw response format."""
    if provider == 'fcsolarapi':
        return {
            'message': {'info': {'time': now.isoformat()}},
            'result': {
                (now + datetime.timedelta(hours=i + 1)).isoformat(): 1000
                for i in range(48)
            },
        }
    if provider == 'solcast':
        return {'forecasts': [
            {'period_end': (now + datetime.timedelta(minutes=30 * (i + 1))).isoformat(),
             'pv_estimate': 1.0}
            for i in range(96)
        ]}
    if provider == 'evcc-solar':
        return {'rates': [
            {'start': (now + datetime.timedelta(minutes=15 * i)).isoformat(),
             'value': 1000}
            for i in range(192)
        ]}
    return {
        'entity_id': 'sensor.solar', 'state': 'available',
        'attributes': {
            'unit_of_measurement': 'kWh',
            'forecast': [
                {'start': (now + datetime.timedelta(hours=i)).isoformat(),
                 'end': (now + datetime.timedelta(hours=i + 1)).isoformat(),
                 'value': 1.0}
                for i in range(48)
            ],
        },
    }


def create_solar(provider, directory, **overrides):
    """Construct through the public factory so missing wiring is visible."""
    installation = {**solar_installation(provider), **overrides}
    return ForecastSolar.create_solar_provider(
        [installation], pytz.UTC, 900, api_delay=0,
        requested_provider=provider, target_resolution=60,
        persistent_cache_directory=directory,
    )


@pytest.mark.parametrize('provider', SOLAR_PROVIDERS)
def test_solar_restart_uses_cache_after_real_request_failure(
        provider, tmp_path, now, mocker, caplog):
    """Reload, refresh failure, raw parsing and alignment all work offline."""
    with patch.object(ForecastSolarHomeAssistantML,
                      '_check_sensor_unit', return_value=1000.0):
        first = create_solar(provider, tmp_path)
    response = solar_response(provider, now)
    first.store_raw_data('roof', response)
    files = list(tmp_path.rglob('*.json'))
    assert len(files) == 1
    assert files[0].parent == tmp_path / provider
    assert SECRET not in files[0].read_text(encoding='utf-8')
    stored_at = json.loads(files[0].read_text(encoding='utf-8'))['stored_at']

    get = mocker.patch(
        'requests.get', side_effect=requests.ConnectionError(SECRET))
    connect = mocker.patch.object(
        ForecastSolarHomeAssistantML, '_websocket_connect',
        new_callable=AsyncMock, side_effect=OSError(SECRET),
    )
    restarted = create_solar(provider, tmp_path)
    forecast = restarted.get_forecast()

    assert forecast[0] == pytest.approx(1000)
    assert len(forecast) >= 48
    assert restarted.get_raw_data('roof') == response
    assert json.loads(files[0].read_text(
        encoding='utf-8'))['stored_at'] == stored_at
    if provider == 'homeassistant-solar-forecast-ml':
        connect.assert_awaited_once()
        get.assert_not_called()
    else:
        get.assert_called_once()
        connect.assert_not_called()
    assert SECRET not in caplog.text


@pytest.mark.parametrize('provider', SOLAR_PROVIDERS)
def test_expired_solar_cache_keeps_existing_no_data_behavior(
        provider, tmp_path, now, mocker):
    """Persistence cannot make a provider use expired data or invent zeros."""
    first = create_solar(provider, tmp_path, sensor_unit='kWh')
    first.store_raw_data('roof', solar_response(provider, now))
    file = next(tmp_path.rglob('*.json'))
    document = json.loads(file.read_text(encoding='utf-8'))
    document['stored_at'] = time.time() - 12 * 3600
    file.write_text(json.dumps(document), encoding='utf-8')
    mocker.patch('requests.get',
                 side_effect=requests.ConnectionError('offline'))
    mocker.patch.object(ForecastSolarHomeAssistantML, '_websocket_connect',
                        new_callable=AsyncMock, side_effect=OSError('offline'))

    restarted = create_solar(provider, tmp_path, sensor_unit='kWh')

    with pytest.raises(CacheMissError):
        restarted.get_forecast()


def test_ha_invalid_cached_unit_uses_live_discovery(tmp_path, now, mocker):
    """Bad cached metadata must not prevent a successful provider refresh."""
    provider = 'homeassistant-solar-forecast-ml'
    first = create_solar(provider, tmp_path, sensor_unit='kWh')
    response = solar_response(provider, now)
    response['attributes']['unit_of_measurement'] = 'invalid-unit'
    first.store_raw_data('roof', response)
    discovery = mocker.patch.object(
        ForecastSolarHomeAssistantML, '_check_sensor_unit_async',
        new_callable=AsyncMock, return_value=1000.0,
    )

    restarted = create_solar(provider, tmp_path)

    assert restarted.unit_conversion_factor == 1000.0
    discovery.assert_awaited_once()


def test_ha_auto_unit_tracks_fresh_response_after_cached_startup(tmp_path, now, mocker):
    """A successful refresh supersedes the cached unit as well as its values."""
    provider = 'homeassistant-solar-forecast-ml'
    first = create_solar(provider, tmp_path, sensor_unit='kWh')
    first.store_raw_data('roof', solar_response(provider, now))
    response = solar_response(provider, now)
    response['attributes']['unit_of_measurement'] = 'Wh'
    for entry in response['attributes']['forecast']:
        entry['value'] = 1000.0
    fetch = mocker.patch.object(
        ForecastSolarHomeAssistantML, '_fetch_entity_state_async',
        new_callable=AsyncMock, return_value=response,
    )

    restarted = create_solar(provider, tmp_path)

    assert restarted.get_forecast()[0] == pytest.approx(1000)
    assert restarted.unit_conversion_factor == 1.0
    fetch.assert_awaited_once()


def test_ha_missing_unit_warns_once_instead_of_every_evaluation(
        tmp_path, now, mocker, caplog):
    """An evcc-style sensor without units keeps its existing Wh assumption."""
    provider = 'homeassistant-solar-forecast-ml'
    first = create_solar(provider, tmp_path, sensor_unit='Wh')
    response = solar_response(provider, now)
    del response['attributes']['unit_of_measurement']
    first.store_raw_data('roof', response)
    mocker.patch.object(ForecastSolarHomeAssistantML, '_websocket_connect',
                       new_callable=AsyncMock, side_effect=OSError('offline'))

    restarted = create_solar(provider, tmp_path)
    restarted.get_forecast()
    restarted.get_forecast()

    assert sum('no unit_of_measurement' in record.getMessage()
               for record in caplog.records) == 1


def test_switching_solar_provider_does_not_load_another_format(tmp_path, now):
    """The same installation name must not share raw data between providers."""
    first = create_solar('fcsolarapi', tmp_path)
    first.store_raw_data('roof', solar_response('fcsolarapi', now))

    switched = create_solar('solcast', tmp_path)

    with pytest.raises(CacheMissError):
        switched.get_raw_data('roof')


@pytest.mark.parametrize('age_hours,usable', [(13, True), (24, False)])
def test_ha_configured_ttl_is_preserved_after_restart(
        tmp_path, now, age_hours, usable):
    """Upstream's provider-specific TTL keeps its original strict boundary."""
    provider = 'homeassistant-solar-forecast-ml'
    first = create_solar(provider, tmp_path,
                         sensor_unit='kWh', cache_ttl_hours=24)
    first.store_raw_data('roof', solar_response(provider, now))
    file = next(tmp_path.rglob('*.json'))
    document = json.loads(file.read_text(encoding='utf-8'))
    document['stored_at'] = time.time() - age_hours * 3600
    file.write_text(json.dumps(document), encoding='utf-8')

    restarted = create_solar(
        provider, tmp_path, sensor_unit='kWh', cache_ttl_hours=24)

    assert restarted.cache_list['roof'].ttl_seconds == 24 * 3600
    if usable:
        assert restarted.get_raw_data('roof') == document['data']
    else:
        with pytest.raises(CacheMissError):
            restarted.get_raw_data('roof')


def tariff_config(provider):
    """Select public factory branches without live API access."""
    return {
        'type': provider, 'api_token': SECRET,
        'url': 'http://provider.invalid',
        'vat': 0, 'fees': 0, 'markup': 0,
    }


def tariff_response(provider, now):
    """Produce 48 hours of prices in each API's raw shape."""
    if provider.startswith('awattar'):
        return {'data': [
            {'start_timestamp': int((now + datetime.timedelta(hours=i)).timestamp() * 1000),
             'marketprice': 200}
            for i in range(48)
        ]}
    rates = [
        {'start': (now + datetime.timedelta(minutes=15 * i)).isoformat(),
         'end': (now + datetime.timedelta(minutes=15 * (i + 1))).isoformat(),
         'value': 0.2, 'startsAt': (now + datetime.timedelta(minutes=15 * i)).isoformat(),
         'total': 0.2, 'price_ct_kwh': 20, 'total_ct_kwh': 20}
        for i in range(192)
    ]
    if provider == 'tibber':
        return {'data': {'viewer': {'homes': [
            {'currentSubscription': {'priceInfo': {'today': rates, 'tomorrow': []}}},
        ]}}}
    if provider == 'evcc':
        return {'rates': rates}
    return {'data': rates}


def create_tariff(provider, directory):
    """Construct through the factory, including its persistent cache path."""
    return DynamicTariff.create_tarif_provider(
        tariff_config(provider), pytz.UTC, 900, 0,
        target_resolution=60, persistent_cache_directory=directory,
    )


@pytest.mark.parametrize('provider', TARIFF_PROVIDERS)
def test_tariff_restart_uses_cache_after_real_request_failure(
        provider, tmp_path, now, mocker, caplog):
    """A failed request cannot hide fresh prices from a restarted provider."""
    first = create_tariff(provider, tmp_path)
    response = tariff_response(provider, now)
    first.store_raw_data(response)
    file = next(tmp_path.rglob('*.json'))
    assert file.parent == tmp_path / provider
    assert SECRET not in file.read_text(encoding='utf-8')
    stored_at = json.loads(file.read_text(encoding='utf-8'))['stored_at']
    get = mocker.patch(
        'requests.get', side_effect=requests.ConnectionError(SECRET))
    post = mocker.patch(
        'requests.post', side_effect=requests.ConnectionError(SECRET))

    restarted = create_tariff(provider, tmp_path)
    prices = restarted.get_prices()

    assert len(prices) >= 48
    assert prices[0] == pytest.approx(0.2)
    assert restarted.get_raw_data() == response
    assert json.loads(file.read_text(encoding='utf-8')
                      )['stored_at'] == stored_at
    assert get.call_count + post.call_count == 1
    assert SECRET not in caplog.text


@pytest.mark.parametrize('provider', TARIFF_PROVIDERS)
def test_expired_tariff_cache_is_not_used(provider, tmp_path, now, mocker):
    """An unavailable price API and expired cache must remain a cache miss."""
    first = create_tariff(provider, tmp_path)
    first.store_raw_data(tariff_response(provider, now))
    file = next(tmp_path.rglob('*.json'))
    document = json.loads(file.read_text(encoding='utf-8'))
    document['stored_at'] = time.time() - 12 * 3600
    file.write_text(json.dumps(document), encoding='utf-8')
    mocker.patch('requests.get',
                 side_effect=requests.ConnectionError('offline'))
    mocker.patch('requests.post',
                 side_effect=requests.ConnectionError('offline'))

    restarted = create_tariff(provider, tmp_path)

    with pytest.raises(CacheMissError):
        restarted.get_prices()


def test_static_tariff_rebuilds_from_current_configuration(tmp_path):
    """A local tariff does not depend on the contents of its raw API cache."""
    first = DynamicTariff.create_tarif_provider(
        {'type': 'tariff_zones', 'tariff_zone_1': 0.2}, pytz.UTC, 900, 0,
        persistent_cache_directory=tmp_path,
    )
    first.get_prices()

    restarted = DynamicTariff.create_tarif_provider(
        {'type': 'tariff_zones', 'tariff_zone_1': 0.3}, pytz.UTC, 900, 0,
        persistent_cache_directory=tmp_path,
    )

    assert restarted.get_prices()[0] == pytest.approx(0.3)


def test_tariff_types_do_not_share_cache(tmp_path, now):
    """Even providers backed by the same class have separate raw responses."""
    first = create_tariff('awattar_at', tmp_path)
    first.store_raw_data(tariff_response('awattar_at', now))

    switched = create_tariff('awattar_de', tmp_path)

    with pytest.raises(CacheMissError):
        switched.get_raw_data()
