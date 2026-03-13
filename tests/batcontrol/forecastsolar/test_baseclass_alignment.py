"""Tests for ForecastSolarBase class Full-Hour Alignment functionality."""
import pytest
from unittest.mock import patch
import datetime
import pytz

from batcontrol.forecastsolar.baseclass import ForecastSolarBaseclass


class MockSolarProvider(ForecastSolarBaseclass):
    """Mock solar provider for testing baseclass functionality."""

    def __init__(self, pvinstallations, timezone, min_time_between_API_calls,
                 delay_evaluation_by_seconds, target_resolution=60, native_resolution=60):
        super().__init__(
            pvinstallations, timezone, min_time_between_API_calls,
            delay_evaluation_by_seconds, target_resolution, native_resolution
        )
        self.mock_data = {}

    def set_mock_data(self, data):
        """Set mock data to be returned by get_forecast_from_raw_data."""
        self.mock_data = data

    def get_raw_data_from_provider(self, pvinstallation_name):
        """Mock implementation."""
        return {'mock': 'data'}

    def get_forecast_from_raw_data(self):
        """Return mock data."""
        return self.mock_data


class TestResolutionConversion:
    """Tests for resolution conversion functionality."""

    @pytest.fixture
    def pvinstallations(self):
        return [{'name': 'test_pv'}]

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_no_conversion_when_resolutions_match(self, pvinstallations, timezone):
        """Test that no conversion happens when native and target match."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=60
        )

        hourly_data = {0: 1000, 1: 1500, 2: 2000}
        result = provider._convert_resolution(hourly_data)

        # Should return unchanged
        assert result == hourly_data

    def test_upsample_60_to_15(self, pvinstallations, timezone):
        """Test upsampling from 60-min to 15-min."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=60
        )

        hourly_data = {0: 1000, 1: 2000}
        result = provider._convert_resolution(hourly_data)

        # Should have 4x as many intervals
        assert len(result) >= 4
        # First interval should be part of first hour
        assert 0 in result
        assert 1 in result
        assert 2 in result
        assert 3 in result

    def test_downsample_15_to_60(self, pvinstallations, timezone):
        """Test downsampling from 15-min to 60-min."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=15
        )

        data_15min = {
            0: 250, 1: 300, 2: 350, 3: 400,  # Hour 0
            4: 500, 5: 500, 6: 500, 7: 500,  # Hour 1
        }
        result = provider._convert_resolution(data_15min)

        # Should have hourly data
        assert result[0] == 1300  # Sum of quarters
        assert result[1] == 2000


class TestCurrentIntervalShifting:
    """Tests for shifting indices to current interval."""

    @pytest.fixture
    def pvinstallations(self):
        return [{'name': 'test_pv'}]

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_shift_at_hour_start_60min(self, pvinstallations, timezone):
        """Test shifting when at the start of an hour (60-min resolution)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=60
        )

        # Mock time at 10:00:00 (start of hour)
        mock_time = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            hour_aligned = {0: 1000, 1: 1500, 2: 2000, 3: 2500}
            result = provider._shift_to_current_interval(hour_aligned)

        # At hour start, no shift needed
        assert result == hour_aligned

    def test_shift_at_20_minutes_60min(self, pvinstallations, timezone):
        """Test shifting at 20 minutes past the hour (60-min resolution)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=60
        )

        # Mock time at 10:20:00
        mock_time = datetime.datetime(2024, 1, 1, 10, 20, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            hour_aligned = {0: 1000, 1: 1500, 2: 2000, 3: 2500}
            result = provider._shift_to_current_interval(hour_aligned)

        # At 10:20 with 60-min resolution, current_interval_in_hour = 20//60 = 0
        # So no shift (still in first interval of hour)
        assert result == hour_aligned

    def test_shift_at_hour_start_15min(self, pvinstallations, timezone):
        """Test shifting at hour start with 15-min resolution."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=15
        )

        # Mock time at 10:00:00
        mock_time = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            hour_aligned = {
                0: 250,   # 10:00-10:15
                1: 300,   # 10:15-10:30
                2: 350,   # 10:30-10:45
                3: 400,   # 10:45-11:00
                4: 450,   # 11:00-11:15
            }
            result = provider._shift_to_current_interval(hour_aligned)

        # At hour start, no shift
        assert result == hour_aligned

    def test_shift_at_20_minutes_15min(self, pvinstallations, timezone):
        """Test shifting at 20 minutes with 15-min resolution (key test case)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=15
        )

        # Mock time at 10:20:30
        mock_time = datetime.datetime(2024, 1, 1, 10, 20, 30, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            hour_aligned = {
                0: 250,   # 10:00-10:15 (PAST)
                1: 300,   # 10:15-10:30 (CURRENT - we're at 10:20)
                2: 350,   # 10:30-10:45
                3: 400,   # 10:45-11:00
                4: 450,   # 11:00-11:15
            }
            result = provider._shift_to_current_interval(hour_aligned)

        # At 10:20, current_interval_in_hour = 20//15 = 1
        # Should shift by 1: drop [0], renumber [1]->[0], [2]->[1], etc.
        expected = {
            0: 300,   # Was [1]: 10:15-10:30 (current)
            1: 350,   # Was [2]: 10:30-10:45
            2: 400,   # Was [3]: 10:45-11:00
            3: 450,   # Was [4]: 11:00-11:15
        }
        assert result == expected

    def test_shift_at_35_minutes_15min(self, pvinstallations, timezone):
        """Test shifting at 35 minutes with 15-min resolution."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=15
        )

        # Mock time at 10:35:00
        mock_time = datetime.datetime(2024, 1, 1, 10, 35, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            hour_aligned = {
                0: 250,   # 10:00-10:15 (PAST)
                1: 300,   # 10:15-10:30 (PAST)
                2: 350,   # 10:30-10:45 (CURRENT - we're at 10:35)
                3: 400,   # 10:45-11:00
                4: 450,   # 11:00-11:15
            }
            result = provider._shift_to_current_interval(hour_aligned)

        # At 10:35, current_interval_in_hour = 35//15 = 2
        # Should shift by 2: drop [0] and [1], renumber [2]->[0], [3]->[1], etc.
        expected = {
            0: 350,   # Was [2]: 10:30-10:45 (current)
            1: 400,   # Was [3]: 10:45-11:00
            2: 450,   # Was [4]: 11:00-11:15
        }
        assert result == expected


class TestFullGetForecastIntegration:
    """Integration tests for the complete get_forecast flow."""

    @pytest.fixture
    def pvinstallations(self):
        return [{'name': 'test_pv'}]

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_hourly_provider_hourly_target(self, pvinstallations, timezone):
        """Test hourly provider with hourly target (no conversion)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=60
        )

        # Set mock data (hour-aligned)
        hourly_data = {i: 1000 + i*100 for i in range(24)}  # 24 hours
        provider.set_mock_data(hourly_data)

        # Mock time at 10:00
        mock_time = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                result = provider.get_forecast()

        # Should return data without modification (at hour start)
        assert len(result) == 24
        assert result[0] == 1000

    def test_hourly_provider_15min_target(self, pvinstallations, timezone):
        """Test hourly provider with 15-min target (upsampling)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=60
        )

        # Set mock hourly data
        hourly_data = {i: 1000 for i in range(24)}
        provider.set_mock_data(hourly_data)

        # Mock time at 10:20
        mock_time = datetime.datetime(2024, 1, 1, 10, 20, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                result = provider.get_forecast()

        # Should have upsampled to 15-min and shifted
        # At 10:20, current_interval = 1, so [0] should be interval starting at 10:15
        assert len(result) > 24  # More than 24 intervals
        # With constant 1000 Wh per hour and linear interpolation
        # Each quarter should be 250 Wh
        assert result[0] == pytest.approx(250, rel=0.01)

    def test_minimum_forecast_validation_hourly(self, pvinstallations, timezone):
        """Test that minimum forecast length is validated (hourly)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=60, native_resolution=60
        )

        # Set insufficient data (less than 18 hours)
        hourly_data = {i: 1000 for i in range(10)}
        provider.set_mock_data(hourly_data)

        mock_time = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                with pytest.raises(RuntimeError, match="Less than 12 hours"):
                    provider.get_forecast()

    def test_minimum_forecast_validation_15min(self, pvinstallations, timezone):
        """Test that minimum forecast length is validated (15-min)."""
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=15
        )

        # Set insufficient data (less than 48 intervals = 12 hours)
        data_15min = {i: 250 for i in range(40)}
        provider.set_mock_data(data_15min)

        mock_time = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                with pytest.raises(RuntimeError, match="Less than 12 hours"):
                    provider.get_forecast()


class TestRealWorldScenario:
    """Tests simulating real-world scenarios."""

    @pytest.fixture
    def pvinstallations(self):
        return [{'name': 'test_pv'}]

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_scenario_1020_with_15min(self, pvinstallations, timezone):
        """
        Real scenario: Time is 10:20:30, 15-min resolution
        Provider returns hour-aligned data
        Expected: [0] should be the 10:15-10:30 interval
        """
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=60
        )

        # Typical solar pattern
        hourly_data = {
            0: 0,     # 10:00
            1: 500,   # 11:00
            2: 1500,  # 12:00
            3: 2500,  # 13:00
            4: 3000,  # 14:00
            5: 2500,  # 15:00
            # ... more hours
        }
        for i in range(6, 24):
            hourly_data[i] = 1000

        provider.set_mock_data(hourly_data)

        # Mock time at 10:20:30
        mock_time = datetime.datetime(2024, 1, 1, 10, 20, 30, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                result = provider.get_forecast()

        # At 10:20, we're in interval 1 of the hour (10:15-10:30)
        # After upsampling and shifting, [0] should represent this interval
        assert 0 in result
        assert result[0] > 0  # Should have some solar production

        # Result should have many intervals (at least 48 for 12 hours)
        assert len(result) >= 48

    def test_scenario_matching_doc_example(self, pvinstallations, timezone):
        """
        Test the exact scenario from documentation:
        Time: 10:20:30
        Provider returns: [0]=10:00-10:15 (250Wh), [1]=10:15-10:30 (300Wh), ...
        Expected output: [0]=10:15-10:30 (300Wh), [1]=10:30-10:45 (350Wh), ...
        """
        provider = MockSolarProvider(
            pvinstallations, timezone, 900, 15,
            target_resolution=15, native_resolution=15
        )

        # Hour-aligned 15-min data from documentation
        data_15min = {
            0: 250,   # 10:00-10:15
            1: 300,   # 10:15-10:30
            2: 350,   # 10:30-10:45
            3: 400,   # 10:45-11:00
            4: 450,   # 11:00-11:15
        }
        # Add more intervals to meet minimum forecast requirement
        for i in range(5, 80):
            data_15min[i] = 500

        provider.set_mock_data(data_15min)

        # Mock time at 10:20:30
        mock_time = datetime.datetime(2024, 1, 1, 10, 20, 30, tzinfo=timezone)

        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = mock_time
            mock_datetime.timezone = datetime.timezone

            with patch.object(provider, 'refresh_data'):
                result = provider.get_forecast()

        # Verify the shift happened correctly
        assert result[0] == 300   # Was [1]: 10:15-10:30 (current)
        assert result[1] == 350   # Was [2]: 10:30-10:45
        assert result[2] == 400   # Was [3]: 10:45-11:00
        assert result[3] == 450   # Was [4]: 11:00-11:15

        # Original [0] (10:00-10:15) should NOT be in result (it's in the past)
        # So result should not contain 250 at the beginning
        assert result[0] != 250
