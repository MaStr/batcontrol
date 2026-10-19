"""The OpenAPI contract.

The schema is what external consumers generate clients from, so a change to
it is a change to a published contract. This test spells out the expected
surface: adding an endpoint means adding it to conftest.API_PATHS
deliberately, and removing or renaming one cannot slip through unnoticed.
"""
from .conftest import API_PATHS

EXPECTED_TAGS = {'meta', 'status', 'price', 'forecast', 'decision',
                 'inverter', 'config'}


def schema(client):
    response = client.get('/openapi.json')
    assert response.status_code == 200
    return response.json()


def test_paths_match_the_documented_contract(client):
    assert set(schema(client)['paths']) == set(API_PATHS)


def test_every_endpoint_is_tagged(client):
    for path, operations in schema(client)['paths'].items():
        for method, operation in operations.items():
            assert operation.get('tags'), f'{method} {path} has no tag'
            assert set(operation['tags']) <= EXPECTED_TAGS


def test_every_endpoint_has_a_summary(client):
    for path, operations in schema(client)['paths'].items():
        for method, operation in operations.items():
            assert operation.get('summary'), f'{method} {path} has no summary'


def test_every_response_is_enveloped(client):
    """ok/timestamp/data is part of the contract, not a coincidence."""
    components = schema(client)['components']['schemas']
    envelopes = [name for name in components if name.startswith('Envelope_')]
    assert envelopes
    for name in envelopes:
        assert set(components[name]['properties']) == {
            'ok', 'timestamp', 'data'}


def test_units_are_documented_on_numeric_fields(client):
    """A bare numeric field without a unit has confused consumers before."""
    components = schema(client)['components']['schemas']
    for model in ('ForecastSlot', 'Battery', 'CurrentPrice', 'PricePoint',
                  'PriceStatus', 'SolarMetrics', 'PeakShavingStatus',
                  'Inverter', 'ConfigOverride'):
        for field, spec in components[model]['properties'].items():
            assert spec.get('description'), f'{model}.{field} is undocumented'


def test_forecast_slot_documents_the_power_pitfall(client):
    """Wh per slot vs average W differ by 4x at 15-minute resolution."""
    power = schema(client)['components']['schemas']['ForecastSlot'][
        'properties']['power_w']
    assert '15-minute' in power['description']
