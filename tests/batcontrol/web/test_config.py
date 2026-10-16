"""Tests for parsing the api: configuration block."""
import pytest

from batcontrol.web import config as web_config


def test_missing_block_disables_the_api():
    """Every existing installation has no api: block and must stay unchanged."""
    parsed = web_config.from_config({})
    assert parsed.enabled is False
    assert parsed.host == '127.0.0.1'
    assert parsed.port == 8080
    assert parsed.auth_enabled is False


def test_defaults_of_an_enabled_block():
    parsed = web_config.from_config({'api': {'enabled': True}})
    assert parsed.enabled is True
    assert parsed.dashboard_enabled is True
    assert parsed.mcp_enabled is False


def test_home_assistant_string_values_are_accepted():
    """HA form fields deliver strings for booleans and numbers."""
    parsed = web_config.from_config({'api': {
        'enabled': 'true',
        'port': '8099',
        'dashboard_enabled': 'false',
        'mcp_enabled': 'TRUE',
    }})
    assert parsed.enabled is True
    assert parsed.port == 8099
    assert parsed.dashboard_enabled is False
    assert parsed.mcp_enabled is True


@pytest.mark.parametrize('port', [0, -1, 65536, 'http', [8080]])
def test_invalid_port_is_rejected(port):
    with pytest.raises(ValueError):
        web_config.from_config({'api': {'port': port}})


def test_explicit_null_port_falls_back_to_the_default():
    """A commented-out HA option arrives as None, not as a missing key."""
    assert web_config.from_config({'api': {'port': None}}).port == 8080


def test_invalid_boolean_is_rejected():
    with pytest.raises(ValueError) as excinfo:
        web_config.from_config({'api': {'enabled': 'maybe'}})
    assert 'api.enabled' in str(excinfo.value)


def test_an_empty_host_falls_back_to_the_default():
    """Changed deliberately: an empty string means "not configured".

    This used to raise. A Home Assistant form that submits a blank optional
    field produces exactly that, and the resulting ValueError aborted
    batcontrol at startup.
    """
    assert web_config.from_config({'api': {'host': '  '}}).host == '127.0.0.1'


def test_api_must_be_a_block():
    with pytest.raises(ValueError):
        web_config.from_config({'api': 'yes'})


def test_token_is_trimmed_and_enables_auth():
    parsed = web_config.from_config({'api': {'auth_token': '  s3cret  '}})
    assert parsed.auth_token == 's3cret'
    assert parsed.auth_enabled is True


def test_empty_token_means_no_auth():
    parsed = web_config.from_config({'api': {'auth_token': '   '}})
    assert parsed.auth_token == ''
    assert parsed.auth_enabled is False


def test_describe_never_leaks_the_token():
    parsed = web_config.from_config({'api': {
        'enabled': True, 'auth_token': 'super-secret-value'}})
    described = parsed.describe()
    assert 'super-secret-value' not in described
    assert 'auth: on' in described


def test_public_bind_without_token_warns(caplog):
    with caplog.at_level('WARNING'):
        web_config.from_config({'api': {'enabled': True, 'host': '0.0.0.0'}})
    assert 'auth_token' in caplog.text


def test_public_bind_with_token_does_not_warn(caplog):
    with caplog.at_level('WARNING'):
        web_config.from_config({'api': {
            'enabled': True, 'host': '0.0.0.0', 'auth_token': 'x'}})
    assert 'auth_token' not in caplog.text


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '::1', '[::1]'])
def test_no_public_bind_warning_for_a_loopback_address(host, caplog):
    with caplog.at_level('WARNING'):
        web_config.from_config({'api': {'enabled': True, 'host': host}})
    assert 'auth_token' not in caplog.text


@pytest.mark.parametrize('value, expected', [
    (None, False), (True, True), ('true', True), (False, False),
])
def test_docs_enabled(value, expected):
    block = {'enabled': True}
    if value is not None:
        block['docs_enabled'] = value
    assert web_config.from_config({'api': block}).docs_enabled is expected


def test_a_bracketed_ipv6_host_is_bindable():
    """Regression: '[::1]' passed validation but uvicorn cannot bind it."""
    config = web_config.from_config({'api': {'enabled': True, 'host': '[::1]'}})
    assert config.host == '::1'
    assert config.describe().startswith('http://[::1]:')


# -- values that mean "not configured" --------------------------------------

@pytest.mark.parametrize('block', [
    {'enabled': False, 'port': ''},
    {'enabled': False, 'host': ''},
    {'enabled': False, 'host': '   '},
    {'enabled': False, 'mcp_enabled': ''},
    {'enabled': False, 'dashboard_enabled': ''},
    {'enabled': '', 'port': 8080},
    {'enabled': 'true', 'host': '', 'port': '', 'auth_token': ''},
])
def test_an_empty_value_means_unset_not_invalid(block):
    """Home Assistant submits '' for an optional field left blank.

    Treating that as a value made a blank field in the add-on form abort
    batcontrol at startup - even with the API disabled, so the battery would
    have stopped being controlled because of a switched-off read-only
    accessory.
    """
    parsed = web_config.from_config({'api': block})
    assert parsed.host == '127.0.0.1'
    assert parsed.port == 8080


@pytest.mark.parametrize('block', [
    {'port': 'eighty'}, {'enabled': 'maybe'}, {'port': 99999}, {'port': 0},
])
def test_a_wrong_value_is_still_an_error(block):
    """Empty is not a value; wrong still is."""
    with pytest.raises(ValueError):
        web_config.from_config({'api': block})
