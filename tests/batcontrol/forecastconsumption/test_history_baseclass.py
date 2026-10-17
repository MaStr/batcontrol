"""Tests for the source independent history forecast baseclass"""

import contextlib
import datetime
from unittest.mock import patch

import pytest
import pytz

from src.batcontrol.forecastconsumption.history_baseclass import (
    ForecastConsumptionHistoryBaseclass
)


class FakeHistory(ForecastConsumptionHistoryBaseclass):
    """Subclass with scripted slot values"""

    def __init__(self, timezone, values=None, **kwargs):
        super().__init__(timezone, **kwargs)
        self.values = values if values is not None else {}
        self.sessions = []
        self.requests = []

    @contextlib.contextmanager
    def _history_session(self, earliest_start, latest_end):
        self.sessions.append((earliest_start, latest_end))
        yield

    def _fetch_slot_value(self, start_time, end_time):
        self.requests.append((start_time, end_time))
        value = self.values(start_time, end_time) if callable(
            self.values) else self.values.get(start_time, -1)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def timezone():
    return pytz.timezone('Europe/Berlin')


@pytest.fixture
def now(timezone):
    return datetime.datetime(2024, 3, 5, 10, 0, tzinfo=timezone)


def test_weighted_slot_average(timezone):
    fc = FakeHistory(timezone, history_days=[-7, -14, -21],
                     history_weights=[3, 2, 1])
    slots = fc._get_reference_slots()
    assert fc._weighted_slot_average(
        {-7: 600.0, -14: 300.0, -21: 0.0}, slots) == pytest.approx(400.0)
    # missing day drops out of the average
    assert fc._weighted_slot_average(
        {-7: 600.0, -21: 0.0}, slots) == pytest.approx(450.0)
    assert fc._weighted_slot_average({}, slots) is None


def test_missing_day_averages_remaining_days(timezone, now):
    fc = FakeHistory(
        timezone,
        values=lambda start, end: -1 if start.day == 26 else 100.0)
    history = {}
    fc._collect_history_periods([0], now, fc._get_reference_slots(), history)
    assert history == {0: 100.0}
    assert len(fc.requests) == 3


def test_slot_without_data_ends_collect(timezone, now):
    fc = FakeHistory(timezone, values=lambda start, end: -1)
    history = {}
    fc._collect_history_periods(
        [0, 1, 2], now, fc._get_reference_slots(), history)
    assert not history
    assert len(fc.requests) == 3  # only hour 0, then break


def test_request_error_continues(timezone, now):
    def values(start, end):
        return RuntimeError("boom") if start.day == 26 else 200.0

    fc = FakeHistory(timezone, values=values)
    history = {}
    fc._collect_history_periods([0], now, fc._get_reference_slots(), history)
    assert history == {0: 200.0}


def test_session_opened_once_with_range(timezone):
    fc = FakeHistory(timezone, values=lambda start, end: 50.0, multiplier=2.0)
    fc.refresh_data_with_limit(3)
    assert len(fc.sessions) == 1
    earliest, latest = fc.sessions[0]
    assert earliest < latest
    assert len(fc.requests) == 9
    # multiplier is applied when the cache is filled
    assert set(fc.consumption_cache.values()) == {100.0}
    assert len(fc.consumption_cache) == 3


def test_total_failure_leaves_cache_empty(timezone):
    fc = FakeHistory(timezone, values=lambda start, end: RuntimeError("down"))
    fc.refresh_data_with_limit(3)
    assert len(fc.consumption_cache) == 0
    with pytest.raises(RuntimeError):
        fc._get_forecast_native(3)


def test_session_error_is_logged_not_raised(timezone):
    fc = FakeHistory(timezone)

    @contextlib.contextmanager
    def broken(_start, _end):
        raise ConnectionError("no route")
        yield  # pylint: disable=unreachable

    with patch.object(fc, '_history_session', broken):
        fc.refresh_data_with_limit(2)
    assert len(fc.consumption_cache) == 0
