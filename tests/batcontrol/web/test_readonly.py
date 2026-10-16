"""The read-only invariant of the API.

These tests are the guard rail for a deliberate design decision: the HTTP API
reads batcontrol, it never steers it. Runtime overrides stay on the MQTT API.
If a future change adds a mutating route, this file fails - that is intended,
do not relax it without the maintainer's decision.

The checks go through the OpenAPI schema and real requests rather than
FastAPI's route objects, so they keep working across FastAPI releases that
reorganise internals.
"""
import pytest

READ_METHODS = {'GET', 'HEAD', 'OPTIONS'}
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')


def documented_paths(client):
    """Every path the API advertises, with its methods."""
    return client.get('/openapi.json').json()['paths']


def test_openapi_declares_no_write_operations(client):
    write_ops = {
        f'{method.upper()} {path}'
        for path, operations in documented_paths(client).items()
        for method in operations
        if method.upper() not in READ_METHODS
    }
    assert not write_ops, f'OpenAPI advertises write operations: {write_ops}'


def test_the_api_actually_has_endpoints(client):
    """Guards against the test above passing on an empty application."""
    paths = documented_paths(client)
    assert len(paths) >= 2, f'suspiciously few endpoints: {sorted(paths)}'


@pytest.mark.parametrize('method', WRITE_METHODS)
def test_every_endpoint_rejects_write_methods(client, method):
    for path in documented_paths(client):
        response = client.request(method, path)
        assert response.status_code == 405, (
            f'{method} {path} returned {response.status_code},'
            ' the API must not accept write methods'
        )


def test_service_layer_exposes_no_setters():
    """The shared service layer is the only data path; keep it read-only."""
    from batcontrol.web.service import BatcontrolService

    mutators = [
        name for name in dir(BatcontrolService)
        if not name.startswith('_')
        and name.split('_')[0] in ('set', 'put', 'update', 'apply', 'clear',
                                   'write', 'delete', 'force')
    ]
    assert not mutators, f'service layer gained mutating methods: {mutators}'
