"""Serving the dashboard assets.

Both branches are forced explicitly, because whether src/batcontrol/web/static
exists depends on whether someone ran the npm build - true on a developer's
machine, false in CI. A test that silently tested only one of them would be
worse than no test.
"""
import pytest
from fastapi.testclient import TestClient

from batcontrol.web import app as app_module
from batcontrol.web.app import create_app
from batcontrol.web.config import ApiConfig


@pytest.fixture(name='built')
def fixture_built(tmp_path, monkeypatch):
    """A directory that looks like a finished dashboard build."""
    static = tmp_path / 'static'
    (static / 'assets').mkdir(parents=True)
    (static / 'index.html').write_text('<!doctype html><title>batcontrol</title>')
    (static / 'assets' / 'index-abc123.js').write_text('console.log(1)')
    (static / 'licenses').mkdir()
    (static / 'licenses' / 'LICENSE-OFL-VT323.txt').write_text('OFL')
    (static / 'swagger').mkdir()
    (static / 'swagger' / 'swagger-ui-bundle.js').write_text('//swagger')
    (static / 'swagger' / 'swagger-ui.css').write_text('/*swagger*/')
    (static / 'favicon.svg').write_text('<svg/>')
    monkeypatch.setattr(app_module, 'STATIC_DIR', static)
    return static


@pytest.fixture(name='unbuilt')
def fixture_unbuilt(tmp_path, monkeypatch):
    """A checkout where npm never ran."""
    monkeypatch.setattr(app_module, 'STATIC_DIR', tmp_path / 'nothing-here')


def client_for(fake_bc, **options):
    options.setdefault('docs_enabled', True)
    return TestClient(create_app(fake_bc, ApiConfig(enabled=True, **options)),
                      base_url='http://127.0.0.1')


def test_index_is_served_at_the_root(fake_bc, built):
    del built
    with client_for(fake_bc) as client:
        response = client.get('/')
    assert response.status_code == 200
    assert 'batcontrol' in response.text


def test_hashed_assets_are_served(fake_bc, built):
    del built
    with client_for(fake_bc) as client:
        assert client.get('/assets/index-abc123.js').status_code == 200


def test_font_licenses_are_reachable(fake_bc, built):
    """The OFL requires the license text to travel with the fonts."""
    del built
    with client_for(fake_bc) as client:
        assert client.get('/licenses/LICENSE-OFL-VT323.txt').status_code == 200


def test_the_api_keeps_precedence_over_the_dashboard(fake_bc, built):
    """The dashboard is mounted at /, so this is not self-evident."""
    del built
    with client_for(fake_bc) as client:
        assert client.get('/api/v1/health').json()['ok'] is True
        assert client.get('/openapi.json').status_code == 200
        assert client.get('/docs').status_code == 200


def test_path_traversal_is_refused(fake_bc, built):
    del built
    with client_for(fake_bc) as client:
        response = client.get('/../../pyproject.toml')
    assert response.status_code in (400, 404)


def test_a_missing_build_serves_a_hint_not_a_stack_trace(fake_bc, unbuilt, caplog):
    del unbuilt
    with caplog.at_level('WARNING'):
        with client_for(fake_bc) as client:
            response = client.get('/')
    assert response.status_code == 200
    assert 'npm' in response.text
    assert 'npm' in caplog.text


def test_the_api_works_without_a_build(fake_bc, unbuilt):
    del unbuilt
    with client_for(fake_bc) as client:
        assert client.get('/api/v1/health').status_code == 200


def test_the_dashboard_can_be_switched_off(fake_bc, built):
    del built
    with client_for(fake_bc, dashboard_enabled=False) as client:
        assert client.get('/').status_code == 404
        assert client.get('/api/v1/health').status_code == 200


def test_docs_work_without_the_dashboard(fake_bc, built):
    """Regression: the Swagger bundle was only served by the dashboard
    mount, so /docs rendered blank with dashboard_enabled: false."""
    del built
    with client_for(fake_bc, dashboard_enabled=False) as client:
        assert client.get('/swagger/swagger-ui-bundle.js').status_code == 200
        assert client.get('/swagger/swagger-ui.css').status_code == 200
        assert client.get('/').status_code == 404


def test_docs_use_the_bundled_swagger_ui(fake_bc, built):
    """FastAPI points /docs at a CDN by default.

    On a Home Assistant box without internet egress that leaves the API
    documentation blank, which is the same reason the fonts are bundled.
    """
    del built
    with client_for(fake_bc) as client:
        page = client.get('/docs').text
    assert 'swagger/swagger-ui-bundle.js' in page
    assert 'swagger/swagger-ui.css' in page


def test_docs_make_no_external_request(fake_bc, built):
    del built
    with client_for(fake_bc) as client:
        page = client.get('/docs').text
    for host in ('cdn.jsdelivr.net', 'unpkg.com', 'fastapi.tiangolo.com'):
        assert host not in page, f'/docs still loads from {host}'


def test_docs_asset_urls_are_relative_for_ingress(fake_bc, built):
    """Under ingress the page lives at /api/hassio_ingress/<token>/docs."""
    del built
    with client_for(fake_bc) as client:
        page = client.get('/docs').text
    assert 'src="swagger/' in page
    assert 'src="/swagger/' not in page


def test_docs_fall_back_to_the_cdn_without_a_build(fake_bc, unbuilt):
    """A source checkout without npm still gets working docs - with internet."""
    del unbuilt
    with client_for(fake_bc) as client:
        response = client.get('/docs')
    assert response.status_code == 200
    assert 'swagger-ui' in response.text


def test_redoc_is_not_served(fake_bc, built):
    """One documentation UI that works offline beats two that need a CDN."""
    del built
    with client_for(fake_bc) as client:
        assert client.get('/redoc').status_code in (404, 405)
