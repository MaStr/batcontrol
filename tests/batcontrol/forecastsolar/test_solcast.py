"""
Test module for the Solcast solar forecast provider.
"""
import datetime
import time
from unittest.mock import MagicMock, patch

import pytest
import pytz

from batcontrol.forecastsolar.solcast import Solcast
from batcontrol.forecastsolar.baseclass import ProviderError, RateLimitException


def _period_end_str(dt_utc: datetime.datetime) -> str:
    """Format a UTC datetime like Solcast does (7-digit fraction + Z)."""
    return dt_utc.strftime('%Y-%m-%dT%H:%M:%S.0000000Z')


class TestSolcast:
    """Tests for the Solcast provider."""

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @pytest.fixture
    def pvinstallations(self):
        """Fixture for a single Solcast installation"""
        return [{
            'name': 'default',
            'resource_id': 'aaaa-bbbb-cccc-dddd',
            'apikey': 'test-api-key'
        }]

    @pytest.fixture
    def instance(self, pvinstallations, timezone):
        """Fixture for a Solcast instance"""
        return Solcast(
            pvinstallations,
            timezone,
            min_time_between_api_calls=900,
            delay_evaluation_by_seconds=0
        )

    def _current_hour_start_utc(self, timezone):
        """Start of the current hour as a UTC datetime."""
        now = datetime.datetime.now().astimezone(timezone)
        local_hour_start = now.replace(minute=0, second=0, microsecond=0)
        return local_hour_start.astimezone(datetime.timezone.utc)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def test_initialization(self, instance):
        """Test init: native resolution and rate limit floor for one site."""
        assert instance.native_resolution == 30
        # 1 installation: 86400 // 8 = 10800 s (3 h)
        assert instance.min_time_between_updates == 10800

    def test_rate_limit_floor_scales_with_installations(self, timezone):
        """Test that the refresh floor doubles with two sites."""
        installations = [
            {'name': 'east', 'resource_id': 'res-1', 'apikey': 'key'},
            {'name': 'west', 'resource_id': 'res-2', 'apikey': 'key'},
        ]
        instance = Solcast(installations, timezone,
                           min_time_between_api_calls=900,
                           delay_evaluation_by_seconds=0)
        # 2 installations: 86400 * 2 // 8 = 21600 s (6 h)
        assert instance.min_time_between_updates == 21600

    def test_larger_caller_value_respected(self, pvinstallations, timezone):
        """Test that a caller value above the floor is kept."""
        instance = Solcast(pvinstallations, timezone,
                           min_time_between_api_calls=50000,
                           delay_evaluation_by_seconds=0)
        assert instance.min_time_between_updates == 50000

    def test_missing_resource_id(self, timezone):
        """Test that a missing resource_id raises ValueError."""
        with pytest.raises(ValueError, match='resource_id'):
            Solcast([{'name': 'default', 'apikey': 'key'}], timezone,
                    min_time_between_api_calls=900,
                    delay_evaluation_by_seconds=0)

    def test_missing_apikey(self, timezone):
        """Test that a missing apikey raises ValueError."""
        with pytest.raises(ValueError, match='API key'):
            Solcast([{'name': 'default', 'resource_id': 'res-1'}], timezone,
                    min_time_between_api_calls=900,
                    delay_evaluation_by_seconds=0)

    def test_invalid_percentile(self, timezone):
        """Test that an invalid percentile raises ValueError."""
        with pytest.raises(ValueError, match='percentile'):
            Solcast([{'name': 'default', 'resource_id': 'res-1',
                      'apikey': 'key', 'percentile': 25}], timezone,
                    min_time_between_api_calls=900,
                    delay_evaluation_by_seconds=0)

    # ------------------------------------------------------------------
    # Parsing 30-minute raw data
    # ------------------------------------------------------------------

    def test_parse_30min_periods(self, instance, timezone):
        """Test that periods map to 30-minute indices with kW -> Wh conversion."""
        hour_start_utc = self._current_hour_start_utc(timezone)

        raw_data = {'forecasts': [
            {   # covers :00-:30 -> index 0
                'pv_estimate': 1.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=30)),
                'period': 'PT30M'
            },
            {   # covers :30-:00 -> index 1
                'pv_estimate': 2.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=60)),
                'period': 'PT30M'
            },
        ]}
        instance.store_raw_data('default', raw_data)

        forecast = instance.get_forecast_from_raw_data()

        # 1 kW avg over 30 min = 500 Wh; 2 kW = 1000 Wh
        assert forecast[0] == pytest.approx(500.0, abs=0.001)
        assert forecast[1] == pytest.approx(1000.0, abs=0.001)

    def test_partial_current_hour(self, instance, timezone):
        """Test that a missing first half-hour is filled with zero."""
        hour_start_utc = self._current_hour_start_utc(timezone)

        raw_data = {'forecasts': [
            {   # only :30-:00 -> index 1
                'pv_estimate': 2.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=60)),
                'period': 'PT30M'
            },
        ]}
        instance.store_raw_data('default', raw_data)

        forecast = instance.get_forecast_from_raw_data()

        assert forecast[0] == 0.0
        assert forecast[1] == pytest.approx(1000.0, abs=0.001)

    def test_gap_filling(self, instance, timezone):
        """Test that gaps between periods are filled with zero."""
        hour_start_utc = self._current_hour_start_utc(timezone)

        raw_data = {'forecasts': [
            {
                'pv_estimate': 1.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=30)),
                'period': 'PT30M'
            },
            {   # gap at indices 1 and 2, next period -> index 3
                'pv_estimate': 1.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=120)),
                'period': 'PT30M'
            },
        ]}
        instance.store_raw_data('default', raw_data)

        forecast = instance.get_forecast_from_raw_data()

        assert forecast[0] == pytest.approx(500.0, abs=0.001)
        assert forecast[1] == 0.0
        assert forecast[2] == 0.0
        assert forecast[3] == pytest.approx(500.0, abs=0.001)

    def test_past_periods_skipped(self, instance, timezone):
        """Test that periods before the current hour are skipped."""
        hour_start_utc = self._current_hour_start_utc(timezone)

        raw_data = {'forecasts': [
            {   # ends exactly at hour start -> in the past
                'pv_estimate': 5.0,
                'period_end': _period_end_str(hour_start_utc),
                'period': 'PT30M'
            },
            {
                'pv_estimate': 1.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=30)),
                'period': 'PT30M'
            },
        ]}
        instance.store_raw_data('default', raw_data)

        forecast = instance.get_forecast_from_raw_data()

        assert forecast[0] == pytest.approx(500.0, abs=0.001)
        assert len(forecast) == 1

    def test_empty_raw_data(self, instance):
        """Test that empty raw data yields an empty forecast."""
        instance.store_raw_data('default', {'forecasts': []})
        assert instance.get_forecast_from_raw_data() == {}

    # ------------------------------------------------------------------
    # Percentile selection
    # ------------------------------------------------------------------

    def _raw_data_with_percentiles(self, timezone):
        hour_start_utc = self._current_hour_start_utc(timezone)
        return {'forecasts': [
            {
                'pv_estimate': 2.0,
                'pv_estimate10': 1.0,
                'pv_estimate90': 3.0,
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=30)),
                'period': 'PT30M'
            },
        ]}

    def test_percentile_default_50(self, instance, timezone):
        """Test that pv_estimate is used by default."""
        instance.store_raw_data('default', self._raw_data_with_percentiles(timezone))
        forecast = instance.get_forecast_from_raw_data()
        assert forecast[0] == pytest.approx(1000.0, abs=0.001)  # 2.0 kW

    @pytest.mark.parametrize('percentile,expected_wh', [(10, 500.0), (90, 1500.0)])
    def test_percentile_option(self, timezone, percentile, expected_wh):
        """Test that percentile 10/90 selects pv_estimate10/pv_estimate90."""
        installations = [{'name': 'default', 'resource_id': 'res-1',
                          'apikey': 'key', 'percentile': percentile}]
        instance = Solcast(installations, timezone,
                           min_time_between_api_calls=900,
                           delay_evaluation_by_seconds=0)
        instance.store_raw_data('default', self._raw_data_with_percentiles(timezone))

        forecast = instance.get_forecast_from_raw_data()
        assert forecast[0] == pytest.approx(expected_wh, abs=0.001)

    # ------------------------------------------------------------------
    # Multiple installations
    # ------------------------------------------------------------------

    def test_multiple_installations_summed(self, timezone):
        """Test that forecasts of two rooftop sites are summed per slot."""
        installations = [
            {'name': 'east', 'resource_id': 'res-1', 'apikey': 'key'},
            {'name': 'west', 'resource_id': 'res-2', 'apikey': 'key'},
        ]
        instance = Solcast(installations, timezone,
                           min_time_between_api_calls=900,
                           delay_evaluation_by_seconds=0)

        hour_start_utc = self._current_hour_start_utc(timezone)
        period_end = _period_end_str(
            hour_start_utc + datetime.timedelta(minutes=30))

        instance.store_raw_data('east', {'forecasts': [
            {'pv_estimate': 1.0, 'period_end': period_end, 'period': 'PT30M'}]})
        instance.store_raw_data('west', {'forecasts': [
            {'pv_estimate': 2.0, 'period_end': period_end, 'period': 'PT30M'}]})

        forecast = instance.get_forecast_from_raw_data()

        assert forecast[0] == pytest.approx(1500.0, abs=0.001)  # 500 + 1000 Wh

    # ------------------------------------------------------------------
    # Timestamp parsing
    # ------------------------------------------------------------------

    def test_parse_period_end(self):
        """Regression: Solcast's 7-digit fraction + Z parses on Python < 3.11."""
        result = Solcast._parse_period_end('2026-01-01T01:00:00.0000000Z')

        assert result == datetime.datetime(
            2026, 1, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
        assert result.tzinfo is not None

    def test_parse_period_end_without_fraction(self):
        """Test parsing a timestamp without fractional seconds."""
        result = Solcast._parse_period_end('2026-01-01T12:30:00Z')

        assert result == datetime.datetime(
            2026, 1, 1, 12, 30, 0, tzinfo=datetime.timezone.utc)

    # ------------------------------------------------------------------
    # HTTP handling
    # ------------------------------------------------------------------

    def _mock_response(self, status_code, json_data=None, headers=None):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = json_data if json_data is not None else {}
        response.headers = headers if headers is not None else {}
        response.text = ''
        return response

    def test_http_200(self, instance):
        """Test that a 200 response returns parsed JSON with Bearer auth."""
        payload = {'forecasts': []}
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(200, payload)) as mock_get:
            result = instance.get_raw_data_from_provider('default')

        assert result == payload
        args, kwargs = mock_get.call_args
        assert 'aaaa-bbbb-cccc-dddd/forecasts' in args[0]
        assert 'format=json' in args[0]
        assert kwargs['headers']['Authorization'] == 'Bearer test-api-key'
        # API key must not appear in the URL
        assert 'test-api-key' not in args[0]

    def test_http_429_sets_blackout_with_retry_after(self, instance):
        """Test that 429 with Retry-After raises RateLimitException + blackout."""
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(429, headers={'Retry-After': '600'})):
            with pytest.raises(RateLimitException):
                instance.get_raw_data_from_provider('default')

        assert instance.rate_limit_blackout_window_ts == pytest.approx(
            time.time() + 600, abs=5)

    def test_http_429_sets_blackout_without_retry_after(self, instance):
        """Test that 429 without Retry-After falls back to the provider floor."""
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(429)):
            with pytest.raises(RateLimitException):
                instance.get_raw_data_from_provider('default')

        assert instance.rate_limit_blackout_window_ts == pytest.approx(
            time.time() + 10800, abs=5)

    def test_http_401(self, instance):
        """Test that 401 raises ProviderError."""
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(401)):
            with pytest.raises(ProviderError, match='Unauthorized'):
                instance.get_raw_data_from_provider('default')

    def test_http_404(self, instance):
        """Test that 404 raises ProviderError mentioning the resource_id."""
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(404)):
            with pytest.raises(ProviderError, match='resource_id'):
                instance.get_raw_data_from_provider('default')

    def test_http_500(self, instance):
        """Test that other status codes raise ProviderError."""
        with patch('batcontrol.forecastsolar.solcast.requests.get',
                   return_value=self._mock_response(500)):
            with pytest.raises(ProviderError):
                instance.get_raw_data_from_provider('default')

    # ------------------------------------------------------------------
    # End-to-end through the baseclass
    # ------------------------------------------------------------------

    def _build_ramp_raw_data(self, timezone, buckets=28):
        """Raw data with a rising power ramp over `buckets` half hours."""
        hour_start_utc = self._current_hour_start_utc(timezone)
        forecasts = []
        for i in range(buckets):
            forecasts.append({
                'pv_estimate': float(i),  # kW, rising ramp
                'period_end': _period_end_str(
                    hour_start_utc + datetime.timedelta(minutes=30 * (i + 1))),
                'period': 'PT30M'
            })
        return {'forecasts': forecasts}

    def test_get_forecast_target_15_interpolates(self, pvinstallations, timezone):
        """End-to-end: 30-min data is interpolated to 15-min, not duplicated."""
        instance = Solcast(pvinstallations, timezone,
                           min_time_between_api_calls=900,
                           delay_evaluation_by_seconds=0,
                           target_resolution=15)
        raw_data = self._build_ramp_raw_data(timezone)

        with patch.object(instance, 'get_raw_data_from_provider',
                          return_value=raw_data):
            forecast = instance.get_forecast()

        assert len(forecast) >= 48

        # On a strictly rising ramp the interpolated quarters keep rising,
        # so adjacent quarters of one bucket must not be identical.
        rising_pairs = [(forecast[i], forecast[i + 1])
                        for i in range(0, 8, 2)]
        assert any(a != b for a, b in rising_pairs)

    def test_get_forecast_target_60_sums_pairs(self, pvinstallations, timezone):
        """End-to-end: 30-min data is summed into hourly values."""
        instance = Solcast(pvinstallations, timezone,
                           min_time_between_api_calls=900,
                           delay_evaluation_by_seconds=0,
                           target_resolution=60)
        raw_data = self._build_ramp_raw_data(timezone)

        with patch.object(instance, 'get_raw_data_from_provider',
                          return_value=raw_data):
            forecast = instance.get_forecast()

        assert len(forecast) >= 12

        # Bucket i has pv_estimate = i kW -> i * 500 Wh.
        # Hour h sums buckets 2h and 2h+1: (2h + 2h+1) * 500 Wh.
        # Use a future hour to be independent of the current-interval shift
        # (hour-aligned index equals shifted index at target 60 only when
        # shift is 0; with 60-min resolution shift is always 0).
        assert forecast[1] == pytest.approx((2 + 3) * 500.0, abs=0.001)
        assert forecast[5] == pytest.approx((10 + 11) * 500.0, abs=0.001)
