"""Tests for the evcc-home consumption forecast (group home of evcc)"""

import datetime
import logging
import math
from unittest.mock import MagicMock, patch

import pytest
import pytz
import requests

from src.batcontrol.forecastconsumption.forecast_evcc_home import (
    ForecastConsumptionEvccHome
)

GET = 'src.batcontrol.forecastconsumption.forecast_evcc_home.requests.get'
UTC = datetime.timezone.utc
BERLIN = pytz.timezone('Europe/Berlin')
WEEK = 7 * 24 * 3600


def _response(status=200, payload=None, text='', json_error=False):
    """Mocked requests response"""
    response = MagicMock()
    response.status_code = status
    response.text = text
    if json_error:
        response.json.side_effect = ValueError('Expecting value')
    else:
        response.json.return_value = payload
    return response


def _slots(iso_start, count, kwh):
    """count 15 minute slots from iso_start (offset is kept), kwh each"""
    start = datetime.datetime.fromisoformat(iso_start)
    result = []
    for i in range(count):
        slot_start = start + datetime.timedelta(minutes=15 * i)
        result.append({
            'start': slot_start.isoformat(),
            'end': (slot_start + datetime.timedelta(minutes=15)).isoformat(),
            'energy': kwh,
        })
    return result


def _series(slots, group='home'):
    return {'group': group, 'data': slots}


def _forecaster(timezone=BERLIN, **kwargs):
    return ForecastConsumptionEvccHome(
        'http://evcc.local:7070', timezone, **kwargs)


def _utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def _hour_value(forecaster, mock_get, payload, start, end, query_hour):
    """Load payload in a session, return the value of query_hour"""
    mock_get.return_value = _response(payload=payload)
    with forecaster._history_session(start, end):  # pylint: disable=protected-access
        return forecaster._fetch_slot_value(  # pylint: disable=protected-access
            query_hour, query_hour + datetime.timedelta(hours=1))


def _fake_server(value_fn, server_tz=BERLIN):
    """requests.get replacement which delivers slots for the asked range

    value_fn(quarter_epoch) returns Wh of the hour or None to leave the slot out.
    """
    def fake_get(url, params=None, timeout=None):  # pylint: disable=unused-argument
        current = datetime.datetime.fromisoformat(params['from'])
        end = datetime.datetime.fromisoformat(params['to'])
        slots = []
        while current < end:
            wh_hour = value_fn(int(current.timestamp()))
            if wh_hour is not None:
                slots.append({
                    'start': current.astimezone(server_tz).isoformat(),
                    'end': (current + datetime.timedelta(minutes=15))
                    .astimezone(server_tz).isoformat(),
                    'energy': wh_hour / 4000.0,
                })
            current += datetime.timedelta(minutes=15)
        return _response(payload=[_series(slots)])
    return fake_get


def _weeks_ago_value(base, per_week=1000.0, skip=None):
    """Wh per hour = per_week * weeks between the hour and base (now)"""
    def value_fn(quarter_epoch):
        hour = quarter_epoch // 3600 * 3600
        weeks = max(1, math.ceil((base - hour) / WEEK))
        offset_hours = ((hour - base) % WEEK) // 3600
        if skip and skip(weeks, offset_hours):
            return None
        return per_week * weeks
    return value_fn


def _now_hour_epoch():
    return int(datetime.datetime.now(tz=UTC).replace(
        minute=0, second=0, microsecond=0).timestamp())


class TestUrl:
    """URL validation and normalisation"""

    @pytest.mark.parametrize('url,expected', [
        ('http://evcc.local:7070', 'http://evcc.local:7070'),
        ('http://evcc.local:7070/', 'http://evcc.local:7070'),
        ('http://evcc.local:7070/api', 'http://evcc.local:7070'),
        ('http://evcc.local:7070/api/', 'http://evcc.local:7070'),
        ('  https://evcc.example/evcc/api  ', 'https://evcc.example/evcc'),
        ('HTTP://evcc:7070', 'HTTP://evcc:7070'),
    ])
    def test_normalized(self, url, expected):
        assert ForecastConsumptionEvccHome(url, BERLIN).base_url == expected

    @pytest.mark.parametrize('url', ['ws://evcc:7070', 'wss://evcc:7070'])
    def test_websocket_url_rejected(self, url):
        with pytest.raises(ValueError, match='http'):
            ForecastConsumptionEvccHome(url, BERLIN)

    @pytest.mark.parametrize('url', ['evcc.local:7070', '', None, '   '])
    def test_missing_scheme_rejected(self, url):
        with pytest.raises(ValueError, match='http'):
            ForecastConsumptionEvccHome(url, BERLIN)

    def test_history_validation_is_shared(self):
        with pytest.raises(ValueError, match='must match'):
            _forecaster(history_days=[-7, -14], history_weights=[1])
        with pytest.raises(ValueError, match='between 1 and 10'):
            _forecaster(history_days=[-7], history_weights=[11])


class TestRequestAndSum:
    """The request and the sum of the quarter hours"""

    def test_request_parameters_and_hour_sum(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 12))
        payload = [_series(_slots('2025-03-05T10:00:00+01:00', 8, 0.1))]

        with patch(GET) as mock_get:
            first = _hour_value(forecaster, mock_get, payload, start, end,
                                _utc(2025, 3, 5, 9))
            second = _hour_value(forecaster, mock_get, payload, start, end,
                                 _utc(2025, 3, 5, 10))
            after = _hour_value(forecaster, mock_get, payload, start, end,
                                _utc(2025, 3, 5, 11))

        # 4 x 0.1 kWh = 400 Wh
        assert first == pytest.approx(400.0)
        assert second == pytest.approx(400.0)
        assert after == -1

        args, kwargs = mock_get.call_args
        assert args[0] == 'http://evcc.local:7070/api/history/energy'
        assert kwargs['params'] == {
            'group': 'home',
            'aggregate': '15m',
            'format': 'json',
            'from': '2025-03-05T09:00:00Z',
            'to': '2025-03-05T11:00:00Z',
        }
        assert kwargs['timeout'] == 30

    def test_incomplete_hour_is_gap(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 12))
        slots = _slots('2025-03-05T10:00:00+01:00', 8, 0.1)
        del slots[1]    # 10:15 missing
        with patch(GET) as mock_get:
            incomplete = _hour_value(
                forecaster, mock_get, [_series(slots)], start, end,
                _utc(2025, 3, 5, 9))
            complete = _hour_value(
                forecaster, mock_get, [_series(slots)], start, end,
                _utc(2025, 3, 5, 10))
        assert incomplete == -1
        assert complete == pytest.approx(400.0)

    def test_only_group_home_and_several_series_are_summed(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 11))
        payload = [
            _series(_slots('2025-03-05T10:00:00+01:00', 4, 0.1)),
            _series(_slots('2025-03-05T10:00:00+01:00', 4, 0.05)),
            _series(_slots('2025-03-05T10:00:00+01:00', 4, 9.0), group='pv'),
            _series(_slots('2025-03-05T10:00:00+01:00', 4, 9.0), group='grid'),
        ]
        with patch(GET) as mock_get:
            value = _hour_value(forecaster, mock_get, payload, start, end,
                                _utc(2025, 3, 5, 9))
        assert value == pytest.approx(600.0)

    @pytest.mark.parametrize('bad', [None, -0.5, 'abc', True, float('nan')])
    def test_unusable_energy_makes_hour_a_gap(self, bad):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 12))
        slots = _slots('2025-03-05T10:00:00+01:00', 8, 0.1)
        slots[2]['energy'] = bad
        with patch(GET) as mock_get:
            damaged = _hour_value(
                forecaster, mock_get, [_series(slots)], start, end,
                _utc(2025, 3, 5, 9))
            fine = _hour_value(
                forecaster, mock_get, [_series(slots)], start, end,
                _utc(2025, 3, 5, 10))
        assert damaged == -1
        assert fine == pytest.approx(400.0)

    def test_zero_energy_is_a_value(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 11))
        payload = [_series(_slots('2025-03-05T10:00:00+01:00', 4, 0.0))]
        with patch(GET) as mock_get:
            value = _hour_value(forecaster, mock_get, payload, start, end,
                                _utc(2025, 3, 5, 9))
        assert value == 0

    def test_slot_without_time_zone_or_alignment_is_skipped(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 11))
        slots = _slots('2025-03-05T10:00:00+01:00', 4, 0.1)
        slots[0]['start'] = '2025-03-05T10:00:00'
        slots[1]['start'] = 'garbage'
        slots[2]['start'] = '2025-03-05T10:31:00+01:00'
        with patch(GET) as mock_get:
            value = _hour_value(forecaster, mock_get, [_series(slots)],
                                start, end, _utc(2025, 3, 5, 9))
        assert value == -1

    def test_no_completed_hour_asks_nothing(self):
        forecaster = _forecaster()
        future = datetime.datetime.now(tz=UTC) + datetime.timedelta(days=2)
        with patch(GET) as mock_get:
            with forecaster._history_session(  # pylint: disable=protected-access
                    future, future + datetime.timedelta(hours=1)):
                value = forecaster._fetch_slot_value(  # pylint: disable=protected-access
                    future, future)
        assert value == -1
        mock_get.assert_not_called()

    def test_fetch_outside_session_raises(self):
        forecaster = _forecaster()
        now = datetime.datetime.now(tz=UTC)
        with pytest.raises(RuntimeError, match='not loaded'):
            forecaster._fetch_slot_value(
                now, now)  # pylint: disable=protected-access

    def test_session_resets_lookup(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 11))
        payload = [_series(_slots('2025-03-05T10:00:00+01:00', 4, 0.1))]
        with patch(GET) as mock_get:
            _hour_value(forecaster, mock_get, payload, start, end,
                        _utc(2025, 3, 5, 9))
        assert forecaster._hourly_wh is None  # pylint: disable=protected-access


class TestDaylightSaving:
    """Time zone and daylight saving time"""

    def test_spring_offsets_in_one_lookup(self):
        """2025-03-30: 02:00 CET jumps to 03:00 CEST, the wall hour 02 is missing"""
        forecaster = _forecaster()
        slots = (_slots('2025-03-30T00:00:00+01:00', 8, 0.1)
                 + _slots('2025-03-30T03:00:00+02:00', 8, 0.2))
        start = _utc(2025, 3, 29, 23)
        end = _utc(2025, 3, 30, 3)
        with patch(GET) as mock_get:
            mock_get.return_value = _response(payload=[_series(slots)])
            with forecaster._history_session(start, end):  # pylint: disable=protected-access
                values = [
                    forecaster._fetch_slot_value(  # pylint: disable=protected-access
                        _utc(2025, 3, 29, 23) + datetime.timedelta(hours=i),
                        end)
                    for i in range(4)]
        # 23:00Z 00:00Z (CET), 01:00Z 02:00Z (CEST), no hour in between lost
        assert values == [pytest.approx(400.0), pytest.approx(400.0),
                          pytest.approx(800.0), pytest.approx(800.0)]

    def test_autumn_repeated_hour_is_gap(self):
        """2025-10-26: 03:00 CEST goes back to 02:00 CET, the hour 02 occurs twice

        evcc groups by the local time text: the four slots of the repeated hour
        carry the energy of two hours and start in CEST (+02:00).
        """
        forecaster = _forecaster()
        slots = (_slots('2025-10-26T01:00:00+02:00', 4, 0.1)
                 + _slots('2025-10-26T02:00:00+02:00', 4, 0.2)
                 + _slots('2025-10-26T03:00:00+01:00', 4, 0.1))
        start = _utc(2025, 10, 25, 23)
        end = _utc(2025, 10, 26, 3)
        with patch(GET) as mock_get:
            mock_get.return_value = _response(payload=[_series(slots)])
            with forecaster._history_session(start, end):  # pylint: disable=protected-access
                values = {
                    hour: forecaster._fetch_slot_value(  # pylint: disable=protected-access
                        _utc(2025, 10, 25, 23) +
                        datetime.timedelta(hours=hour),
                        end)
                    for hour in range(4)}
        assert values[0] == pytest.approx(400.0)   # 01:00 CEST
        assert values[1] == -1                     # 02:00 CEST, merged
        assert values[2] == -1                     # 02:00 CET, not delivered
        assert values[3] == pytest.approx(400.0)   # 03:00 CET

    def test_evcc_server_in_utc(self):
        forecaster = _forecaster()
        start = BERLIN.localize(datetime.datetime(2025, 3, 5, 10))
        end = BERLIN.localize(datetime.datetime(2025, 3, 5, 11))
        # 10:00 Berlin (CET) is 09:00 UTC
        payload = [_series(_slots('2025-03-05T09:00:00Z', 4, 0.25))]
        with patch(GET) as mock_get:
            value = _hour_value(forecaster, mock_get, payload, start, end,
                                BERLIN.localize(datetime.datetime(2025, 3, 5, 10)))
        assert value == pytest.approx(1000.0)

    @pytest.mark.parametrize('zone', ['pytz', 'zoneinfo'])
    def test_is_ambiguous(self, zone):
        if zone == 'pytz':
            timezone = BERLIN
        else:
            zoneinfo = pytest.importorskip('zoneinfo')
            try:
                timezone = zoneinfo.ZoneInfo('Europe/Berlin')
            except zoneinfo.ZoneInfoNotFoundError:
                pytest.skip('no tz database')
        ambiguous = ForecastConsumptionEvccHome._is_ambiguous  # pylint: disable=protected-access

        def epoch(*args):
            return int(_utc(*args).timestamp())

        assert ambiguous(epoch(2025, 10, 26, 0, 15),
                         timezone)      # 02:15 CEST
        assert ambiguous(epoch(2025, 10, 26, 1, 15), timezone)      # 02:15 CET
        assert not ambiguous(epoch(2025, 10, 25, 23, 45),
                             timezone)  # 01:45 CEST
        assert not ambiguous(epoch(2025, 10, 26, 2, 0),
                             timezone)    # 03:00 CET
        assert not ambiguous(epoch(2025, 3, 30, 1, 0),
                             timezone)     # 03:00 CEST
        assert not ambiguous(epoch(2025, 7, 1, 10, 0), timezone)


class TestErrors:
    """Errors give a readable message, an unchanged cache and no exception"""

    CASES = [
        ('timeout', {'side_effect': requests.exceptions.Timeout('slow')},
         'timed out'),
        ('connection', {'side_effect': requests.exceptions.ConnectionError('no route')},
         'not reachable'),
        ('request', {'side_effect': requests.exceptions.RequestException('odd')},
         'request failed'),
        ('404', {'return_value': _response(status=404)}, '>= 0.311.0'),
        ('400', {'return_value': _response(status=400, text='database offline')},
         'database offline'),
        ('500', {'return_value': _response(status=500, text='invalid aggregate')},
         'HTTP 500'),
        ('no_json', {'return_value': _response(
            json_error=True)}, 'valid JSON'),
        ('null', {'return_value': _response(payload=None)}, "group 'home'"),
        ('empty', {'return_value': _response(payload=[])}, "group 'home'"),
        ('no_home', {'return_value': _response(
            payload=[_series(_slots('2025-03-05T10:00:00+01:00', 4, 1.0), group='pv')])},
         "group 'home'"),
        ('empty_data', {'return_value': _response(payload=[_series([])])},
         "group 'home'"),
        ('not_a_list', {'return_value': _response(payload={'error': 'x'})},
         "group 'home'"),
    ]

    @pytest.mark.parametrize('name,mock_args,message',
                             CASES, ids=[c[0] for c in CASES])
    def test_error(self, name, mock_args, message, caplog):  # pylint: disable=unused-argument
        forecaster = _forecaster()
        with caplog.at_level(logging.DEBUG), patch(GET, **mock_args):
            forecaster.refresh_data_with_limit(4)

        assert len(forecaster.consumption_cache) == 0
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(message in r.getMessage() for r in errors)
        assert all(r.exc_info is None for r in errors)

    def test_get_forecast_raises_like_homeassistant(self):
        forecaster = _forecaster()
        with patch(GET, side_effect=requests.exceptions.Timeout('slow')):
            with pytest.raises(RuntimeError,
                               match='No consumption forecast data'):
                forecaster.get_forecast(4)

    def test_error_keeps_existing_cache(self):
        forecaster = _forecaster()
        now = datetime.datetime.now(tz=BERLIN)
        key = forecaster._get_cache_key(  # pylint: disable=protected-access
            now.weekday(), now.hour)
        forecaster.consumption_cache[key] = 123.0
        with patch(GET, side_effect=requests.exceptions.Timeout('slow')):
            forecaster.refresh_data_with_limit(4)
        assert forecaster.consumption_cache[key] == 123.0


class TestForecast:
    """Profile building against a fake evcc"""

    def test_weighted_profile_multiplier_and_one_request(self):
        base = _now_hour_epoch()
        forecaster = _forecaster(
            history_days=[-7, -14, -21], history_weights=[3, 2, 1],
            multiplier=1.5)
        with patch(GET, side_effect=_fake_server(_weeks_ago_value(base))) as mock_get:
            forecast = forecaster.get_forecast(3)
            assert mock_get.call_count == 1
            forecaster.refresh_data()
            forecaster.get_forecast(3)
            # the first refresh asked for 3 hours only, the second for 48
            assert mock_get.call_count == 2
            forecaster.refresh_data()
            assert mock_get.call_count == 2

        # weeks ago 1, 2, 3 with 1000 Wh per week and the weights 3, 2, 1
        expected = (3 * 1000 + 2 * 2000 + 1 * 3000) / 6 * 1.5
        assert sorted(forecast) == [0, 1, 2]
        for value in forecast.values():
            assert value == pytest.approx(expected)

    def test_48_hours_with_one_request(self):
        base = _now_hour_epoch()
        forecaster = _forecaster()
        with patch(GET, side_effect=_fake_server(_weeks_ago_value(base))) as mock_get:
            forecaster.refresh_data()
        assert mock_get.call_count == 1
        assert len(forecaster.consumption_cache) == 48

    def test_missing_history_day_is_left_out_of_the_average(self):
        base = _now_hour_epoch()
        forecaster = _forecaster(
            history_days=[-7, -14, -21], history_weights=[3, 2, 1])
        value_fn = _weeks_ago_value(base, skip=lambda weeks, _: weeks == 2)
        with patch(GET, side_effect=_fake_server(value_fn)):
            forecast = forecaster.get_forecast(2)
        assert forecast[0] == pytest.approx((3 * 1000 + 1 * 3000) / 4)

    def test_hour_without_data_ends_the_forecast(self):
        base = _now_hour_epoch()
        forecaster = _forecaster()
        value_fn = _weeks_ago_value(base, skip=lambda _, offset: offset >= 5)
        with patch(GET, side_effect=_fake_server(value_fn)):
            forecaster.refresh_data_with_limit(8)
        assert len(forecaster.consumption_cache) == 5

    def test_server_in_utc(self):
        base = _now_hour_epoch()
        forecaster = _forecaster()
        fake = _fake_server(_weeks_ago_value(base), server_tz=UTC)
        with patch(GET, side_effect=fake):
            forecast = forecaster.get_forecast(2)
        assert forecast[0] == pytest.approx((1000 + 2000 + 3000) / 3)

    def test_quarter_hour_target_resolution(self):
        base = _now_hour_epoch()
        forecaster = _forecaster(target_resolution=15)
        with patch(GET, side_effect=_fake_server(_weeks_ago_value(base))):
            forecast = forecaster.get_forecast(8)
        assert len(forecast) == 8
        hour_value = (1000 + 2000 + 3000) / 3
        for value in forecast.values():
            assert value == pytest.approx(hour_value / 4)
