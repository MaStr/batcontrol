"""Endpoint tests of the read-only API.

These cover what the HTTP layer adds: the envelope, status codes, parameter
validation and that no credential reaches a response. The payload values come
from the service layer and are tested there, in test_service.py.
"""
import pytest
from fastapi.testclient import TestClient

from batcontrol.web.app import create_app

from .conftest import API_PATHS


def data(response):
    """The payload of a successful envelope, with the envelope checked."""
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True
    assert isinstance(body['timestamp'], int)
    return body['data']


@pytest.mark.parametrize('path', API_PATHS.values())
def test_every_endpoint_answers_with_an_envelope(client, fake_bc, path):
    fake_bc.record_decision()
    assert data(client.get(path)) is not None


@pytest.mark.parametrize('series,unit', [
    ('price', 'EUR/kWh'),
    ('consumption', 'Wh'),
    ('production', 'Wh'),
    ('net_consumption', 'Wh'),
])
def test_every_forecast_series_is_routed(client, series, unit):
    assert data(client.get(f'/api/v1/forecast/{series}'))['unit'] == unit


def test_unknown_forecast_series_is_a_404_with_a_hint(client):
    response = client.get('/api/v1/forecast/weather')
    assert response.status_code == 404
    body = response.json()
    assert body['error'] == 'not_found'
    assert 'net_consumption' in body['detail']


@pytest.mark.parametrize('path', [
    '/api/v1/forecast', '/api/v1/price/status', '/api/v1/solar',
    '/api/v1/peak-shaving'])
def test_cycle_endpoints_before_the_first_cycle_are_503(empty_bc, path):
    with TestClient(create_app(empty_bc), base_url='http://127.0.0.1') as client:
        response = client.get(path)
    assert response.status_code == 503
    body = response.json()
    assert body['ok'] is False
    assert body['error'] == 'no_data_yet'
    assert 'first evaluation' in body['detail']


def test_decision_without_a_trace_is_404(client):
    response = client.get('/api/v1/decision')
    assert response.status_code == 404
    assert response.json()['error'] == 'not_found'


def test_decision_history_respects_the_limit(client, fake_bc):
    for _ in range(4):
        fake_bc.record_decision()
    payload = data(client.get('/api/v1/decision/history?limit=2'))
    assert payload['count'] == 2


@pytest.mark.parametrize('limit', [0, -1, 201, 'many'])
def test_decision_history_rejects_an_invalid_limit(client, limit):
    assert client.get(f'/api/v1/decision/history?limit={limit}').status_code == 422


def test_unknown_inverter_is_a_404(client):
    response = client.get('/api/v1/inverter/5')
    assert response.status_code == 404
    assert '/inverters' in response.json()['detail']


def test_inverter_id_must_be_a_number(client):
    assert client.get('/api/v1/inverter/abc').status_code == 422


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
    for path in API_PATHS.values():
        body = client.get(path).text.lower()
        for needle in forbidden:
            assert needle not in body, f'{path} mentions {needle!r}'
