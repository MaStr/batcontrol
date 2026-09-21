"""Tests for value parsing helpers in value_utils."""
import pytest

from batcontrol.value_utils import (
    parse_bool_flag,
    parse_optional_ratio,
    parse_positive_number,
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
