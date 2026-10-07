"""Tests for HomeAssistant consumption forecasting"""

import asyncio
import contextlib
import datetime
import gc
import json
import warnings
from unittest.mock import patch, AsyncMock
import pytz
import pytest
from src.batcontrol.async_utils import managed_event_loop
from src.batcontrol.forecastconsumption.forecast_homeassistant import (
    ForecastConsumptionHomeAssistant,
    MAX_FORECAST_HOURS
)


@pytest.fixture
def timezone():
    """Return Berlin timezone for testing"""
    return pytz.timezone('Europe/Berlin')


@pytest.fixture
def base_config(timezone):
    """Return basic configuration for HomeAssistant forecaster"""
    return {
        'base_url': 'http://localhost:8123',
        'api_token': 'test_token_12345',
        'entity_id': 'sensor.energy_consumption',
        'timezone': timezone,
        'history_days': [-7, -14],
        'history_weights': [2, 1],
        'cache_ttl_hours': 24.0
    }


@pytest.fixture
def mock_unit_check():
    """Mock the _check_sensor_unit method to avoid WebSocket connections in tests"""
    with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0):
        yield


class TestForecastConsumptionHomeAssistant:
    """Test cases for ForecastConsumptionHomeAssistant"""

    def test_initialization_default_params(self, timezone, mock_unit_check):
        """Test initialization with default parameters"""
        forecaster = ForecastConsumptionHomeAssistant(
            base_url='http://localhost:8123',
            api_token='test_token',
            entity_id='sensor.test',
            timezone=timezone
        )

        assert forecaster.base_url == 'http://localhost:8123'
        assert forecaster.api_token == 'test_token'
        assert forecaster.entity_id == 'sensor.test'
        assert forecaster.history_days == [-7, -14, -21]
        assert forecaster.history_weights == [1, 1, 1]
        assert forecaster.cache_ttl_hours == 48.0

    def test_initialization_custom_params(self, base_config, mock_unit_check):
        """Test initialization with custom parameters"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        assert forecaster.history_days == [-7, -14]
        assert forecaster.history_weights == [2, 1]
        assert forecaster.cache_ttl_hours == 24.0

    def test_initialization_mismatched_lengths(self, timezone, mock_unit_check):
        """Test that mismatched history_days and weights raises error"""
        with pytest.raises(ValueError, match="Length of history_days"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                history_days=[-7, -14, -21],
                history_weights=[1, 1]  # Wrong length
            )

    def test_initialization_invalid_weights(self, timezone, mock_unit_check):
        """Test that invalid weight values raise error"""
        with pytest.raises(ValueError, match="History weights must be between 1 and 10"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                history_days=[-7],
                history_weights=[15]  # Invalid weight
            )

    def test_get_cache_key(self, base_config, mock_unit_check):
        """Test cache key generation"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Monday (0) at 14:00
        key = forecaster._get_cache_key(0, 14)
        assert key == '0_14'

        # Tuesday (1) at 9:00
        key = forecaster._get_cache_key(1, 9)
        assert key == '1_9'

        # Sunday (6) at 23:00
        key = forecaster._get_cache_key(6, 23)
        assert key == '6_23'

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_success(self, mock_connect, base_config, mock_unit_check):
        """Test successful fetch and processing of hourly statistics"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket connection and messages
        mock_websocket = AsyncMock()

        # Simulate WebSocket message exchange
        mock_websocket.recv = AsyncMock(side_effect=[
            # 1. auth_required
            json.dumps({"type": "auth_required"}),
            # 2. auth_ok
            json.dumps({"type": "auth_ok"}),
            # 3. statistics response
            json.dumps({
                "id": 1,#
                "type": "result",
                "success": True,
                "result": {
                    'sensor.energy_consumption': [
                        {
                            'start': '2023-10-30T10:00:00+00:00',
                            'end': '2023-10-30T11:00:00+00:00',
                            'change': 100.5
                        },
                        {
                            'start': '2023-10-30T11:00:00+00:00',
                            'end': '2023-10-30T12:00:00+00:00',
                            'change': 110.2
                        }
                    ]
                }
            })
        ])

        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        # Make connect return an awaitable that resolves to the websocket
        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        result = forecaster._fetch_hourly_statistics(start, end)

        # Should return float (average consumption)
        assert isinstance(result, float)
        assert result > 0  # Should have positive consumption value
        assert mock_connect.called

        # Verify WebSocket was called with correct URL
        call_args = mock_connect.call_args
        assert 'ws://localhost:8123/api/websocket' in str(call_args)

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_api_error(self, mock_connect, base_config, mock_unit_check):
        """Test handling of API errors"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket connection failure
        async def raise_error(*args, **kwargs):
            raise Exception("Connection refused")

        mock_connect.side_effect = raise_error

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        with pytest.raises(RuntimeError, match="HomeAssistant WebSocket request failed"):
            forecaster._fetch_hourly_statistics(start, end)

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_no_data(self, mock_connect, base_config, mock_unit_check):
        """Test handling when no statistics data is available"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket with no data
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": {}  # Empty result
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        # Make connect return an awaitable that resolves to the websocket
        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        result = forecaster._fetch_hourly_statistics(start, end)

        # Should return -1 when no data available
        assert result == -1.0

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_negative_consumption(self, mock_connect, base_config, mock_unit_check):
        """Test handling of negative consumption (counter resets)"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket with negative consumption
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": {
                    'sensor.energy_consumption': [
                        {
                            'start': '2023-10-30T10:00:00+00:00',
                            'end': '2023-10-30T11:00:00+00:00',
                            'change': -50.0
                        },
                        {
                            'start': '2023-10-30T11:00:00+00:00',
                            'end': '2023-10-30T12:00:00+00:00',
                            'change': 100.0
                        }
                    ]
                }
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        # Make connect return an awaitable that resolves to the websocket
        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        result = forecaster._fetch_hourly_statistics(start, end)

        # Should skip negative values and return average of positive values only
        assert isinstance(result, float)
        assert result > 0  # Should only include the 100.0 value

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_unix_timestamps(self, mock_connect, base_config, mock_unit_check):
        """Test handling of Unix timestamps (seconds and milliseconds)"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket with Unix timestamp in seconds
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": {
                    'sensor.energy_consumption': [
                        {
                            'start': 1698667200,  # Unix timestamp in seconds
                            'end': 1698670800,
                            'change': 100.5
                        },
                        {
                            'start': 1698670800000,  # Unix timestamp in milliseconds
                            'end': 1698674400000,
                            'change': 110.2
                        }
                    ]
                }
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        # Make connect return an awaitable that resolves to the websocket
        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        result = forecaster._fetch_hourly_statistics(start, end)

        # Should handle both timestamp formats
        assert isinstance(result, float)
        assert result > 0  # Should have positive consumption average

    def test_update_cache_with_statistics(self, base_config, mock_unit_check):
        """Test cache update with weighted statistics"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Create sample data - dict mapping hour offset to consumption value
        # These represent average consumption values for specific hours
        history_periods = {0: 100.0, 1: 120.0, 2: 150.0}  # 3 hours of data

        # Use a fixed timestamp for testing (Monday 10:00)
        test_timestamp = datetime.datetime(2023, 10, 30, 10, 0, tzinfo=pytz.UTC)

        # Update cache
        updated_count = forecaster._update_cache_with_statistics(test_timestamp, history_periods)

        assert updated_count == 3  # Three hour slots updated

        # Check cache contents
        with forecaster._cache_lock:
            # Monday 10:00 (first hour, offset 0)
            assert '0_10' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['0_10'] - 100.0) < 0.1

            # Monday 11:00 (second hour, offset 1)
            assert '0_11' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['0_11'] - 120.0) < 0.1

            # Monday 12:00 (third hour, offset 2)
            assert '0_12' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['0_12'] - 150.0) < 0.1

    def test_update_cache_with_statistics_non_contiguous(self, base_config, mock_unit_check):
        """Test cache update with non-contiguous hour offsets (dict keys)"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Create sample data with non-contiguous hour offsets (gaps in the data)
        # This simulates the case where hours 38-40 need to be filled
        history_periods = {38: 200.0, 39: 220.0, 40: 250.0}

        # Use a fixed timestamp for testing (Monday 10:00)
        test_timestamp = datetime.datetime(2023, 10, 30, 10, 0, tzinfo=pytz.UTC)

        # Update cache
        updated_count = forecaster._update_cache_with_statistics(test_timestamp, history_periods)

        assert updated_count == 3  # Three hour slots updated

        # Check cache contents - hours 38, 39, 40 from Monday 10:00
        # Hour 38 = Monday 10:00 + 38 hours = Wednesday 0:00 (weekday 2, hour 0)
        # Hour 39 = Monday 10:00 + 39 hours = Wednesday 1:00 (weekday 2, hour 1)
        # Hour 40 = Monday 10:00 + 40 hours = Wednesday 2:00 (weekday 2, hour 2)
        with forecaster._cache_lock:
            assert '2_0' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['2_0'] - 200.0) < 0.1

            assert '2_1' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['2_1'] - 220.0) < 0.1

            assert '2_2' in forecaster.consumption_cache
            assert abs(forecaster.consumption_cache['2_2'] - 250.0) < 0.1

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_refresh_data(self, mock_connect, base_config, mock_unit_check):
        """Test data refresh functionality"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Mock WebSocket to return sample data
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            # Return data for each fetch call (multiple fetches will occur)
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": {
                    'sensor.energy_consumption': [
                        {
                            'start': '2023-10-30T10:00:00+00:00',
                            'change': 100.0
                        }
                    ]
                }
            })
        ] * 100)  # Repeat for multiple fetches
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        # Make connect return an awaitable that resolves to the websocket
        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        forecaster.refresh_data()

        # Check that cache was updated
        with forecaster._cache_lock:
            cache_size = len(forecaster.consumption_cache)
            assert cache_size > 0, "Cache should have been updated"

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data')
    def test_get_forecast_with_cache(self, mock_refresh, base_config, timezone, mock_unit_check):
        """Test forecast generation with cached data"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Get current time to populate cache with appropriate keys
        now = datetime.datetime.now(tz=timezone)

        # Populate cache with test data for the next few hours from now
        with forecaster._cache_lock:
            for h in range(5):
                future_time = now + datetime.timedelta(hours=h)
                key = forecaster._get_cache_key(future_time.weekday(), future_time.hour)
                forecaster.consumption_cache[key] = 50.0 + (h * 10.0)

        # Get forecast for 3 hours
        forecast = forecaster.get_forecast(3)

        # Should not trigger refresh since cache exists
        assert not mock_refresh.called

        assert len(forecast) == 3
        for h in range(3):
            assert h in forecast
            assert forecast[h] >= 0

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data_with_limit')
    def test_get_forecast_cache_miss(self, mock_refresh, base_config, timezone, mock_unit_check):
        """Test forecast generation triggers refresh on cache miss"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Set up mock to populate cache when called
        def populate_cache(hours):
            now = datetime.datetime.now(tz=timezone)
            with forecaster._cache_lock:
                key = forecaster._get_cache_key(now.weekday(), now.hour)
                forecaster.consumption_cache[key] = 100.0

        mock_refresh.side_effect = populate_cache

        # Get forecast with empty cache
        forecast = forecaster.get_forecast(1)

        # Should trigger refresh
        assert mock_refresh.called
        assert len(forecast) == 1

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data_with_limit')
    def test_get_forecast_stale_cache_triggers_refresh(
        self, mock_refresh, base_config, timezone, mock_unit_check
    ):
        """Test that cache with old entries still triggers refresh for missing future hours.

        This test verifies the fix for the issue where cache_size < hours was used
        incorrectly - old cache entries should not satisfy the requirement for
        specific future hour forecasts.
        """
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Get current time
        now = datetime.datetime.now(tz=timezone)

        # Add old/irrelevant cache entries for different hours than we need
        # This simulates having many cache entries but not the ones we need
        with forecaster._cache_lock:
            # Add entries for hours that are NOT the ones we'll need for forecast
            for day in range(7):
                for h in [0, 1, 2, 3]:  # Early morning hours
                    key = f"{day}_{h}"
                    forecaster.consumption_cache[key] = 100.0

        # Verify we have many cache entries
        with forecaster._cache_lock:
            cache_size = len(forecaster.consumption_cache)
        assert cache_size >= 28  # 7 days * 4 hours

        # Set up mock to populate required cache entries when called
        def populate_cache(hours):
            with forecaster._cache_lock:
                for h in range(hours):
                    future_time = now + datetime.timedelta(hours=h)
                    key = forecaster._get_cache_key(future_time.weekday(), future_time.hour)
                    forecaster.consumption_cache[key] = 100.0 + (h * 10.0)

        mock_refresh.side_effect = populate_cache

        # Request forecast - should trigger refresh despite having 28+ cache entries
        # because the specific future hours we need are not in the cache
        forecast = forecaster.get_forecast(5)

        # Should have triggered refresh because required keys were missing
        assert mock_refresh.called
        assert len(forecast) == 5

    def test_get_forecast_fallback_on_missing_key(self, base_config, timezone, mock_unit_check):
        """Test forecast stops when specific hour not in cache"""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Get current time
        now = datetime.datetime.now(tz=timezone)

        # Cache with limited data - only first 2 hours from now
        with forecaster._cache_lock:
            for h in range(2):
                future_time = now + datetime.timedelta(hours=h)
                key = forecaster._get_cache_key(future_time.weekday(), future_time.hour)
                forecaster.consumption_cache[key] = 100.0 + (h * 50.0)

        # Request forecast for 5 hours - should only return 2 hours
        forecast = forecaster.get_forecast(5)

        # Should only get 2 hours since we only cached 2 hours
        assert len(forecast) == 2
        # All values should be reasonable (not 0)
        for value in forecast.values():
            assert value > 0

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_check_sensor_unit_wh(self, mock_connect, timezone):
        """Test unit check for sensor with Wh unit"""
        # Mock WebSocket connection
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.energy_consumption",
                        "state": "1234.5",
                        "attributes": {
                            "unit_of_measurement": "Wh",
                            "friendly_name": "Energy Consumption"
                        }
                    }
                ]
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        forecaster = ForecastConsumptionHomeAssistant(
            base_url='http://localhost:8123',
            api_token='test_token',
            entity_id='sensor.energy_consumption',
            timezone=timezone
        )

        # Should have conversion factor of 1.0 for Wh
        assert forecaster.unit_conversion_factor == 1.0

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_check_sensor_unit_kwh(self, mock_connect, timezone):
        """Test unit check for sensor with kWh unit"""
        # Mock WebSocket connection
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.energy_consumption",
                        "state": "1.234",
                        "attributes": {
                            "unit_of_measurement": "kWh",
                            "friendly_name": "Energy Consumption"
                        }
                    }
                ]
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        forecaster = ForecastConsumptionHomeAssistant(
            base_url='http://localhost:8123',
            api_token='test_token',
            entity_id='sensor.energy_consumption',
            timezone=timezone
        )

        # Should have conversion factor of 1000.0 for kWh
        assert forecaster.unit_conversion_factor == 1000.0

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_check_sensor_unit_invalid(self, mock_connect, timezone):
        """Test unit check for sensor with invalid unit"""
        # Mock WebSocket connection
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.energy_consumption",
                        "state": "1234.5",
                        "attributes": {
                            "unit_of_measurement": "MWh",
                            "friendly_name": "Energy Consumption"
                        }
                    }
                ]
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        # Should raise ValueError for unsupported unit
        with pytest.raises(ValueError, match="Unsupported unit_of_measurement 'MWh'"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.energy_consumption',
                timezone=timezone
            )

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_check_sensor_unit_entity_not_found(self, mock_connect, timezone):
        """Test unit check when entity is not found"""
        # Mock WebSocket connection
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.other_sensor",
                        "state": "100",
                        "attributes": {
                            "unit_of_measurement": "Wh"
                        }
                    }
                ]
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        # Should raise RuntimeError when entity not found
        with pytest.raises(RuntimeError, match="Entity 'sensor.energy_consumption' not found"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.energy_consumption',
                timezone=timezone
            )

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_with_kwh_conversion(self, mock_connect, timezone):
        """Test that kWh values are correctly converted to Wh"""
        # Mock WebSocket connection for unit check
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            # Unit check
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [
                    {
                        "entity_id": "sensor.energy_consumption",
                        "state": "1.5",
                        "attributes": {
                            "unit_of_measurement": "kWh"
                        }
                    }
                ]
            }),
            # Statistics fetch
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": {
                    'sensor.energy_consumption': [
                        {
                            'start': '2023-10-30T10:00:00+00:00',
                            'end': '2023-10-30T11:00:00+00:00',
                            'change': 1.5  # 1.5 kWh should become 1500 Wh
                        },
                        {
                            'start': '2023-10-30T11:00:00+00:00',
                            'end': '2023-10-30T12:00:00+00:00',
                            'change': 2.0  # 2.0 kWh should become 2000 Wh
                        }
                    ]
                }
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        forecaster = ForecastConsumptionHomeAssistant(
            base_url='http://localhost:8123',
            api_token='test_token',
            entity_id='sensor.energy_consumption',
            timezone=timezone
        )

        # Verify conversion factor is set
        assert forecaster.unit_conversion_factor == 1000.0

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        result = forecaster._fetch_hourly_statistics(start, end)

        # Average of 1500 and 2000 Wh = 1750 Wh
        assert result == 1750.0

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_refresh_data_with_non_contiguous_missing_hours(
        self, mock_connect, base_config, timezone, mock_unit_check
    ):
        """Test that refresh correctly populates cache for non-contiguous missing hours.

        This test verifies the fix for the bug where cache was incorrectly populated
        when missing_periods started at a non-zero hour offset (e.g., hours 38-47).
        Previously, the code would store data for hours 0-9 instead of 38-47.
        """
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        # Get current time
        now = datetime.datetime.now(tz=timezone)
        now_full_hour = now.replace(minute=0, second=0, microsecond=0)

        # Pre-populate cache with data for first 38 hours (hours 0-37)
        with forecaster._cache_lock:
            for h in range(38):
                future_time = now + datetime.timedelta(hours=h)
                key = forecaster._get_cache_key(future_time.weekday(), future_time.hour)
                forecaster.consumption_cache[key] = 100.0 + h

        # Mock WebSocket to return sample data for missing hours
        mock_websocket = AsyncMock()

        # Build response list: auth_required, auth_ok, then data for each missing hour
        responses = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
        ]

        # Add responses for each of the missing hours (38-47) and each history_day (-7, -14)
        message_id = 1
        for h in range(38, 48):
            for history_day in base_config['history_days']:
                # Calculate the actual time being queried (for this hour + history_day offset)
                query_time = now_full_hour + datetime.timedelta(hours=h, days=history_day)
                responses.append(json.dumps({
                    "id": message_id,
                    "type": "result",
                    "success": True,
                    "result": {
                        'sensor.energy_consumption': [
                            {
                                'start': query_time.isoformat(),
                                'change': 200.0 + h  # Distinct value for each hour
                            }
                        ]
                    }
                }))
                message_id += 1

        mock_websocket.recv = AsyncMock(side_effect=responses)
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        # Call refresh_data_with_limit to fill missing hours
        forecaster.refresh_data_with_limit(48)

        # Verify that hours 38-47 are now in the cache with correct keys
        with forecaster._cache_lock:
            for h in range(38, 48):
                future_time = now + datetime.timedelta(hours=h)
                key = forecaster._get_cache_key(future_time.weekday(), future_time.hour)
                assert key in forecaster.consumption_cache, \
                    f"Hour {h} (key={key}) should be in cache but is missing"
                # The value should be around 200 + h (adjusted by multiplier if any)
                value = forecaster.consumption_cache[key]
                assert value > 200, \
                    f"Hour {h} (key={key}) should have value > 200, got {value}"


class TestSensorUnitConfiguration:
    """Test cases for sensor_unit configuration parameter (Issue #241)"""

    def test_sensor_unit_wh_skips_autodiscovery(self, timezone):
        """Test that sensor_unit='Wh' skips auto-discovery and sets factor to 1.0"""
        # Should NOT call _check_sensor_unit when sensor_unit is explicitly set
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit') as mock_check:
            forecaster = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='Wh'
            )

            # _check_sensor_unit should NOT be called
            mock_check.assert_not_called()

            # Conversion factor should be 1.0
            assert forecaster.unit_conversion_factor == 1.0
            assert forecaster.sensor_unit == 'wh'

    def test_sensor_unit_kwh_skips_autodiscovery(self, timezone):
        """Test that sensor_unit='kWh' skips auto-discovery and sets factor to 1000.0"""
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit') as mock_check:
            forecaster = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='kWh'
            )

            mock_check.assert_not_called()
            assert forecaster.unit_conversion_factor == 1000.0
            assert forecaster.sensor_unit == 'kwh'

    def test_sensor_unit_auto_performs_autodiscovery(self, timezone):
        """Test that sensor_unit='auto' triggers auto-discovery"""
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', 
                         return_value=1.0) as mock_check:
            forecaster = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='auto'
            )

            # _check_sensor_unit SHOULD be called for 'auto'
            mock_check.assert_called_once()
            assert forecaster.unit_conversion_factor == 1.0
            assert forecaster.sensor_unit == 'auto'

    def test_sensor_unit_case_insensitive(self, timezone):
        """Test that sensor_unit is case-insensitive"""
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit') as mock_check:
            # Test uppercase
            forecaster1 = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='WH'
            )
            assert forecaster1.sensor_unit == 'wh'
            assert forecaster1.unit_conversion_factor == 1.0

            # Test mixed case
            forecaster2 = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='KwH'
            )
            assert forecaster2.sensor_unit == 'kwh'
            assert forecaster2.unit_conversion_factor == 1000.0

            mock_check.assert_not_called()

    def test_sensor_unit_invalid_value_raises_error(self, timezone):
        """Test that invalid sensor_unit values raise ValueError"""
        with pytest.raises(ValueError, match="Invalid sensor_unit"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit='invalid'
            )

    def test_sensor_unit_empty_string_raises_error(self, timezone):
        """Test that empty string sensor_unit raises ValueError"""
        with pytest.raises(ValueError, match="Invalid sensor_unit"):
            ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone,
                sensor_unit=''
            )

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_websocket_connect_uses_4mb_limit(self, mock_connect, timezone):
        """Test that WebSocket connection uses 4MB max_size limit (Issue #241)"""
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', 
                         return_value=1.0):
            forecaster = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone
            )

        # Mock WebSocket for connection test
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"})
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        # Trigger a WebSocket connection
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(forecaster._websocket_connect())
        finally:
            loop.close()

        # Verify connect was called with max_size=4MB
        mock_connect.assert_called_once()
        call_kwargs = mock_connect.call_args.kwargs
        assert 'max_size' in call_kwargs
        assert call_kwargs['max_size'] == 4 * 1024 * 1024  # 4MB

    def test_sensor_unit_no_default_performs_autodiscovery(self, timezone):
        """Test that omitting sensor_unit parameter performs auto-discovery"""
        with patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit',
                         return_value=1.0) as mock_check:
            # Don't pass sensor_unit at all
            forecaster = ForecastConsumptionHomeAssistant(
                base_url='http://localhost:8123',
                api_token='test_token',
                entity_id='sensor.test',
                timezone=timezone
            )

            # Should call auto-discovery
            mock_check.assert_called_once()
            assert forecaster.sensor_unit == 'auto'
            assert forecaster.unit_conversion_factor == 1.0


class TestEventLoopHandling:
    """Regression tests for the sync-to-asyncio bridge (Python 3.14).

    Up to Python 3.13 asyncio.get_event_loop() silently created a loop; since
    3.14 it raises RuntimeError. The provider used to work around that by
    creating a loop it never closed, which stayed registered as the thread's
    current loop. These tests pin down that the provider now owns and releases
    its loops.
    """

    @staticmethod
    def _current_loop():
        """Return the thread's current event loop, or None if there is none."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

    @pytest.fixture(autouse=True)
    def _no_current_loop(self):
        """Start each test without a current event loop, as the daemon does."""
        asyncio.set_event_loop(None)
        yield
        asyncio.set_event_loop(None)

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_check_sensor_unit_leaves_no_current_loop(
            self, mock_connect, base_config):
        """_check_sensor_unit must not leave a loop registered behind."""
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({
                "id": 1,
                "type": "result",
                "success": True,
                "result": [{
                    "entity_id": base_config['entity_id'],
                    "state": "123",
                    "attributes": {"unit_of_measurement": "kWh"}
                }]
            })
        ])
        mock_websocket.send = AsyncMock()
        mock_websocket.close = AsyncMock()

        async def mock_connect_coro(*args, **kwargs):
            return mock_websocket

        mock_connect.side_effect = mock_connect_coro

        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        assert forecaster.unit_conversion_factor == 1000.0
        assert self._current_loop() is None

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_fetch_hourly_statistics_leaves_no_current_loop(
            self, mock_connect, base_config, mock_unit_check):
        """The private loop of _fetch_hourly_statistics must be released."""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        async def raise_error(*args, **kwargs):
            raise OSError("Connection refused")

        mock_connect.side_effect = raise_error

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        with pytest.raises(RuntimeError):
            forecaster._fetch_hourly_statistics(start, end)

        assert self._current_loop() is None

    def test_fetch_hourly_statistics_rejects_async_context(
            self, base_config, mock_unit_check):
        """The async-context guard used to be dead code and never fired.

        It raised RuntimeError inside the very try block that caught
        RuntimeError, so the call silently fell through to asyncio.run().
        """
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        start = datetime.datetime(2025, 10, 27, 0, 0, tzinfo=pytz.UTC)
        end = datetime.datetime(2025, 10, 28, 0, 0, tzinfo=pytz.UTC)

        async def call_from_async_context():
            with pytest.raises(RuntimeError, match="running event loop"):
                forecaster._fetch_hourly_statistics(start, end)

        # The old code let the coroutine fall through to asyncio.run(), which
        # abandoned it and emitted "coroutine was never awaited".
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            asyncio.run(call_from_async_context())
            gc.collect()

    @patch('src.batcontrol.forecastconsumption.forecast_homeassistant.connect')
    def test_refresh_data_closes_its_loop(
            self, mock_connect, base_config, mock_unit_check):
        """refresh_data_with_limit must close the loop it opened."""
        forecaster = ForecastConsumptionHomeAssistant(**base_config)

        async def raise_error(*args, **kwargs):
            raise OSError("Connection refused")

        mock_connect.side_effect = raise_error

        captured = {}
        real_managed_event_loop = managed_event_loop

        @contextlib.contextmanager
        def spy():
            with real_managed_event_loop() as loop:
                captured['loop'] = loop
                yield loop

        with patch(
            'src.batcontrol.forecastconsumption.forecast_homeassistant'
            '.managed_event_loop',
            spy
        ):
            forecaster.refresh_data_with_limit(2)

        assert captured['loop'].is_closed()
        assert self._current_loop() is None


class TestForecastHorizonLimit:
    """Tests for the MAX_FORECAST_HOURS limit (issue #446)"""

    @pytest.fixture
    def forecaster(self, base_config, mock_unit_check):
        """Forecaster with a fully populated cache"""
        instance = ForecastConsumptionHomeAssistant(**base_config)
        with instance._cache_lock:
            for weekday in range(7):
                for hour in range(24):
                    key = instance._get_cache_key(weekday, hour)
                    instance.consumption_cache[key] = 100.0
        return instance

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data_with_limit')
    def test_native_forecast_is_limited(self, mock_refresh, forecaster):
        """A request beyond MAX_FORECAST_HOURS is cut down, not forwarded"""
        prediction = forecaster._get_forecast_native(MAX_FORECAST_HOURS + 52)

        assert len(prediction) == MAX_FORECAST_HOURS
        assert not mock_refresh.called

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data_with_limit')
    def test_native_forecast_below_limit_is_untouched(
            self, mock_refresh, forecaster):
        """A request within the limit is served in full"""
        prediction = forecaster._get_forecast_native(24)

        assert len(prediction) == 24
        assert not mock_refresh.called

    @patch.object(ForecastConsumptionHomeAssistant, 'refresh_data_with_limit')
    def test_refresh_is_limited_on_cache_miss(self, mock_refresh, base_config,
                                              mock_unit_check):
        """A cold cache does not trigger a refresh beyond the limit"""
        instance = ForecastConsumptionHomeAssistant(**base_config)

        def populate_cache(hours):
            now = datetime.datetime.now(tz=instance.timezone)
            with instance._cache_lock:
                for h in range(hours):
                    future_time = now + datetime.timedelta(hours=h)
                    key = instance._get_cache_key(
                        future_time.weekday(), future_time.hour)
                    instance.consumption_cache[key] = 100.0

        mock_refresh.side_effect = populate_cache

        instance._get_forecast_native(96)

        mock_refresh.assert_called_once_with(MAX_FORECAST_HOURS)
