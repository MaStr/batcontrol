"""Tests for the optional bearer token.

The token is opt-in. With it configured, every endpoint but /health requires
it; without it, nothing is protected and no dependency is installed at all.
"""
import contextlib

import pytest
from fastapi.testclient import TestClient

from batcontrol.web.app import create_app
from batcontrol.web.config import ApiConfig

from .conftest import API_PATHS, MCP_HEADERS, MCP_INIT

TOKEN = 'a-very-secret-token'

PROTECTED = [url for url in API_PATHS.values() if url != '/api/v1/health']


@pytest.fixture(name='secured')
def fixture_secured(fake_bc):
    # docs_enabled, so the token check below also covers the schema.
    config = ApiConfig(enabled=True, auth_token=TOKEN, docs_enabled=True)
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        yield client


def test_health_stays_open(secured):
    """Container health checks must not need a token."""
    assert secured.get('/api/v1/health').status_code == 200


@pytest.mark.parametrize('path', PROTECTED)
def test_missing_token_is_401(secured, path):
    response = secured.get(path)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


@pytest.mark.parametrize('header', [
    'Bearer wrong-token',
    'Bearer ',
    'Basic a-very-secret-token',
    'a-very-secret-token',
])
def test_wrong_credentials_are_401(secured, header):
    response = secured.get('/api/v1/status',
                           headers={'Authorization': header})
    assert response.status_code == 401


def test_correct_token_is_accepted(secured):
    response = secured.get('/api/v1/status',
                           headers={'Authorization': f'Bearer {TOKEN}'})
    assert response.status_code == 200


def test_scheme_is_case_insensitive(secured):
    response = secured.get('/api/v1/status',
                           headers={'Authorization': f'bearer {TOKEN}'})
    assert response.status_code == 200


def test_without_a_token_nothing_is_protected(client):
    for path in PROTECTED:
        assert client.get(path).status_code in (200, 404), path


def test_no_dependency_is_installed_without_a_token():
    """Not just "allows everything" - the check must not exist at all."""
    from batcontrol.web.auth import auth_dependencies
    assert auth_dependencies(ApiConfig()) == []
    assert auth_dependencies(ApiConfig(auth_token='x'))


def test_the_token_never_appears_in_a_response(secured):
    for path in ['/api/v1/health', '/openapi.json']:
        assert TOKEN not in secured.get(path).text


def test_the_token_is_never_logged(fake_bc, caplog):
    with caplog.at_level('DEBUG'):
        config = ApiConfig(enabled=True, auth_token=TOKEN)
        with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
            client.get('/api/v1/status')
            client.get('/api/v1/status',
                       headers={'Authorization': f'Bearer {TOKEN}'})
    assert TOKEN not in caplog.text


# -- non-ASCII tokens -------------------------------------------------------

# Sent as raw bytes on purpose: a well-behaved client refuses to put a
# non-ASCII string into a header, so this is what actually reaches the server
# from curl or a hand-rolled socket - the case the old code crashed on.
NON_ASCII = (
    b'Bearer \xc3\xbc',                      # "u" with umlaut, UTF-8
    b'Bearer gr\xc3\xbcn-schl\xc3\xbcssel',  # a plausible German token
    b'Bearer \xe2\x9c\x93',                   # check mark
    b'Bearer \xf0\x9f\x94\x92',               # lock emoji
    b'Bearer \xff\xfe\x00',                   # not valid UTF-8 at all
    b'\xc3\xbc',                             # no scheme at all
)


@pytest.fixture(name='guarded', params=['rest', 'mcp'])
def fixture_guarded(request, fake_bc):
    """Opens an app whose REST API or MCP endpoint requires `token`.

    /mcp has its own ASGI bearer guard, so every token check has to hold in
    two places. Returns a function that sends one authenticated request.
    """
    with contextlib.ExitStack() as stack:
        def open_with(token):
            if request.param == 'rest':
                config = ApiConfig(enabled=True, auth_token=token)
            else:
                config = ApiConfig(enabled=True, mcp_enabled=True,
                                   mcp_auth_token=token)
            client = stack.enter_context(TestClient(
                create_app(fake_bc, config), base_url='http://127.0.0.1'))

            def send(header=None):
                headers = {} if header is None else {'Authorization': header}
                if request.param == 'rest':
                    return client.get('/api/v1/status', headers=headers)
                return client.post('/mcp', json=MCP_INIT,
                                   headers={**MCP_HEADERS, **headers})
            return send
        yield open_with


@pytest.mark.parametrize('header', NON_ASCII)
def test_a_non_ascii_token_is_rejected_not_crashed(guarded, header):
    """Regression: secrets.compare_digest raises on non-ASCII strings.

    Comparing the presented token as a str turned a bearer token with an
    umlaut in it into a 500 with a traceback instead of a plain 401. It failed
    closed either way, but an authentication check must reject, not crash.
    """
    response = guarded(TOKEN)(header)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


def test_a_non_ascii_token_can_be_configured_and_used(guarded):
    """And if someone does configure one, it has to actually work."""
    token = 'gr\u00fcn-schl\u00fcssel-\u2713'
    send = guarded(token)
    assert send().status_code == 401
    assert send(b'Bearer ' + token.encode('utf-8')).status_code == 200


def test_token_matching_is_byte_exact():
    """No decode step may normalise a wrong token into a match."""
    from batcontrol.web.config import token_matches

    assert token_matches('secret', 'secret') is True
    assert token_matches(b'secret', 'secret') is True
    assert token_matches('secret ', 'secret') is False
    assert token_matches('Secret', 'secret') is False
    assert token_matches('', 'secret') is False
    assert token_matches(None, 'secret') is False
    assert token_matches('secret', '') is False
    # a non-ASCII value on either side returns a verdict instead of raising
    assert token_matches(b'\xc3\xbc', 'secret') is False
    assert token_matches('secret', '\u00fc') is False
    assert token_matches('\u00fc'.encode('utf-8'), '\u00fc') is True
