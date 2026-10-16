"""Application level tests: envelope, error mapping and OpenAPI metadata."""
from fastapi.testclient import TestClient

from batcontrol.web.app import create_app
from batcontrol.web.config import ApiConfig


def test_health_is_wrapped_in_the_envelope(client):
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is True
    assert isinstance(body['timestamp'], int)
    assert body['data']['status'] == 'ok'


def test_version_endpoint(client):
    body = client.get('/api/v1/version').json()
    assert body['data']['interval_minutes'] == 60
    assert body['data']['runtime_env']


def test_health_works_without_a_completed_cycle(empty_bc):
    from fastapi.testclient import TestClient

    from batcontrol.web.app import create_app
    with TestClient(create_app(empty_bc), base_url='http://127.0.0.1') as client:
        body = client.get('/api/v1/health').json()
    assert body['data']['has_data'] is False


def test_unknown_path_is_a_clean_404(client):
    assert client.get('/api/v1/nope').status_code == 404


def test_openapi_metadata_is_informative(client):
    schema = client.get('/openapi.json').json()
    assert schema['info']['title'] == 'batcontrol'
    assert 'never changes anything' in schema['info']['description']
    # Units must be documented on the fields, not just in prose.
    health = schema['components']['schemas']['Health']
    assert 'description' in health['properties']['evaluation_interval_seconds']


def test_swagger_ui_is_served(client):
    response = client.get('/docs')
    assert response.status_code == 200
    assert 'swagger' in response.text.lower()


def test_a_reverse_proxy_host_header_is_accepted(fake_bc):
    """Maintainer decision: REST does not validate the Host header, so a
    reverse proxy forwarding its own host name works on a loopback bind."""
    with TestClient(create_app(fake_bc), base_url='http://127.0.0.1') as client:
        response = client.get('/api/v1/battery',
                              headers={'Host': 'batcontrol.example.org'})
    assert response.status_code == 200


def test_docs_and_schema_are_off_by_default(fake_bc):
    """api.docs_enabled defaults to false: no Swagger UI, no schema."""
    with TestClient(create_app(fake_bc, ApiConfig(dashboard_enabled=False)),
                    base_url='http://127.0.0.1') as client:
        assert client.get('/docs').status_code == 404
        assert client.get('/openapi.json').status_code == 404
        assert client.get('/api/v1/health').status_code == 200


def test_docs_stay_off_behind_the_dashboard_mount(fake_bc, tmp_path, monkeypatch):
    """With the dashboard mounted at / (the default), StaticFiles answers the
    two paths - and must not find anything there either."""
    from batcontrol.web import app as app_module  # pylint: disable=import-outside-toplevel
    (tmp_path / 'index.html').write_text('<!doctype html>')
    (tmp_path / 'swagger').mkdir()
    (tmp_path / 'swagger' / 'swagger-ui-bundle.js').write_text('//')
    monkeypatch.setattr(app_module, 'STATIC_DIR', tmp_path)
    with TestClient(create_app(fake_bc), base_url='http://127.0.0.1') as client:
        assert client.get('/').status_code == 200
        assert client.get('/docs').status_code == 404
        assert client.get('/openapi.json').status_code == 404


def test_the_schema_can_still_be_generated_when_docs_are_off(fake_bc):
    """scripts/dump_openapi.py relies on app.openapi(), not on the route."""
    schema = create_app(fake_bc).openapi()
    assert '/api/v1/status' in schema['paths']
