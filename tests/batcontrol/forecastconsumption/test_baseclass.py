"""
Test module for ForecastConsumptionBaseclass
"""
import contextlib
import pytest
import pytz
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
from batcontrol.forecastconsumption.baseclass import ForecastConsumptionBaseclass


class ConcreteConsumptionForecast(ForecastConsumptionBaseclass):
    """Concrete implementation of ForecastConsumptionBaseclass for testing"""

    def __init__(self, timezone, target_resolution=60, native_resolution=60,
                 mock_forecast_func=None):
        super().__init__(timezone, target_resolution, native_resolution)
        self.mock_forecast_func = mock_forecast_func
        self.requested_hours = []

    def _get_forecast_native(self, hours: int) -> dict[int, float]:
        """Mock implementation returning test data"""
        self.requested_hours.append(hours)
        if self.mock_forecast_func:
            return self.mock_forecast_func(hours)
        # Return simple test data: 1000 Wh per hour
        return {h: 1000.0 for h in range(hours)}


class TestForecastConsumptionBaseclass:
    """Tests for ForecastConsumptionBaseclass"""

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @pytest.fixture
    def baseclass_instance(self, timezone):
        """Fixture for ForecastConsumptionBaseclass instance with 60-min resolution"""
        return ConcreteConsumptionForecast(
            timezone, target_resolution=60, native_resolution=60)

    @pytest.fixture
    def baseclass_instance_15min(self, timezone):
        """Fixture for ForecastConsumptionBaseclass instance with 15-min target resolution"""
        return ConcreteConsumptionForecast(
            timezone, target_resolution=15, native_resolution=60)

    def test_initialization(self, baseclass_instance, timezone):
        """Test that ForecastConsumptionBaseclass initializes correctly"""
        assert baseclass_instance.timezone == timezone
        assert baseclass_instance.target_resolution == 60
        assert baseclass_instance.native_resolution == 60

    def test_initialization_15min_target(self, baseclass_instance_15min):
        """Test initialization with 15-minute target resolution"""
        assert baseclass_instance_15min.target_resolution == 15
        assert baseclass_instance_15min.native_resolution == 60

    def test_get_forecast_no_conversion_needed(self, timezone):
        """Test get_forecast when native and target resolution match"""
        # Mock time to 10:20
        mock_now = datetime(
            2024,
            1,
            15,
            10,
            20,
            0,
            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=60, native_resolution=60)
            forecast = instance.get_forecast(5)

            # Should return data starting from current hour (no shifting for
            # 60-min)
            assert len(forecast) == 5
            assert all(v == 1000.0 for v in forecast.values())
            # 5 hourly slots are 5 hours of native data
            assert instance.requested_hours == [5]

    def test_get_forecast_with_upsampling(self, timezone):
        """Test get_forecast with upsampling from 60min to 15min"""
        # Mock time to 10:00 (start of hour for simplicity)
        mock_now = datetime(
            2024,
            1,
            15,
            10,
            0,
            0,
            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            # Create instance with 15-min target, 60-min native
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=60)
            forecast = instance.get_forecast(
                8)  # 8 intervals of 15 min = 2 hours

            # Should upsample 2 hours to 8 intervals
            assert len(forecast) == 8
            assert instance.requested_hours == [2]
            # Each 15-min interval should have 250 Wh (1000 / 4)
            for value in forecast.values():
                assert abs(value - 250.0) < 0.1

    def test_get_forecast_with_interval_alignment(self, timezone):
        """Test that forecast is aligned to current interval"""
        # Mock time to 10:20 (in the middle of second 15-min interval)
        mock_now = datetime(
            2024,
            1,
            15,
            10,
            20,
            0,
            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            # Create instance with 15-min target
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=60)
            forecast = instance.get_forecast(8)

            # At 10:20, we're in slot 1 of the hour (10:15-10:30), so the
            # first slot of the hour is dropped. The requested number of
            # slots is still delivered, by fetching one extra hour of
            # native data.
            assert len(forecast) == 8
            assert 0 in forecast  # First interval should be index 0
            assert instance.requested_hours == [3]

    def test_empty_native_forecast(self, timezone):
        """Test handling of empty native forecast"""
        def mock_empty_forecast(hours):
            return {}

        instance = ConcreteConsumptionForecast(
            timezone, target_resolution=60, native_resolution=60,
            mock_forecast_func=mock_empty_forecast
        )

        forecast = instance.get_forecast(5)
        assert forecast == {}

    def test_refresh_data_default(self, baseclass_instance):
        """Test that default refresh_data is a no-op"""
        # Should not raise any exception
        baseclass_instance.refresh_data()

    def test_convert_resolution_no_conversion(self, baseclass_instance):
        """Test _convert_resolution when resolutions match"""
        test_forecast = {0: 1000.0, 1: 1200.0, 2: 1400.0}
        result = baseclass_instance._convert_resolution(test_forecast, hours=3)
        assert result == test_forecast

    def test_convert_resolution_upsample_60_to_15(self, timezone):
        """Test _convert_resolution upsampling from 60 to 15 minutes"""
        instance = ConcreteConsumptionForecast(timezone, target_resolution=15,
                                               native_resolution=60)
        test_forecast = {0: 1000.0, 1: 2000.0}
        result = instance._convert_resolution(test_forecast, hours=2)

        # Should have 8 intervals (2 hours * 4 quarters)
        assert len(result) == 8
        # First hour quarters should each be 250 Wh
        assert abs(result[0] - 250.0) < 0.1
        assert abs(result[1] - 250.0) < 0.1
        assert abs(result[2] - 250.0) < 0.1
        assert abs(result[3] - 250.0) < 0.1
        # Second hour quarters should each be 500 Wh
        assert abs(result[4] - 500.0) < 0.1
        assert abs(result[5] - 500.0) < 0.1

    def test_convert_resolution_downsample_15_to_60(self, timezone):
        """Test _convert_resolution downsampling from 15 to 60 minutes"""
        instance = ConcreteConsumptionForecast(timezone, target_resolution=60,
                                               native_resolution=15)

        # 8 intervals of 15 min = 2 hours
        # Each interval has 250 Wh, so each hour should sum to 1000 Wh
        test_forecast = {0: 250.0, 1: 250.0, 2: 250.0, 3: 250.0,
                         4: 300.0, 5: 300.0, 6: 300.0, 7: 300.0}
        result = instance._convert_resolution(test_forecast, hours=2)

        # Should have 2 hourly values
        assert len(result) == 2
        assert abs(result[0] - 1000.0) < 0.1  # Sum of first 4 intervals
        assert abs(result[1] - 1200.0) < 0.1  # Sum of next 4 intervals

    def test_shift_to_current_interval_start_of_hour(self, timezone):
        """Test _shift_to_current_interval at the start of an hour"""
        # At 10:00, current interval is 0
        mock_now = datetime(
            2024,
            1,
            15,
            10,
            0,
            0,
            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=15)
            test_forecast = {0: 100, 1: 200, 2: 300, 3: 400}
            result = instance._shift_to_current_interval(test_forecast)

            # No shift needed at start of hour
            assert result == test_forecast

    def test_shift_to_current_interval_mid_hour(self, timezone):
        """Test _shift_to_current_interval in the middle of an hour"""
        # At 10:30, we're in interval 2 (10:30-10:45) for 15-min resolution
        mock_now = datetime(
            2024,
            1,
            15,
            10,
            30,
            0,
            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=15)
            test_forecast = {0: 100, 1: 200, 2: 300, 3: 400, 4: 500}
            result = instance._shift_to_current_interval(test_forecast)

            # Should drop intervals 0 and 1, shift others down
            assert len(result) == 3
            assert result[0] == 300  # Was index 2
            assert result[1] == 400  # Was index 3
            assert result[2] == 500  # Was index 4

    def test_thread_safety(self, timezone):
        """Test that forecast operations are thread-safe"""
        instance = ConcreteConsumptionForecast(timezone, target_resolution=60,
                                               native_resolution=60)

        # Multiple calls should not interfere with each other
        forecast1 = instance.get_forecast(5)
        forecast2 = instance.get_forecast(5)

        assert forecast1 == forecast2
        assert len(forecast1) == 5


class TestForecastHorizonSlots:
    """Regression tests for the forecast horizon (issue #446).

    core.py requests a number of slots at the configured target resolution.
    Before the fix the baseclass forwarded that count to
    _get_forecast_native() as hours, so a 24 h request in 15-minute mode
    fetched ~96 h of hourly history (4x the HomeAssistant queries needed).
    """

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @staticmethod
    @contextlib.contextmanager
    def _at(timezone, hour, minute):
        """Patch the baseclass' notion of 'now' to the given local time"""
        mock_now = datetime(
            2024, 1, 15, hour, minute, 0,
            tzinfo=dt_timezone.utc).astimezone(timezone)
        with patch('batcontrol.forecastconsumption.baseclass.datetime') \
                as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone
            yield

    @pytest.mark.parametrize('minute', [0, 14, 20, 35, 50, 59])
    def test_hourly_horizon_is_requested_slots(self, timezone, minute):
        """60-min mode: a slot is an hour, horizon must match exactly"""
        with self._at(timezone, 10, minute):
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=60, native_resolution=60)
            forecast = instance.get_forecast(48)

            assert len(forecast) == 48
            assert instance.requested_hours == [48]

    @pytest.mark.parametrize('minute, expected_hours', [
        (0, 24),    # no slot elapsed in this hour
        (14, 24),
        (20, 25),   # one slot elapsed -> one extra hour needed
        (35, 25),
        (50, 25),
        (59, 25),
    ])
    def test_quarterly_horizon_is_converted_to_hours(
            self, timezone, minute, expected_hours):
        """15-min mode: 96 slots are 24 h, not 96 h"""
        with self._at(timezone, 10, minute):
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=60)
            forecast = instance.get_forecast(96)

            # Full horizon delivered, regardless of the position in the hour
            assert len(forecast) == 96
            assert sorted(forecast.keys()) == list(range(96))
            # And it was built from a day of hourly data, not four days
            assert instance.requested_hours == [expected_hours]

    def test_native_resolution_15min_is_not_over_fetched(self, timezone):
        """A 15-min native provider is also asked for hours, not slots"""
        with self._at(timezone, 10, 20):
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=15,
                mock_forecast_func=lambda hours: {
                    i: 250.0 for i in range(hours * 4)}
            )
            forecast = instance.get_forecast(96)

            assert len(forecast) == 96
            assert instance.requested_hours == [25]

    def test_shorter_provider_data_is_passed_through(self, timezone):
        """A provider with less data still returns what it has"""
        with self._at(timezone, 10, 0):
            instance = ConcreteConsumptionForecast(
                timezone, target_resolution=15, native_resolution=60,
                mock_forecast_func=lambda hours: {
                    h: 1000.0 for h in range(min(hours, 5))}
            )
            forecast = instance.get_forecast(96)

            # 5 hours of data -> 20 quarter-hour slots
            assert len(forecast) == 20
