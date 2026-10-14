"""Tests for consumption forecast factory (Issue #241)"""

from unittest.mock import patch
import pytest
import pytz
from src.batcontrol.forecastconsumption.consumption import (
    Consumption,
    _create_homeassistant_forecast
)
from src.batcontrol.forecastconsumption.forecast_homeassistant import (
    ForecastConsumptionHomeAssistant
)


@pytest.fixture
def timezone():
    """Return Berlin timezone for testing"""
    return pytz.timezone('Europe/Berlin')


class TestConsumptionFactory:
    """Test cases for Consumption factory with sensor_unit support"""

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_with_sensor_unit_wh(self, mock_check, timezone):
        """Test factory creates HomeAssistant forecast with sensor_unit='Wh'"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'Wh'
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.sensor_unit == 'wh'
        assert forecaster.unit_conversion_factor == 1.0
        # Should NOT call _check_sensor_unit when explicitly set
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1000.0)
    def test_create_homeassistant_with_sensor_unit_kwh(self, mock_check, timezone):
        """Test factory creates HomeAssistant forecast with sensor_unit='kWh'"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'kWh'
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.sensor_unit == 'kwh'
        assert forecaster.unit_conversion_factor == 1000.0
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_with_sensor_unit_auto(self, mock_check, timezone):
        """Test factory creates HomeAssistant forecast with sensor_unit='auto'"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'auto'
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.sensor_unit == 'auto'
        # SHOULD call _check_sensor_unit for 'auto'
        mock_check.assert_called_once()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_without_sensor_unit(self, mock_check, timezone):
        """Test factory creates HomeAssistant forecast without sensor_unit (default behavior)"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test'
                # No sensor_unit specified
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.sensor_unit == 'auto'
        # SHOULD call _check_sensor_unit when not specified
        mock_check.assert_called_once()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_flat_config_with_sensor_unit(self, mock_check, timezone):
        """Test factory handles flat config structure (HomeAssistant addon quirk)"""
        # HomeAssistant addon doesn't support 3-level nesting, so config is flat
        config = {
            'type': 'homeassistant-api',
            'base_url': 'http://localhost:8123',
            'apitoken': 'test_token',
            'entity_id': 'sensor.test',
            'sensor_unit': 'kWh'
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.sensor_unit == 'kwh'
        assert forecaster.unit_conversion_factor == 1000.0
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_sensor_unit_case_insensitive(self, mock_check, timezone):  # pylint: disable=redefined-outer-name
        """Test that sensor_unit is handled case-insensitively in factory"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'WH'  # Uppercase
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert forecaster.sensor_unit == 'wh'
        assert forecaster.unit_conversion_factor == 1.0
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_create_homeassistant_with_all_params_including_sensor_unit(self, mock_check, timezone):
        """Test factory passes sensor_unit along with other parameters"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'history_days': [-7, -14],
                'history_weights': [2, 1],
                'cache_ttl_hours': 24.0,
                'multiplier': 1.5,
                'sensor_unit': 'Wh'
            }
        }

        forecaster = Consumption.create_consumption(timezone, config)

        assert isinstance(forecaster, ForecastConsumptionHomeAssistant)
        assert forecaster.history_days == [-7, -14]
        assert forecaster.history_weights == [2, 1]
        assert forecaster.cache_ttl_hours == 24.0
        assert forecaster.multiplier == 1.5
        assert forecaster.sensor_unit == 'wh'
        assert forecaster.unit_conversion_factor == 1.0
        mock_check.assert_not_called()


class TestCreateHomeAssistantForecast:
    """Test _create_homeassistant_forecast helper function directly"""

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_sensor_unit_extracted_from_nested_config(self, mock_check, timezone):
        """Test sensor_unit is correctly extracted from nested config"""
        config = {
            'homeassistant_api': {
                'base_url': 'http://localhost:8123',
                'apitoken': 'test_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'kWh'
            }
        }

        forecaster = _create_homeassistant_forecast(timezone, config)

        assert forecaster.sensor_unit == 'kwh'
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_sensor_unit_extracted_from_flat_config(self, mock_check, timezone):  # pylint: disable=redefined-outer-name
        """Test sensor_unit is correctly extracted from flat config"""
        config = {
            'base_url': 'http://localhost:8123',
            'apitoken': 'test_token',
            'entity_id': 'sensor.test',
            'sensor_unit': 'Wh'
        }

        forecaster = _create_homeassistant_forecast(timezone, config)

        assert forecaster.sensor_unit == 'wh'
        mock_check.assert_not_called()

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_sensor_unit_defaults_to_none(self, mock_check, timezone):  # pylint: disable=redefined-outer-name
        """Test sensor_unit defaults to None when not provided"""
        config = {
            'base_url': 'http://localhost:8123',
            'apitoken': 'test_token',
            'entity_id': 'sensor.test'
        }

        forecaster = _create_homeassistant_forecast(timezone, config)

        assert forecaster.sensor_unit == 'auto'
        mock_check.assert_called_once()


class TestConfigKeyNames:
    """Canonical 'url'/'api_token' keys and their deprecated spellings (#451)"""

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_canonical_keys(self, mock_check, timezone):  # pylint: disable=redefined-outer-name,unused-argument
        """url/api_token are the documented key names"""
        config = {
            'type': 'homeassistant-api',
            'homeassistant_api': {
                'url': 'ws://localhost:8123',
                'api_token': 'canonical_token',
                'entity_id': 'sensor.test',
                'sensor_unit': 'Wh'
            }
        }

        forecaster = _create_homeassistant_forecast(timezone, config['homeassistant_api'])

        assert forecaster.base_url == 'ws://localhost:8123'
        assert forecaster.api_token == 'canonical_token'

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_deprecated_keys_still_work(self, mock_check, timezone):  # pylint: disable=redefined-outer-name,unused-argument
        """base_url/apitoken keep working for hand-written configurations"""
        config = {
            'base_url': 'ws://legacy:8123',
            'apitoken': 'legacy_token',
            'entity_id': 'sensor.test',
            'sensor_unit': 'Wh'
        }

        forecaster = _create_homeassistant_forecast(timezone, config)

        assert forecaster.base_url == 'ws://legacy:8123'
        assert forecaster.api_token == 'legacy_token'

    @patch.object(ForecastConsumptionHomeAssistant, '_check_sensor_unit', return_value=1.0)
    def test_canonical_key_wins_over_deprecated(self, mock_check, timezone):  # pylint: disable=redefined-outer-name,unused-argument
        config = {
            'url': 'ws://canonical:8123',
            'base_url': 'ws://legacy:8123',
            'api_token': 'canonical_token',
            'apitoken': 'legacy_token',
            'entity_id': 'sensor.test',
            'sensor_unit': 'Wh'
        }

        forecaster = _create_homeassistant_forecast(timezone, config)

        assert forecaster.base_url == 'ws://canonical:8123'
        assert forecaster.api_token == 'canonical_token'

    @pytest.mark.parametrize("missing", ['url', 'api_token', 'entity_id'])
    def test_missing_key_raises_naming_it(self, timezone, missing):  # pylint: disable=redefined-outer-name
        config = {
            'url': 'ws://localhost:8123',
            'api_token': 'test_token',
            'entity_id': 'sensor.test',
            'sensor_unit': 'Wh'
        }
        del config[missing]

        with pytest.raises(ValueError, match=missing):
            _create_homeassistant_forecast(timezone, config)

    @pytest.mark.parametrize("empty", [None, ''])
    def test_empty_token_raises(self, timezone, empty):  # pylint: disable=redefined-outer-name
        """The add-on delivers a cleared field as '' and an unset one as null.

        Regression for issue #451: the presence-only check let both through
        and HomeAssistant answered with "expected str at 'access_token'".
        """
        config = {
            'url': 'ws://localhost:8123',
            'api_token': empty,
            'entity_id': 'sensor.test',
            'sensor_unit': 'Wh'
        }

        with pytest.raises(ValueError, match='api_token'):
            _create_homeassistant_forecast(timezone, config)
