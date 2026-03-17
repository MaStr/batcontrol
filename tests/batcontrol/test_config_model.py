"""Tests for Pydantic configuration models."""
import pytest
from pydantic import ValidationError
from batcontrol.config_model import (
    BatcontrolConfig,
    BatteryControlConfig,
    BatteryControlExpertConfig,
    InverterConfig,
    UtilityConfig,
    MqttConfig,
    EvccConfig,
    PvInstallationConfig,
    ConsumptionForecastConfig,
    validate_config,
)


class TestBatcontrolConfig:
    """Tests for the top-level BatcontrolConfig model."""

    def _minimal_config(self):
        """Return a minimal valid config dict."""
        return {
            'timezone': 'Europe/Berlin',
            'utility': {'type': 'awattar_de'},
            'pvinstallations': [{'name': 'Test PV', 'kWp': 10.0}],
        }

    def test_minimal_config(self):
        """Test that a minimal config is valid."""
        cfg = BatcontrolConfig(**self._minimal_config())
        assert cfg.timezone == 'Europe/Berlin'
        assert cfg.time_resolution_minutes == 60

    def test_defaults_applied(self):
        """Test that defaults are applied for missing fields."""
        cfg = BatcontrolConfig(**self._minimal_config())
        assert cfg.loglevel == 'info'
        assert cfg.logfile_enabled is True
        assert cfg.log_everything is False
        assert cfg.max_logfile_size == 200
        assert cfg.solar_forecast_provider == 'fcsolarapi'

    def test_time_resolution_string_coercion(self):
        """Test that string time_resolution_minutes is coerced to int."""
        data = self._minimal_config()
        data['time_resolution_minutes'] = '15'
        cfg = BatcontrolConfig(**data)
        assert cfg.time_resolution_minutes == 15
        assert isinstance(cfg.time_resolution_minutes, int)

    def test_time_resolution_invalid_value(self):
        """Test that invalid time_resolution_minutes raises error."""
        data = self._minimal_config()
        data['time_resolution_minutes'] = 30
        with pytest.raises(ValidationError, match='time_resolution_minutes'):
            BatcontrolConfig(**data)

    def test_time_resolution_valid_values(self):
        """Test both valid time resolution values."""
        for val in [15, 60, '15', '60']:
            data = self._minimal_config()
            data['time_resolution_minutes'] = val
            cfg = BatcontrolConfig(**data)
            assert cfg.time_resolution_minutes in (15, 60)

    def test_extra_fields_preserved(self):
        """Test that unknown fields are preserved (extra='allow')."""
        data = self._minimal_config()
        data['custom_field'] = 'custom_value'
        cfg = BatcontrolConfig(**data)
        assert cfg.custom_field == 'custom_value'

    def test_loglevel_valid_values(self):
        """Test all valid loglevel values."""
        for level in ['debug', 'info', 'warning', 'error', 'DEBUG', 'Info']:
            data = self._minimal_config()
            data['loglevel'] = level
            cfg = BatcontrolConfig(**data)
            assert cfg.loglevel == level.lower()

    def test_loglevel_invalid_value(self):
        """Test that invalid loglevel raises error."""
        data = self._minimal_config()
        data['loglevel'] = 'verbose'
        with pytest.raises(ValidationError, match='loglevel'):
            BatcontrolConfig(**data)

    def test_loglevel_normalized_to_lowercase(self):
        """Test that loglevel is normalized to lowercase."""
        data = self._minimal_config()
        data['loglevel'] = 'DEBUG'
        cfg = BatcontrolConfig(**data)
        assert cfg.loglevel == 'debug'


class TestBatteryControlConfig:
    """Tests for BatteryControlConfig."""

    def test_defaults(self):
        cfg = BatteryControlConfig()
        assert cfg.min_price_difference == 0.05
        assert cfg.always_allow_discharge_limit == 0.90

    def test_string_float_coercion(self):
        """Test that string floats are coerced."""
        cfg = BatteryControlConfig(
            min_price_difference='0.03',
            always_allow_discharge_limit='0.85',
        )
        assert cfg.min_price_difference == 0.03
        assert isinstance(cfg.min_price_difference, float)


class TestInverterConfig:
    """Tests for InverterConfig."""

    def test_defaults(self):
        cfg = InverterConfig()
        assert cfg.type == 'dummy'
        assert cfg.max_grid_charge_rate == 5000
        assert cfg.enable_resilient_wrapper is False

    def test_string_numeric_coercion(self):
        """Test that string numerics are coerced (HA addon issue)."""
        cfg = InverterConfig(
            max_grid_charge_rate='3000',
            outage_tolerance_minutes='30',
        )
        assert cfg.max_grid_charge_rate == 3000.0
        assert cfg.outage_tolerance_minutes == 30.0

    def test_legacy_max_charge_rate_rename(self):
        """Test backward compat: max_charge_rate -> max_grid_charge_rate."""
        cfg = InverterConfig(max_charge_rate=4000)
        assert cfg.max_grid_charge_rate == 4000.0

    def test_max_grid_charge_rate_takes_precedence(self):
        """Test that max_grid_charge_rate is used when both are present."""
        cfg = InverterConfig(
            max_charge_rate=4000,
            max_grid_charge_rate=3000,
        )
        assert cfg.max_grid_charge_rate == 3000.0

    def test_cache_ttl_coercion(self):
        """Test that cache_ttl string is coerced to int (MQTT inverter)."""
        cfg = InverterConfig(cache_ttl='120')
        assert cfg.cache_ttl == 120
        assert isinstance(cfg.cache_ttl, int)


class TestUtilityConfig:
    """Tests for UtilityConfig."""

    def test_type_required(self):
        with pytest.raises(ValidationError):
            UtilityConfig()

    def test_string_float_coercion(self):
        cfg = UtilityConfig(
            type='awattar_de',
            vat='0.19',
            fees='0.015',
            markup='0.03',
        )
        assert cfg.vat == 0.19
        assert isinstance(cfg.vat, float)

    def test_tariff_zone_coercion(self):
        cfg = UtilityConfig(
            type='tariff_zones',
            tariff_zone_1='0.2733',
            tariff_zone_2='0.1734',
        )
        assert cfg.tariff_zone_1 == 0.2733
        assert isinstance(cfg.tariff_zone_1, float)


class TestMqttConfig:
    """Tests for MqttConfig."""

    def test_port_string_coercion(self):
        """Test the critical HA addon bug: port arrives as string."""
        cfg = MqttConfig(port='1883')
        assert cfg.port == 1883
        assert isinstance(cfg.port, int)

    def test_retry_string_coercion(self):
        cfg = MqttConfig(retry_attempts='3', retry_delay='5')
        assert cfg.retry_attempts == 3
        assert cfg.retry_delay == 5

    def test_defaults(self):
        cfg = MqttConfig()
        assert cfg.enabled is False
        assert cfg.port == 1883
        assert cfg.broker == 'localhost'


class TestEvccConfig:
    """Tests for EvccConfig."""

    def test_port_string_coercion(self):
        """Test the critical HA addon bug: port arrives as string."""
        cfg = EvccConfig(port='1883')
        assert cfg.port == 1883
        assert isinstance(cfg.port, int)

    def test_loadpoint_topic_string(self):
        cfg = EvccConfig(loadpoint_topic='evcc/loadpoints/1/charging')
        assert cfg.loadpoint_topic == 'evcc/loadpoints/1/charging'

    def test_loadpoint_topic_list(self):
        cfg = EvccConfig(
            loadpoint_topic=[
                'evcc/loadpoints/1/charging',
                'evcc/loadpoints/2/charging',
            ]
        )
        assert isinstance(cfg.loadpoint_topic, list)
        assert len(cfg.loadpoint_topic) == 2


class TestPvInstallationConfig:
    """Tests for PvInstallationConfig."""

    def test_float_coercion(self):
        """Test that numeric PV fields are coerced from strings."""
        cfg = PvInstallationConfig(
            name='Test',
            lat='48.43',
            lon='8.77',
            kWp='10.5',
            declination='30',
            azimuth='-90',
        )
        assert cfg.lat == 48.43
        assert cfg.kWp == 10.5
        assert cfg.azimuth == -90.0
        assert isinstance(cfg.lat, float)


class TestConsumptionForecastConfig:
    """Tests for ConsumptionForecastConfig."""

    def test_defaults(self):
        cfg = ConsumptionForecastConfig()
        assert cfg.type == 'csv'

    def test_annual_consumption_coercion(self):
        cfg = ConsumptionForecastConfig(annual_consumption='4500')
        assert cfg.annual_consumption == 4500.0

    def test_history_days_semicolon_string(self):
        """Test HA addon semicolon-separated string parsing."""
        cfg = ConsumptionForecastConfig(history_days='-7;-14;-21')
        assert cfg.history_days == [-7, -14, -21]
        assert all(isinstance(x, int) for x in cfg.history_days)

    def test_history_weights_semicolon_string(self):
        """Test HA addon semicolon-separated string parsing."""
        cfg = ConsumptionForecastConfig(history_weights='1;1;1')
        assert cfg.history_weights == [1, 1, 1]

    def test_history_days_list_passthrough(self):
        """Test that regular lists are preserved and items coerced to int."""
        cfg = ConsumptionForecastConfig(history_days=[-7, -14, -21])
        assert cfg.history_days == [-7, -14, -21]

    def test_history_days_string_list_coercion(self):
        """Test that list of strings is coerced to list of ints."""
        cfg = ConsumptionForecastConfig(history_days=['-7', '-14', '-21'])
        assert cfg.history_days == [-7, -14, -21]

    def test_history_days_none(self):
        """Test that None is preserved."""
        cfg = ConsumptionForecastConfig(history_days=None)
        assert cfg.history_days is None


class TestValidateConfig:
    """Tests for the validate_config() function."""

    def _full_config(self):
        """Return a full config dict similar to batcontrol_config_dummy.yaml."""
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'loglevel': 'debug',
            'logfile_enabled': True,
            'log_everything': False,
            'max_logfile_size': 200,
            'logfile_path': 'logs/batcontrol.log',
            'battery_control': {
                'min_price_difference': 0.05,
                'min_price_difference_rel': 0.10,
                'always_allow_discharge_limit': 0.90,
                'max_charging_from_grid_limit': 0.89,
                'min_recharge_amount': 100,
            },
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
            },
            'utility': {
                'type': 'awattar_de',
                'vat': 0.19,
                'fees': 0.015,
                'markup': 0.03,
            },
            'pvinstallations': [
                {
                    'name': 'Test PV',
                    'lat': 48.43,
                    'lon': 8.77,
                    'kWp': 10.0,
                    'declination': 30,
                    'azimuth': 0,
                }
            ],
            'consumption_forecast': {
                'type': 'csv',
                'csv': {
                    'annual_consumption': 4500,
                    'load_profile': 'load_profile.csv',
                },
            },
            'mqtt': {
                'enabled': False,
                'broker': 'localhost',
                'port': 1883,
                'topic': 'house/batcontrol',
                'tls': False,
            },
            'evcc': {
                'enabled': False,
                'broker': 'localhost',
                'port': 1883,
                'status_topic': 'evcc/status',
                'loadpoint_topic': ['evcc/loadpoints/1/charging'],
                'tls': False,
            },
        }

    def test_validate_full_config(self):
        """Test validation of a complete config dict."""
        result = validate_config(self._full_config())
        assert isinstance(result, dict)
        assert result['timezone'] == 'Europe/Berlin'
        assert result['time_resolution_minutes'] == 60

    def test_validate_returns_dict(self):
        """Test that validate_config returns a plain dict."""
        result = validate_config(self._full_config())
        assert type(result) is dict

    def test_validate_coerces_string_types(self):
        """Test that validation coerces HA addon string values."""
        config = self._full_config()
        config['time_resolution_minutes'] = '60'
        config['mqtt']['port'] = '1883'
        config['evcc']['port'] = '1883'
        config['utility']['vat'] = '0.19'
        result = validate_config(config)
        assert result['time_resolution_minutes'] == 60
        assert result['mqtt']['port'] == 1883
        assert result['evcc']['port'] == 1883
        assert result['utility']['vat'] == 0.19

    def test_validate_preserves_nested_structure(self):
        """Test that nested dict structure is preserved."""
        result = validate_config(self._full_config())
        assert 'battery_control' in result
        assert result['battery_control']['min_price_difference'] == 0.05
        assert 'inverter' in result
        assert result['inverter']['type'] == 'dummy'

    def test_validate_invalid_time_resolution(self):
        """Test that invalid time_resolution_minutes fails validation."""
        config = self._full_config()
        config['time_resolution_minutes'] = 45
        with pytest.raises(ValidationError):
            validate_config(config)

    def test_ha_addon_string_config(self):
        """Simulate HA addon options.json where many values are strings."""
        config = self._full_config()
        # Simulate HA string coercion for all numeric fields
        config['time_resolution_minutes'] = '60'
        config['max_logfile_size'] = '200'
        config['battery_control']['min_price_difference'] = '0.05'
        config['battery_control']['min_recharge_amount'] = '100'
        config['inverter']['max_grid_charge_rate'] = '5000'
        config['mqtt']['port'] = '1883'
        config['mqtt']['retry_attempts'] = '5'
        config['mqtt']['retry_delay'] = '10'
        config['evcc']['port'] = '1883'

        result = validate_config(config)

        assert isinstance(result['time_resolution_minutes'], int)
        assert isinstance(result['max_logfile_size'], int)
        assert isinstance(
            result['battery_control']['min_price_difference'], float)
        assert isinstance(result['inverter']['max_grid_charge_rate'], float)
        assert isinstance(result['mqtt']['port'], int)
        assert isinstance(result['mqtt']['retry_attempts'], int)
        assert isinstance(result['evcc']['port'], int)
