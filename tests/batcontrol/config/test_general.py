"""Tests for the top-level config checks (time_resolution_minutes)."""
import math

import pytest

from batcontrol.config import ALLOWED_TIME_RESOLUTIONS, parse_time_resolution


@pytest.mark.parametrize('value, expected', [
    (15, 15), (60, 60), ('15', 15), ('60', 60), (' 15 ', 15),
    ('\t60\n', 60), (15.0, 15), ('60.0', 60),
])
def test_valid_values_become_int(value, expected):
    result = parse_time_resolution(value)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize('value', [
    30, 0, -15, 15.5, '30', 'quarter', '', '   ', '1_5', 'nan', math.nan,
    math.inf, None, True, False, [15], {'minutes': 15},
])
def test_invalid_values_name_key_and_allowed_values(value):
    with pytest.raises(ValueError) as excinfo:
        parse_time_resolution(value)
    message = str(excinfo.value)
    assert message.startswith('time_resolution_minutes must be either 15')
    assert 'or 60 (hourly)' in message
    assert f'got {value!r}' in message


def test_allowed_values():
    assert ALLOWED_TIME_RESOLUTIONS == (15, 60)
