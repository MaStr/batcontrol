"""
Test module for ForecastSolarBaseclass
"""
import pytest
import time
import pytz
from unittest.mock import MagicMock, patch, call
from batcontrol.forecastsolar.baseclass import (
    ForecastSolarBaseclass,
    ProviderError,
    RateLimitException
)
from batcontrol.fetcher.relaxed_caching import CacheMissError


class ConcreteForecastSolar(ForecastSolarBaseclass):
    """Concrete implementation of ForecastSolarBaseclass for testing"""

    def __init__(self, pvinstallations, timezone, min_time_between_API_calls,
                 delay_evaluation_by_seconds, mock_provider_func=None, mock_forecast_func=None,
                 target_resolution=60, native_resolution=60):
        super().__init__(pvinstallations, timezone, min_time_between_API_calls,
                        delay_evaluation_by_seconds,
                        target_resolution=target_resolution,
                        native_resolution=native_resolution)
        self.mock_provider_func = mock_provider_func
        self.mock_forecast_func = mock_forecast_func

    def get_raw_data_from_provider(self, pvinstallation_name):
        if self.mock_provider_func:
            return self.mock_provider_func(pvinstallation_name)
        return {'test': 'data'}

    def get_forecast_from_raw_data(self):
        if self.mock_forecast_func:
            return self.mock_forecast_func()
        return {0: 100.0, 1: 200.0, 18: 50.0}


class TestForecastSolarBaseclass:
    """Tests for ForecastSolarBaseclass"""

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @pytest.fixture
    def pvinstallations(self):
        """Fixture for PV installations config"""
        return [
            {'name': 'installation1'},
            {'name': 'installation2'}
        ]

    @pytest.fixture
    def single_installation(self):
        """Fixture for single PV installation"""
        return [{'name': 'single'}]

    @pytest.fixture
    def baseclass_instance(self, pvinstallations, timezone):
        """Fixture for ForecastSolarBaseclass instance"""
        return ConcreteForecastSolar(
            pvinstallations,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0
        )

    def test_initialization(self, baseclass_instance, pvinstallations):
        """Test that ForecastSolarBaseclass initializes correctly"""
        assert baseclass_instance.pvinstallations == pvinstallations
        assert baseclass_instance.next_update_ts == 0
        assert baseclass_instance.min_time_between_updates == 900
        assert baseclass_instance.delay_evaluation_by_seconds == 0
        assert baseclass_instance.rate_limit_blackout_window_ts == 0
        assert len(baseclass_instance.cache_list) == 2
        assert 'installation1' in baseclass_instance.cache_list
        assert 'installation2' in baseclass_instance.cache_list

    def test_initialization_without_name(self, timezone):
        """Test that initialization fails without 'name' key"""
        with pytest.raises(ValueError, match="'name' key"):
            ConcreteForecastSolar(
                [{'no_name': 'value'}],
                timezone,
                min_time_between_API_calls=900,
                delay_evaluation_by_seconds=0
            )

    def test_store_and_get_raw_data(self, baseclass_instance):
        """Test storing and retrieving raw data"""
        test_data = {'forecast': [100, 200, 300]}
        baseclass_instance.store_raw_data('installation1', test_data)

        retrieved_data = baseclass_instance.get_raw_data('installation1')
        assert retrieved_data == test_data

    def test_get_all_raw_data(self, baseclass_instance):
        """Test getting all raw data"""
        data1 = {'forecast': [100, 200]}
        data2 = {'forecast': [300, 400]}

        baseclass_instance.store_raw_data('installation1', data1)
        baseclass_instance.store_raw_data('installation2', data2)

        all_data = baseclass_instance.get_all_raw_data()

        assert all_data['installation1'] == data1
        assert all_data['installation2'] == data2

    def test_refresh_data_initial_call(self, single_installation, timezone):
        """Test refresh_data on initial call (no delay)"""
        mock_data = {'test': 'initial'}

        def mock_provider(name):
            return mock_data

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=10,
            mock_provider_func=mock_provider
        )

        with patch('time.sleep') as mock_sleep:
            instance.refresh_data()
            # On initial call (next_update_ts == 0), should not sleep
            mock_sleep.assert_not_called()

        assert instance.get_raw_data('single') == mock_data

    def test_refresh_data_with_delay(self, single_installation, timezone):
        """Test refresh_data applies random delay on subsequent calls"""
        mock_data = {'test': 'delayed'}
        call_count = [0]

        def mock_provider(name):
            call_count[0] += 1
            return {'call': call_count[0]}

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=1,  # 1 second for quick test
            delay_evaluation_by_seconds=5,
            mock_provider_func=mock_provider
        )

        # First call
        instance.refresh_data()

        # Wait for next update window
        time.sleep(1.1)

        # Second call should trigger delay
        with patch('time.sleep') as mock_sleep:
            with patch('random.randrange', return_value=3) as mock_random:
                instance.refresh_data()
                mock_random.assert_called_once_with(0, 5, 1)
                mock_sleep.assert_called_once_with(3)

    def test_refresh_data_rate_limit(self, single_installation, timezone):
        """Test refresh_data respects rate limit blackout window"""
        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=1,
            delay_evaluation_by_seconds=0
        )

        # Set blackout window
        future_time = time.time() + 100
        instance.rate_limit_blackout_window_ts = future_time

        # Try to refresh - should skip
        with patch.object(instance, 'get_raw_data_from_provider') as mock_provider:
            instance.refresh_data()
            mock_provider.assert_not_called()
            assert instance.next_update_ts == future_time

    def test_refresh_data_multiple_installations(self, pvinstallations, timezone):
        """Test refresh_data fetches data for all installations"""
        call_log = []

        def mock_provider(name):
            call_log.append(name)
            return {'installation': name}

        instance = ConcreteForecastSolar(
            pvinstallations,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        instance.refresh_data()

        assert 'installation1' in call_log
        assert 'installation2' in call_log
        assert instance.get_raw_data('installation1') == {'installation': 'installation1'}
        assert instance.get_raw_data('installation2') == {'installation': 'installation2'}

    def test_refresh_data_connection_error(self, single_installation, timezone):
        """Test refresh_data handles connection errors gracefully"""

        def mock_provider(name):
            raise ConnectionError("Network error")

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        # Should not raise, just log warning
        instance.refresh_data()

    def test_refresh_data_timeout_error(self, single_installation, timezone):
        """Test refresh_data handles timeout errors gracefully"""

        def mock_provider(name):
            raise TimeoutError("Request timeout")

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        # Should not raise, just log warning
        instance.refresh_data()

    def test_refresh_data_provider_error(self, single_installation, timezone):
        """Test refresh_data handles provider errors gracefully"""

        def mock_provider(name):
            raise ProviderError("Provider unavailable")

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        # Should not raise, just log warning
        instance.refresh_data()

    def test_refresh_data_rate_limit_exception(self, single_installation, timezone):
        """Test refresh_data handles rate limit exceptions"""

        def mock_provider(name):
            raise RateLimitException("Too many requests")

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        # RateLimitException inherits from ProviderError, so it's caught and logged
        # Should not raise, but should handle gracefully
        instance.refresh_data()

    def test_refresh_data_respects_min_time(self, single_installation, timezone):
        """Test refresh_data respects minimum time between API calls"""
        call_count = [0]

        def mock_provider(name):
            call_count[0] += 1
            return {'call': call_count[0]}

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=2,  # 2 seconds
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider
        )

        # First call
        instance.refresh_data()
        assert call_count[0] == 1

        # Immediate second call - should skip
        instance.refresh_data()
        assert call_count[0] == 1

        # Wait and call again
        time.sleep(2.1)
        instance.refresh_data()
        assert call_count[0] == 2

    def test_get_forecast_success(self, single_installation, timezone):
        """Test get_forecast with successful data"""

        def mock_provider(name):
            return {'data': 'test'}

        def mock_forecast():
            return {i: float(i * 10) for i in range(24)}

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider,
            mock_forecast_func=mock_forecast
        )

        forecast = instance.get_forecast()
        # Original 24 entries must be intact; _pad_to_midnight may add trailing zeros
        # up to the midnight following the last entry, so total length >= 24.
        assert len(forecast) >= 24
        assert forecast[0] == 0.0
        assert forecast[18] == 180.0
        # Any padding must be zero
        for idx in range(24, len(forecast)):
            assert forecast[idx] == 0.0

    def test_get_forecast_insufficient_hours(self, single_installation, timezone):
        """Test get_forecast raises error with insufficient forecast hours.

        Clock is fixed at 22:00. The provider returns only 2 intervals (22:00
        and 23:00). _pad_to_midnight() fills to midnight (2 intervals total),
        which is still below the 12-interval minimum, so RuntimeError is raised.
        """
        import datetime as dt

        fixed_now = timezone.localize(dt.datetime(2024, 6, 1, 22, 0, 0))

        def mock_provider(name):
            return {'data': 'test'}

        def mock_forecast():
            # Only 2 hours of data - stays below 12 even after padding to midnight
            return {i: float(i * 10) for i in range(2)}

        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=mock_provider,
            mock_forecast_func=mock_forecast
        )

        with patch('batcontrol.forecastsolar.baseclass.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = dt.timedelta
            mock_dt.timezone = dt.timezone
            with pytest.raises(RuntimeError, match="Less than 12 hours"):
                instance.get_forecast()

    def test_pad_to_midnight_dst_spring_forward(self, single_installation, timezone):
        """_pad_to_midnight must count a 23-hour day correctly (spring forward).

        On 2024-03-31 Europe/Berlin loses an hour at 02:00 (CET -> CEST), so the
        day has only 23 hourly intervals. A naive interval_start + timedelta would
        keep the CET offset and yield 24; normalize/localize must give 23.
        """
        import datetime as dt

        instance = ConcreteForecastSolar(
            single_installation, timezone,
            min_time_between_API_calls=900, delay_evaluation_by_seconds=0,
        )

        # Start of the DST day, before the 02:00 jump.
        fixed_now = timezone.localize(dt.datetime(2024, 3, 31, 0, 0, 0))

        with patch('batcontrol.forecastsolar.baseclass.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = dt.timedelta
            mock_dt.timezone = dt.timezone
            result = instance._pad_to_midnight({0: 100.0})

        # 00:00 to next midnight on a 23-hour day = 23 hourly intervals (0..22).
        assert len(result) == 23
        assert result[0] == 100.0
        assert result[22] == 0.0

    def test_pad_to_midnight_dst_fall_back(self, single_installation, timezone):
        """_pad_to_midnight must count a 25-hour day correctly (fall back).

        On 2024-10-27 Europe/Berlin gains an hour at 03:00 (CEST -> CET), so the
        day has 25 hourly intervals. Naive arithmetic would yield 24; the
        normalize/localize path must give 25.
        """
        import datetime as dt

        instance = ConcreteForecastSolar(
            single_installation, timezone,
            min_time_between_API_calls=900, delay_evaluation_by_seconds=0,
        )

        fixed_now = timezone.localize(dt.datetime(2024, 10, 27, 0, 0, 0))

        with patch('batcontrol.forecastsolar.baseclass.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = dt.timedelta
            mock_dt.timezone = dt.timezone
            result = instance._pad_to_midnight({0: 100.0})

        # 00:00 to next midnight on a 25-hour day = 25 hourly intervals (0..24).
        assert len(result) == 25
        assert result[0] == 100.0
        assert result[24] == 0.0

    def test_base_class_not_implemented_errors(self, single_installation, timezone):
        """Test that base class methods raise NotImplementedError"""
        instance = ForecastSolarBaseclass(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0
        )

        with pytest.raises(RuntimeError, match="not implemented"):
            instance.get_raw_data_from_provider('single')

        with pytest.raises(RuntimeError, match="not implemented"):
            instance.get_forecast_from_raw_data()

    def test_exception_classes(self):
        """Test custom exception classes"""
        # Test ProviderError
        error = ProviderError("Test error")
        assert str(error) == "Test error"
        assert isinstance(error, Exception)

        # Test RateLimitException
        rate_error = RateLimitException("Rate limit")
        assert str(rate_error) == "Rate limit"
        assert isinstance(rate_error, ProviderError)
        assert isinstance(rate_error, Exception)

    def test_cache_initialization_per_installation(self, pvinstallations, timezone):
        """Test that each installation gets its own cache"""
        instance = ConcreteForecastSolar(
            pvinstallations,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0
        )

        # Verify each installation has a separate cache
        assert 'installation1' in instance.cache_list
        assert 'installation2' in instance.cache_list
        assert instance.cache_list['installation1'] is not instance.cache_list['installation2']

    def test_timezone_storage(self, single_installation, timezone):
        """Test that timezone is properly stored"""
        instance = ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0
        )

        assert instance.timezone == timezone
        assert str(instance.timezone) == 'Europe/Berlin'


class TestNativeResolution30:
    """Tests for the 30-minute native resolution conversion paths."""

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @pytest.fixture
    def single_installation(self):
        """Fixture for single PV installation"""
        return [{'name': 'single'}]

    def _make_instance(self, single_installation, timezone, target_resolution,
                       forecast_30min):
        """Build a ConcreteForecastSolar with native 30-min data."""
        return ConcreteForecastSolar(
            single_installation,
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            mock_provider_func=lambda name: {'data': 'test'},
            mock_forecast_func=lambda: forecast_30min,
            target_resolution=target_resolution,
            native_resolution=30
        )

    def test_convert_resolution_30_to_15_interpolates(self, single_installation, timezone):
        """30 -> 15 conversion must interpolate power, not duplicate values."""
        instance = self._make_instance(single_installation, timezone, 15, {})

        # Bucket 0: 500 Wh (1000 W), bucket 1: 1000 Wh (2000 W)
        result = instance._convert_resolution({0: 500.0, 1: 1000.0})

        assert result[0] == pytest.approx(250, rel=0.01)
        assert result[1] == pytest.approx(375, rel=0.01)
        assert result[2] == pytest.approx(500, rel=0.01)
        assert result[3] == pytest.approx(500, rel=0.01)
        # Plain doubling of the 30-min bucket would yield identical quarters
        assert result[0] != result[1]

    def test_convert_resolution_30_to_60_sums_pairs(self, single_installation, timezone):
        """30 -> 60 conversion must sum bucket pairs exactly."""
        instance = self._make_instance(single_installation, timezone, 60, {})

        result = instance._convert_resolution({0: 500.0, 1: 700.0, 2: 300.0, 3: 100.0})

        assert result[0] == pytest.approx(1200, abs=0.001)
        assert result[1] == pytest.approx(400, abs=0.001)

    def test_get_forecast_native_30_target_15(self, single_installation, timezone):
        """Full get_forecast() path with native 30-min data and 15-min target."""
        import datetime as dt

        # 24 buckets = 12 hours of 30-min data with a rising ramp
        forecast_30min = {i: float(100 * i) for i in range(24)}

        instance = self._make_instance(single_installation, timezone, 15, forecast_30min)

        fixed_now = timezone.localize(dt.datetime(2024, 6, 1, 10, 0, 0))
        with patch('batcontrol.forecastsolar.baseclass.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = dt.timedelta
            mock_dt.timezone = dt.timezone
            forecast = instance.get_forecast()

        # 12 hours of data, padded to midnight (14 hours) = 56 intervals minimum
        assert len(forecast) >= 48

        # Bucket 0 = 0 Wh (0 W), bucket 1 = 100 Wh (200 W):
        # quarters of bucket 0 ramp from 0 W to 200 W -> 0 W and 100 W
        # -> 0 Wh and 100 W * 0.25h = 25 Wh
        assert forecast[0] == pytest.approx(0, abs=0.001)
        assert forecast[1] == pytest.approx(25.0, rel=0.01)
        # Interpolated output: adjacent quarters of a bucket differ on a ramp
        assert forecast[2] != forecast[3]

    def test_get_forecast_native_30_target_60(self, single_installation, timezone):
        """Full get_forecast() path with native 30-min data and 60-min target."""
        import datetime as dt

        forecast_30min = {i: float(100 * i) for i in range(24)}

        instance = self._make_instance(single_installation, timezone, 60, forecast_30min)

        fixed_now = timezone.localize(dt.datetime(2024, 6, 1, 10, 0, 0))
        with patch('batcontrol.forecastsolar.baseclass.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_now
            mock_dt.timedelta = dt.timedelta
            mock_dt.timezone = dt.timezone
            forecast = instance.get_forecast()

        assert len(forecast) >= 12

        # Hourly values are the exact bucket pair sums
        assert forecast[0] == pytest.approx(0 + 100, abs=0.001)
        assert forecast[1] == pytest.approx(200 + 300, abs=0.001)
        assert forecast[11] == pytest.approx(2200 + 2300, abs=0.001)
