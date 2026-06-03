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
                 delay_evaluation_by_seconds, mock_provider_func=None, mock_forecast_func=None):
        super().__init__(pvinstallations, timezone, min_time_between_API_calls,
                        delay_evaluation_by_seconds)
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

        fixed_now = dt.datetime(2024, 6, 1, 22, 0, 0, tzinfo=timezone)

        def mock_provider(name):
            return {'data': 'test'}

        def mock_forecast():
            # Only 2 hours of data — stays below 12 even after padding to midnight
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
