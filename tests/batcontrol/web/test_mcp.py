"""Tests for the read-only MCP server."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from batcontrol.web import mcp as mcp_module
from batcontrol.web.app import create_app
from batcontrol.web.config import ApiConfig
from batcontrol.web.service import BatcontrolService

from .conftest import MCP_HEADERS, MCP_INIT

EXPECTED_TOOLS = {
    'batcontrol_get_status',
    'batcontrol_get_battery',
    'batcontrol_get_current',
    'batcontrol_get_price_status',
    'batcontrol_get_price_forecast',
    'batcontrol_get_solar_forecast',
    'batcontrol_get_consumption_forecast',
    'batcontrol_get_net_consumption_forecast',
    'batcontrol_get_solar',
    'batcontrol_get_peak_shaving',
    'batcontrol_list_inverters',
    'batcontrol_get_inverter',
    'batcontrol_get_decision',
    'batcontrol_list_decisions',
    'batcontrol_get_config',
    'batcontrol_get_version',
}


@pytest.fixture(name='server')
def fixture_server(fake_bc):
    return mcp_module.build_server(BatcontrolService(fake_bc))


def definitions(server):
    return server._tool_manager.list_tools()  # pylint: disable=protected-access


def call(server, name, **arguments):
    """Call a tool and return the parsed structured result."""
    result = asyncio.run(server.call_tool(name, arguments))
    return result.structured_content


# -- tool surface -----------------------------------------------------------

def test_the_tool_set_is_exactly_the_read_tools(server):
    """An exact match: a new tool - a mutating one in particular - has to be
    added to EXPECTED_TOOLS deliberately, under the batcontrol_get/list names."""
    assert set(mcp_module.tool_names(server)) == EXPECTED_TOOLS


def test_every_tool_is_annotated_read_only(server):
    for definition in definitions(server):
        annotations = definition.annotations
        assert annotations is not None, definition.name
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.open_world_hint is False


def test_every_tool_has_a_description_and_an_output_schema(server):
    for definition in definitions(server):
        assert definition.description, definition.name
        # Units belong in the description - a model that guesses Wh vs W
        # answers confidently and wrongly.
        assert definition.output_schema, definition.name


def test_paged_tools_document_their_parameters(server):
    paged = [d for d in definitions(server) if 'forecast' in d.name]
    assert paged
    for definition in paged:
        for field in ('limit', 'offset'):
            spec = definition.parameters['properties'][field]
            assert spec.get('description'), f'{definition.name}.{field}'


def test_instructions_state_the_conventions(server):
    for needle in ('epoch millisecond', 'EUR/kWh', 'read-only', 'has_more'):
        assert needle.lower() in server.instructions.lower()


# -- payloads ---------------------------------------------------------------

def test_status_tool_returns_structured_data(server):
    result = call(server, 'batcontrol_get_status')
    assert result['available'] is True
    assert result['data']['mode_label'] == 'ALLOW DISCHARGE'


def test_forecast_respects_limit_and_reports_paging(server):
    result = call(server, 'batcontrol_get_price_forecast', limit=2)
    data = result['data']
    assert data['returned_count'] == 2
    assert data['total_count'] == 4
    assert data['has_more'] is True
    assert data['next_offset'] == 2


def test_forecast_offset_continues_the_window(server):
    first = call(server, 'batcontrol_get_price_forecast', limit=2)['data']
    rest = call(server, 'batcontrol_get_price_forecast',
                limit=2, offset=first['next_offset'])['data']
    assert rest['slots'][0]['slot'] == 2
    assert rest['has_more'] is False
    assert rest['next_offset'] is None


def test_out_of_range_limit_is_rejected_with_a_readable_error(server):
    """The input schema enforces the range, so the model can correct itself."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as excinfo:
        call(server, 'batcontrol_get_price_forecast', limit=10_000_000)
    assert 'less than or equal to' in str(excinfo.value)


def test_omitted_limit_falls_back_to_the_default_window(server):
    """None means "the next 24 hours", not "everything"."""
    result = call(server, 'batcontrol_get_price_forecast')
    assert result['available'] is True
    # the fixture horizon is shorter than 24 h, so everything fits
    assert result['data']['returned_count'] == 4


def test_net_consumption_keeps_the_surplus_sign(server):
    slots = call(server, 'batcontrol_get_net_consumption_forecast')['data']['slots']
    assert slots[2]['value'] == -500.0


def test_decision_history_is_paged(server, fake_bc):
    for _ in range(5):
        fake_bc.record_decision()
    assert call(server, 'batcontrol_list_decisions',
                limit=2)['data']['count'] == 2


def test_config_tool_exposes_no_credentials(server, fake_bc):
    fake_bc.config = {'inverter': {'password': 'inverter-pw'}}
    assert 'inverter-pw' not in str(call(server, 'batcontrol_get_config'))


# -- degradation ------------------------------------------------------------

def test_missing_data_is_reported_with_a_next_step(empty_bc):
    server = mcp_module.build_server(BatcontrolService(empty_bc))
    result = call(server, 'batcontrol_get_price_forecast')
    assert result['available'] is False
    assert result['data'] is None
    assert 'control cycle' in result['reason']


def test_missing_decision_is_reported_not_raised(server):
    result = call(server, 'batcontrol_get_decision')
    assert result['available'] is False
    assert 'No decision recorded yet' in result['reason']


def test_internal_errors_do_not_leak_details(fake_bc):
    def explode():
        raise RuntimeError('mqtt://user:hunter2@broker internal detail')

    service = BatcontrolService(fake_bc)
    service.status = explode
    server = mcp_module.build_server(service)
    result = call(server, 'batcontrol_get_status')
    assert result['available'] is False
    assert 'hunter2' not in result['reason']
    assert 'batcontrol log' in result['reason']


# -- HTTP transport ---------------------------------------------------------

def test_mcp_is_reachable_without_a_redirect(fake_bc):
    config = ApiConfig(enabled=True, mcp_enabled=True)
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        response = client.post('/mcp', json=MCP_INIT, headers=MCP_HEADERS,
                               follow_redirects=False)
    assert response.status_code == 200


def test_a_reverse_proxy_host_header_is_accepted_on_mcp(fake_bc):
    """Maintainer decision: no Host validation, also not the one the SDK
    switches on by itself for a loopback bind."""
    config = ApiConfig(enabled=True, mcp_enabled=True)
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        response = client.post(
            '/mcp', json=MCP_INIT,
            headers={**MCP_HEADERS, 'Host': 'batcontrol.example.org'})
    assert response.status_code == 200


def test_mcp_is_absent_when_disabled(client):
    schema = client.get('/openapi.json').json()
    assert '/mcp' not in schema['paths']
    # With the dashboard mounted at /, an unknown path is answered by
    # StaticFiles: 404 when it is not mounted, 405 when it is (it serves GET
    # only). Either way there is no MCP endpoint.
    assert client.post('/mcp', json=MCP_INIT,
                       headers=MCP_HEADERS).status_code in (404, 405)


# -- the two tokens ---------------------------------------------------------

def test_mcp_token_is_independent_of_the_api_token(fake_bc):
    config = ApiConfig(enabled=True, mcp_enabled=True,
                       auth_token='api-token', mcp_auth_token='mcp-token')
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        assert client.post('/mcp', json=MCP_INIT,
                           headers=MCP_HEADERS).status_code == 401
        assert client.post('/mcp', json=MCP_INIT, headers={
            **MCP_HEADERS,
            'Authorization': 'Bearer api-token'}).status_code == 401
        assert client.post('/mcp', json=MCP_INIT, headers={
            **MCP_HEADERS,
            'Authorization': 'Bearer mcp-token'}).status_code == 200
        # and the MCP token must not open the REST API
        assert client.get('/api/v1/status', headers={
            'Authorization': 'Bearer mcp-token'}).status_code == 401


def test_mcp_inherits_the_api_token_when_unset(fake_bc):
    """Securing the API must never leave MCP wide open."""
    config = ApiConfig(enabled=True, mcp_enabled=True, auth_token='shared')
    assert config.effective_mcp_token == 'shared'
    assert config.mcp_token_is_separate is False
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        assert client.post('/mcp', json=MCP_INIT,
                           headers=MCP_HEADERS).status_code == 401
        assert client.post('/mcp', json=MCP_INIT, headers={
            **MCP_HEADERS,
            'Authorization': 'Bearer shared'}).status_code == 200


def test_no_token_leaves_mcp_open(fake_bc):
    config = ApiConfig(enabled=True, mcp_enabled=True)
    with TestClient(create_app(fake_bc, config), base_url='http://127.0.0.1') as client:
        assert client.post('/mcp', json=MCP_INIT,
                           headers=MCP_HEADERS).status_code == 200


def test_a_failing_mcp_lifespan_does_not_take_rest_down(fake_bc, monkeypatch, caplog):
    """MCP is optional: if its session manager cannot start, REST and the
    dashboard must still come up."""
    import contextlib  # pylint: disable=import-outside-toplevel
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    from batcontrol.web import app as app_module  # pylint: disable=import-outside-toplevel

    @contextlib.asynccontextmanager
    async def exploding(_app):
        raise RuntimeError('session manager broke')
        yield  # pylint: disable=unreachable

    broken = SimpleNamespace(router=SimpleNamespace(lifespan_context=exploding))
    monkeypatch.setattr(app_module, '_build_mcp', lambda *_a: ([], broken))
    config = ApiConfig(enabled=True, mcp_enabled=True)
    with caplog.at_level('ERROR'):
        with TestClient(create_app(fake_bc, config),
                        base_url='http://127.0.0.1') as client:
            assert client.get('/api/v1/health').status_code == 200
    assert 'session manager broke' in caplog.text


def test_price_status_tool_returns_the_statistics(server):
    result = call(server, 'batcontrol_get_price_status')
    assert result['available'] is True
    assert result['data']['min']['value'] == 0.201
    assert result['data']['rank'] == 3


def test_solar_and_peak_shaving_tools_answer(server):
    assert call(server, 'batcontrol_get_solar')['data']['solar_active'] is False
    assert call(server, 'batcontrol_get_peak_shaving')['data'][
        'charge_limit_w'] == -1


def test_inverter_tools(server):
    listing = call(server, 'batcontrol_list_inverters')['data']
    inverter_id = listing['inverters'][0]['id']
    result = call(server, 'batcontrol_get_inverter', inverter_id=inverter_id)
    assert result['data']['soc'] == 87.4


def test_unknown_inverter_is_reported_not_raised(server):
    result = call(server, 'batcontrol_get_inverter', inverter_id=9)
    assert result['available'] is False
    assert 'inverters' in result['reason']
