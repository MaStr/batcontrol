"""Tests for LowPriceChargingConfig dataclass validation and construction."""
import pytest

from batcontrol.core import LowPriceChargingConfig


class TestLowPriceChargingConfigValidation:
    """LowPriceChargingConfig.__post_init__ validates inputs."""

    def test_defaults_are_valid(self):
        cfg = LowPriceChargingConfig()
        assert cfg.enabled is False
        assert cfg.threshold == 0.0
        assert cfg.force_charge_at_min is True

    def test_numeric_threshold_accepted(self):
        cfg = LowPriceChargingConfig(threshold=-0.10)
        assert cfg.threshold == -0.10
        cfg_int = LowPriceChargingConfig(threshold=-1)
        assert cfg_int.threshold == -1

    def test_string_threshold_rejected(self):
        with pytest.raises(ValueError, match='low_price_charging.threshold'):
            LowPriceChargingConfig(threshold='cheap')

    def test_bool_threshold_rejected(self):
        with pytest.raises(ValueError, match='low_price_charging.threshold'):
            LowPriceChargingConfig(threshold=True)

    def test_non_bool_enabled_rejected(self):
        with pytest.raises(ValueError, match='low_price_charging.enabled'):
            LowPriceChargingConfig(enabled='yes')

    def test_non_bool_force_charge_rejected(self):
        with pytest.raises(ValueError,
                           match='low_price_charging.force_charge_at_min'):
            LowPriceChargingConfig(force_charge_at_min='yes')


class TestLowPriceChargingConfigFromConfig:
    """LowPriceChargingConfig.from_config parses the YAML dict shape."""

    def test_missing_section_uses_defaults(self):
        cfg = LowPriceChargingConfig.from_config({})
        assert cfg.enabled is False
        assert cfg.threshold == 0.0
        assert cfg.force_charge_at_min is True

    def test_explicit_values(self):
        cfg = LowPriceChargingConfig.from_config({
            'low_price_charging': {
                'enabled': True,
                'threshold': -0.15,
                'force_charge_at_min': False,
            }
        })
        assert cfg.enabled is True
        assert cfg.threshold == -0.15
        assert cfg.force_charge_at_min is False

    def test_string_numeric_threshold_coerced(self):
        cfg = LowPriceChargingConfig.from_config({
            'low_price_charging': {'threshold': '-0.10'}
        })
        assert cfg.threshold == -0.10

    def test_unparseable_threshold_raises(self):
        with pytest.raises(ValueError,
                           match='low_price_charging.threshold'):
            LowPriceChargingConfig.from_config({
                'low_price_charging': {'threshold': 'cheap'}
            })

    def test_null_section_treated_as_empty(self):
        cfg = LowPriceChargingConfig.from_config({'low_price_charging': None})
        assert cfg.enabled is False
        assert cfg.threshold == 0.0
