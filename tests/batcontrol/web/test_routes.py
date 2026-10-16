"""Endpoint tests of the read-only API."""
import pytest
from fastapi.testclient import TestClient

from batcontrol.web.app import create_app


def data(response):
    """The payload of a successful envelope, with the envelope checked."""
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True
    assert isinstance(body['timestamp'], int)
    return body['data']


def test_status(client):
    payload = data(client.get('/api/v1/status'))
    assert payload['mode'] == 10
    assert payload['mode_label'] == 'ALLOW DISCHARGE'
    assert payload['online'] is True
    assert payload['next_evaluation'] > payload['last_evaluation']


def test_battery(client):
    payload = data(client.get('/api/v1/battery'))
    assert payload['soc'] == 87.4
    assert payload['soc_limits']['min_soc'] == 7.0
    assert payload['reserved_energy_capacity'] == 1500.0


def test_current(client):
    payload = data(client.get('/api/v1/current'))
    assert payload['price']['current'] == 0.308
    assert payload['price']['unit'] == 'EUR/kWh'
    assert payload['soc'] == 87.4


def test_forecast_bundle_shares_one_slot_grid(client):
    payload = data(client.get('/api/v1/forecast'))
    grids = {
        series: [slot['time_start'] for slot in payload[series]]
        for series in ('price', 'consumption', 'production', 'net_consumption')
    }
    assert len({tuple(grid) for grid in grids.values()}) == 1


@pytest.mark.parametrize('series,unit', [
    ('price', 'EUR/kWh'),
    ('consumption', 'Wh'),
    ('production', 'Wh'),
    ('net_consumption', 'Wh'),
])
def test_single_forecast_series(client, series, unit):
    payload = data(client.get(f'/api/v1/forecast/{series}'))
    assert payload['unit'] == unit
    assert payload['total_count'] == 4
    assert payload['slots'][0]['slot'] == 0


def test_unknown_forecast_series_is_a_404_with_a_hint(client):
    response = client.get('/api/v1/forecast/weather')
    assert response.status_code == 404
    body = response.json()
    assert body['error'] == 'not_found'
    assert 'net_consumption' in body['detail']


def test_forecast_before_the_first_cycle_is_503(empty_bc):
    with TestClient(create_app(empty_bc), base_url='http://127.0.0.1') as client:
        response = client.get('/api/v1/forecast')
    assert response.status_code == 503
    body = response.json()
    assert body['ok'] is False
    assert body['error'] == 'no_data_yet'
    assert 'first evaluation' in body['detail']


def test_decision_without_a_trace_is_404(client):
    response = client.get('/api/v1/decision')
    assert response.status_code == 404
    assert response.json()['error'] == 'not_found'


def test_decision(client, fake_bc):
    fake_bc.record_decision()
    payload = data(client.get('/api/v1/decision'))
    assert payload['decisive']['reason'] == 'ALWAYS_ALLOW_DISCHARGE_LIMIT'
    assert payload['status_text']
    assert payload['trace']['records']


def test_decision_history_respects_the_limit(client, fake_bc):
    for _ in range(4):
        fake_bc.record_decision()
    payload = data(client.get('/api/v1/decision/history?limit=2'))
    assert payload['count'] == 2


@pytest.mark.parametrize('limit', [0, -1, 201, 'many'])
def test_decision_history_rejects_an_invalid_limit(client, limit):
    assert client.get(f'/api/v1/decision/history?limit={limit}').status_code == 422


def test_config_exposes_runtime_parameters(client):
    payload = data(client.get('/api/v1/config'))
    assert payload['battery_control']['min_price_difference'] == 0.05
    assert payload['peak_shaving']['enabled'] is False


def test_config_contains_no_credentials(client, fake_bc):
    """A credential must not be reachable through the config endpoint.

    The stand-in is given a config dict full of secrets on purpose: the
    endpoint builds its answer from core attributes, so none of this can
    surface. If someone changes the service to read the config dict, the
    secrets would appear here and this test fails.
    """
    fake_bc.config = {
        'inverter': {'user': 'customer', 'password': 'inverter-pw'},
        'utility': {'api_token': 'tibber-token'},
        'mqtt': {'username': 'mqtt-user', 'password': 'mqtt-pw'},
    }
    body = client.get('/api/v1/config').text
    for secret in ('inverter-pw', 'tibber-token', 'mqtt-user', 'mqtt-pw',
                   'customer'):
        assert secret not in body


def test_no_response_contains_a_secret_looking_key(client, fake_bc):
    """Sweep every endpoint for credential-shaped keys."""
    fake_bc.record_decision()
    forbidden = ('password', 'token', 'secret', 'apikey', 'api_key',
                 'username')
    paths = [
        '/api/v1/health', '/api/v1/version', '/api/v1/status',
        '/api/v1/battery', '/api/v1/current', '/api/v1/forecast',
        '/api/v1/decision', '/api/v1/decision/history', '/api/v1/config',
    ]
    for path in paths:
        body = client.get(path).text.lower()
        for needle in forbidden:
            assert needle not in body, f'{path} mentions {needle!r}'
