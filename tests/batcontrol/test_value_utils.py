"""Tests for value parsing helpers in value_utils."""
import pytest

from batcontrol.value_utils import (
    get_config_value,
    mask_secret,
    mask_sensitive_headers,
    redact_secrets,
    parse_bool_flag,
    parse_optional_ratio,
    parse_positive_number,
    reset_alias_warnings,
)


class TestParseBoolFlag:
    """parse_bool_flag() parses MQTT payloads for the grid-charge lock (issue #216)."""

    @pytest.mark.parametrize("payload,expected", [
        ("1", True), ("true", True), ("True", True), ("TRUE", True),
        (" 1 ", True), (" true ", True),
        ("0", False), ("false", False), ("False", False), ("FALSE", False),
        (" 0 ", False), (" false ", False),
    ])
    def test_valid_payloads(self, payload, expected):
        assert parse_bool_flag(payload) is expected

    @pytest.mark.parametrize("payload", ["", "maybe", "2", "yes", "on"])
    def test_invalid_payload_raises(self, payload):
        with pytest.raises(ValueError):
            parse_bool_flag(payload)


class TestParseOptionalRatio:
    """parse_optional_ratio() validates optional 0..1 config values."""

    def test_none_returns_none(self):
        assert parse_optional_ratio(None, 'key') is None

    @pytest.mark.parametrize("value,expected", [
        (0, 0.0), (1, 1.0), (0.5, 0.5), ("0.25", 0.25),
    ])
    def test_valid_values(self, value, expected):
        assert parse_optional_ratio(value, 'key') == expected

    @pytest.mark.parametrize("value", [-0.1, 1.1, 50, "2"])
    def test_out_of_range_raises(self, value):
        with pytest.raises(ValueError, match="between 0 and 1"):
            parse_optional_ratio(value, 'key')

    @pytest.mark.parametrize("value", ["abc", "", [0.5], {}])
    def test_non_numeric_raises(self, value):
        with pytest.raises(ValueError, match="key"):
            parse_optional_ratio(value, 'key')

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected(self, value):
        with pytest.raises(ValueError, match="bool"):
            parse_optional_ratio(value, 'key')

    def test_error_message_contains_config_key(self):
        with pytest.raises(ValueError, match="battery_control.min_grid_charge_soc"):
            parse_optional_ratio(2, 'battery_control.min_grid_charge_soc')


class TestParsePositiveNumber:
    """parse_positive_number() validates strictly positive, finite config values."""

    @pytest.mark.parametrize("value,expected", [
        (1, 1.0), (1.1, 1.1), ("2.5", 2.5), (0.001, 0.001), (1000, 1000.0),
    ])
    def test_valid_values(self, value, expected):
        assert parse_positive_number(value, 'key') == expected

    @pytest.mark.parametrize("value", [0, 0.0, -1, -0.5, "-3"])
    def test_zero_and_negative_raise(self, value):
        with pytest.raises(ValueError, match="positive number"):
            parse_positive_number(value, 'key')

    @pytest.mark.parametrize("value", [float('inf'), float('-inf'), float('nan')])
    def test_non_finite_raises(self, value):
        with pytest.raises(ValueError, match="positive number"):
            parse_positive_number(value, 'key')

    @pytest.mark.parametrize("value", [None, "abc", "", [1.0], {}])
    def test_non_numeric_and_none_raise(self, value):
        with pytest.raises(ValueError, match="key"):
            parse_positive_number(value, 'key')

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected(self, value):
        with pytest.raises(ValueError, match="bool"):
            parse_positive_number(value, 'key')

    def test_error_message_contains_config_key(self):
        with pytest.raises(ValueError, match="battery_control_expert.charge_rate_multiplier"):
            parse_positive_number(
                -1, 'battery_control_expert.charge_rate_multiplier')


class TestGetConfigValue:
    """get_config_value() accepts deprecated spellings of a config key (issue #451).

    batcontrol grew three spellings for a credential ('apikey', 'apitoken',
    'api') and two for an endpoint ('url', 'base_url'). A user who put the
    token into 'apikey' of a HomeAssistant installation got no error at all,
    just a broken auth message against HomeAssistant.
    """

    @pytest.fixture(autouse=True)
    def _clear_warnings(self):
        reset_alias_warnings()
        yield
        reset_alias_warnings()

    def test_canonical_key_wins(self):
        config = {'api_token': 'canonical', 'apikey': 'legacy'}
        assert get_config_value(config, 'api_token') == 'canonical'

    @pytest.mark.parametrize("alias", ['apikey', 'apitoken', 'api'])
    def test_token_aliases(self, alias):
        assert get_config_value({alias: 'legacy'}, 'api_token') == 'legacy'

    def test_url_alias(self):
        assert get_config_value(
            {'base_url': 'ws://ha:8123'}, 'url') == 'ws://ha:8123'

    @pytest.mark.parametrize("empty", [None, ''])
    def test_empty_canonical_falls_back_to_alias(self, empty):
        """The add-on delivers a cleared field as '' and an unset one as null."""
        config = {'api_token': empty, 'apikey': 'legacy'}
        assert get_config_value(config, 'api_token') == 'legacy'

    def test_zero_is_a_value(self):
        assert get_config_value({'cache_ttl_hours': 0}, 'cache_ttl_hours') == 0

    def test_default_when_nothing_is_set(self):
        assert get_config_value({}, 'api_token', 'fallback') == 'fallback'

    def test_unknown_key_has_no_aliases(self):
        assert get_config_value({'entity_id': 'sensor.x'}, 'entity_id') == 'sensor.x'
        assert get_config_value({'other': 'x'}, 'entity_id') is None

    def test_non_dict_config_returns_default(self):
        assert get_config_value(None, 'api_token', 'fallback') == 'fallback'
        assert get_config_value(['a'], 'api_token', 'fallback') == 'fallback'

    def test_alias_warns_once_per_context(self, caplog):
        config = {'apikey': 'legacy'}
        with caplog.at_level('WARNING'):
            for _ in range(3):
                get_config_value(config, 'api_token', None, 'pvinstallations[Haus]')
        warnings = [r for r in caplog.records if 'deprecated' in r.message]
        assert len(warnings) == 1
        assert 'pvinstallations[Haus]' in warnings[0].getMessage()
        assert 'api_token' in warnings[0].getMessage()

    def test_canonical_key_does_not_warn(self, caplog):
        with caplog.at_level('WARNING'):
            get_config_value({'api_token': 'x'}, 'api_token', None, 'utility')
        assert not [r for r in caplog.records if 'deprecated' in r.message]


class TestMaskSecret:
    """mask_secret() keeps a credential recognisable without logging it.

    Code scanning alert py/clear-text-logging-sensitive-data: the Fronius
    digest Authorization header is derived from the inverter password and was
    logged in full by the auth debug logger.
    """

    def test_keeps_first_and_last_three_characters(self):
        assert mask_secret('eyJhbGciOiJIUzI1NiJ9fQ') == 'eyJ...9fQ'

    def test_recognisable_but_not_reusable(self):
        token = 'A' * 40 + 'xyz'
        masked = mask_secret(token)
        assert masked.startswith('AAA')
        assert masked.endswith('xyz')
        assert token not in masked
        assert len(masked) == 9

    @pytest.mark.parametrize("secret", ['', None])
    def test_unset_and_empty(self, secret):
        assert mask_secret(secret) in ('<unset>', '<empty>')

    @pytest.mark.parametrize("secret", ['a', 'ab', 'abc', 'abcdef'])
    def test_short_secrets_are_masked_completely(self, secret):
        """Keeping both ends of a short secret would give it away"""
        masked = mask_secret(secret)
        assert masked == '*' * len(secret)
        assert secret not in masked

    def test_keep_is_configurable(self):
        assert mask_secret('0123456789', keep=2) == '01...89'

    def test_non_string_values(self):
        assert mask_secret(1234567890) == '123...890'


class TestMaskSensitiveHeaders:
    """Only credential headers are shortened, the rest stays readable"""

    def test_authorization_is_masked(self):
        digest = ('Digest username="customer", realm="Webinterface area", '
                  'nonce="abc", response="0123456789abcdef"')

        masked = mask_sensitive_headers({'Authorization': digest})

        assert masked['Authorization'] == 'Dig...ef"'
        assert 'response="0123456789abcdef"' not in masked['Authorization']

    def test_other_headers_are_untouched(self):
        headers = {'Content-Type': 'application/json', 'Accept': '*/*'}

        assert mask_sensitive_headers(headers) == headers

    @pytest.mark.parametrize("name", [
        'Authorization', 'authorization', 'AUTHORIZATION',
        'Proxy-Authorization', 'Cookie', 'Set-Cookie', 'X-Api-Key',
        'api-key', 'X-Auth-Token', 'x-access-token',
    ])
    def test_sensitive_header_names(self, name):
        masked = mask_sensitive_headers({name: 'secretvalue123'})
        assert masked[name] == 'sec...123'

    def test_original_mapping_is_not_modified(self):
        headers = {'Authorization': 'secretvalue123'}

        mask_sensitive_headers(headers)

        assert headers['Authorization'] == 'secretvalue123'

    @pytest.mark.parametrize("headers", [None, {}])
    def test_empty_headers(self, headers):
        assert mask_sensitive_headers(headers) == {}


class TestRedactSecrets:
    """redact_secrets() keeps a logged message readable but secret-free.

    forecast.solar takes the API key as the first path segment of the request
    URL, so an error body or header echoing that path would leak it
    (code scanning py/clear-text-logging-sensitive-data).
    """

    def test_secret_is_replaced_by_its_masked_form(self):
        text = 'Forbidden for /secret-key-1234567890/estimate'

        result = redact_secrets(text, 'secret-key-1234567890')

        assert result == 'Forbidden for /sec...890/estimate'

    def test_message_stays_readable(self):
        text = '{"message":{"code":429,"text":"rate limit exceeded"}}'

        result = redact_secrets(text, 'secret-key-1234567890')

        assert result == text

    def test_every_occurrence_is_replaced(self):
        result = redact_secrets('abcdefghij and abcdefghij', 'abcdefghij')

        assert 'abcdefghij' not in result
        assert result.count('abc...hij') == 2

    def test_several_secrets(self):
        result = redact_secrets(
            'key=aaaaaaaaaa token=bbbbbbbbbb', 'aaaaaaaaaa', 'bbbbbbbbbb')

        assert result == 'key=aaa...aaa token=bbb...bbb'

    @pytest.mark.parametrize("secret", [None, ''])
    def test_unset_secrets_are_ignored(self, secret):
        assert redact_secrets('nothing to hide', secret) == 'nothing to hide'

    def test_no_secret_given(self):
        assert redact_secrets('nothing to hide') == 'nothing to hide'

    def test_non_string_input(self):
        assert redact_secrets(429, 'aaaaaaaaaa') == '429'
