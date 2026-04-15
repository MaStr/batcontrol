"""Tests for PeakShavingConfig dataclass validation and construction.

These tests verify that invalid configuration values raise a clear
``ValueError`` referencing the peak_shaving.* config key, rather than
failing later in ``CalculationParameters.__post_init__``.
"""
import pytest

from batcontrol.core import PeakShavingConfig


class TestPeakShavingConfigValidation:
    """Test PeakShavingConfig.__post_init__ validates inputs."""

    def test_defaults_are_valid(self):
        cfg = PeakShavingConfig()
        assert cfg.enabled is False
        assert cfg.mode == 'combined'
        assert cfg.allow_full_battery_after == 14
        assert cfg.price_limit is None

    def test_all_valid_modes_accepted(self):
        for mode in ('time', 'price', 'combined'):
            cfg = PeakShavingConfig(mode=mode)
            assert cfg.mode == mode

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError, match='peak_shaving.mode'):
            PeakShavingConfig(mode='invalid')

    def test_allow_full_battery_after_out_of_range_high(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig(allow_full_battery_after=99)

    def test_allow_full_battery_after_out_of_range_low(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig(allow_full_battery_after=-1)

    def test_allow_full_battery_after_boundaries(self):
        # boundary values (0 and 23) must be accepted
        PeakShavingConfig(allow_full_battery_after=0)
        PeakShavingConfig(allow_full_battery_after=23)

    def test_price_limit_none_accepted(self):
        cfg = PeakShavingConfig(price_limit=None)
        assert cfg.price_limit is None

    def test_price_limit_numeric_accepted(self):
        cfg = PeakShavingConfig(price_limit=0.05)
        assert cfg.price_limit == 0.05
        cfg_int = PeakShavingConfig(price_limit=-1)
        assert cfg_int.price_limit == -1

    def test_price_limit_string_raises_value_error(self):
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig(price_limit='cheap')


class TestPeakShavingConfigFromConfig:
    """Test PeakShavingConfig.from_config factory method."""

    def test_missing_section_uses_defaults(self):
        cfg = PeakShavingConfig.from_config({})
        assert cfg.enabled is False
        assert cfg.mode == 'combined'
        assert cfg.allow_full_battery_after == 14
        assert cfg.price_limit is None

    def test_full_config_applied(self):
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {
                'enabled': True,
                'mode': 'price',
                'allow_full_battery_after': 12,
                'price_limit': 0.10,
            }
        })
        assert cfg.enabled is True
        assert cfg.mode == 'price'
        assert cfg.allow_full_battery_after == 12
        assert cfg.price_limit == 0.10

    def test_invalid_mode_from_config_raises(self):
        with pytest.raises(ValueError, match='peak_shaving.mode'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'mode': 'bogus'}
            })

    def test_invalid_hour_from_config_raises(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'allow_full_battery_after': 99}
            })
