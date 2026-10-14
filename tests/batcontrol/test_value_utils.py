"""Tests for value parsing helpers in value_utils."""
import pytest

from batcontrol.value_utils import (
    get_config_value,
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
