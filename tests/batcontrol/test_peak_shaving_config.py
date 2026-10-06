"""Tests for PeakShavingConfig dataclass validation and construction.

These tests verify that invalid configuration values raise a clear
``ValueError`` referencing the peak_shaving.* config key, rather than
failing later in ``CalculationParameters.__post_init__``.
"""
import pytest

from batcontrol.logic import PeakShavingConfig


class TestPeakShavingConfigValidation:
    """Test PeakShavingConfig.__post_init__ validates inputs."""

    def test_defaults_are_valid(self):
        cfg = PeakShavingConfig()
        assert cfg.enabled is False
        assert cfg.time_active is True
        assert cfg.price_active is True
        assert cfg.allow_full_battery_after == 14
        assert cfg.price_limit is None

    def test_mode_keyword_no_longer_accepted(self):
        with pytest.raises(TypeError):
            PeakShavingConfig(mode='time')  # pylint: disable=unexpected-keyword-arg

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

    def test_price_limit_bool_rejected(self):
        # bool is a subclass of int but is almost certainly a config typo.
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig(price_limit=True)
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig(price_limit=False)

    def test_allow_full_battery_after_bool_rejected(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig(allow_full_battery_after=True)

    def test_allow_full_battery_after_string_rejected(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig(allow_full_battery_after='12')


class TestPeakShavingConfigFromConfig:
    """Test PeakShavingConfig.from_config factory method."""

    def test_missing_section_uses_defaults(self):
        cfg = PeakShavingConfig.from_config({})
        assert cfg.enabled is False
        assert cfg.allow_full_battery_after == 14
        assert cfg.price_limit is None

    def test_full_config_applied(self):
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {
                'enabled': True,
                'time_active': False,
                'price_active': True,
                'allow_full_battery_after': 12,
                'price_limit': 0.10,
            }
        })
        assert cfg.enabled is True
        assert cfg.time_active is False
        assert cfg.price_active is True
        assert cfg.allow_full_battery_after == 12
        assert cfg.price_limit == 0.10

    @pytest.mark.parametrize('mode', ['time', 'price', 'combined', 'bogus'])
    def test_removed_mode_key_raises(self, mode):
        with pytest.raises(ValueError, match='peak_shaving.mode was removed'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'mode': mode}
            })

    def test_removed_mode_key_raises_alongside_switches(self):
        with pytest.raises(ValueError, match='peak_shaving.mode was removed'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'mode': 'time', 'price_active': True}
            })

    def test_invalid_hour_from_config_raises(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.allow_full_battery_after'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'allow_full_battery_after': 99}
            })

    def test_unparseable_price_limit_string_raises_keyed_error(self):
        # Reviewer comment: float() of an unparseable string should be
        # re-wrapped with a peak_shaving.price_limit-prefixed message.
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'price_limit': 'cheap'}
            })

    def test_price_limit_list_raises_keyed_error(self):
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'price_limit': [0.05]}
            })

    def test_price_limit_bool_from_config_raises_keyed_error(self):
        with pytest.raises(ValueError, match='peak_shaving.price_limit'):
            PeakShavingConfig.from_config({
                'peak_shaving': {'price_limit': True}
            })

    def test_price_limit_numeric_string_accepted(self):
        # A YAML quoted number like "0.05" is a common HA-form-field shape;
        # float() coerces it cleanly.
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {'price_limit': '0.05'}
        })
        assert cfg.price_limit == 0.05


class TestPeakShavingConfigFallbackWarning:
    """Test the one-time warning for price_active + missing price_limit.

    The warning fires at config load (``from_config``) only -- not in
    ``__post_init__`` -- so that ``dataclasses.replace`` in the per-cycle
    build path does not re-emit it on every evaluation.
    """

    LOGGER = 'batcontrol.logic.logic_interface'

    def test_price_active_without_price_limit_logs_warning(self, caplog):
        with caplog.at_level('WARNING', logger=self.LOGGER):
            PeakShavingConfig.from_config({
                'peak_shaving': {'enabled': True},
            })
        messages = [r.getMessage() for r in caplog.records
                    if r.levelname == 'WARNING']
        assert any("price_active" in m and "price_limit" in m
                   for m in messages)

    def test_disabled_without_price_limit_does_not_warn(self, caplog):
        # When peak shaving is disabled there is no user-visible problem.
        with caplog.at_level('WARNING', logger=self.LOGGER):
            PeakShavingConfig.from_config({
                'peak_shaving': {'enabled': False},
            })
        assert not [r for r in caplog.records if r.levelname == 'WARNING']

    def test_price_active_with_price_limit_does_not_warn(self, caplog):
        with caplog.at_level('WARNING', logger=self.LOGGER):
            PeakShavingConfig.from_config({
                'peak_shaving': {'enabled': True, 'price_limit': 0.05},
            })
        assert not [r for r in caplog.records if r.levelname == 'WARNING']

    def test_time_only_without_price_limit_does_not_warn(self, caplog):
        with caplog.at_level('WARNING', logger=self.LOGGER):
            PeakShavingConfig.from_config({
                'peak_shaving': {'enabled': True, 'price_active': False},
            })
        assert not [r for r in caplog.records if r.levelname == 'WARNING']

    def test_replace_does_not_re_emit_warning(self, caplog):
        # dataclasses.replace re-runs __post_init__ but must not trigger
        # the fallback warning on every per-cycle rebuild.
        import dataclasses
        cfg = PeakShavingConfig(enabled=True, price_limit=None)
        with caplog.at_level('WARNING', logger=self.LOGGER):
            dataclasses.replace(cfg, enabled=False)
            dataclasses.replace(cfg, enabled=True)
        warnings = [r for r in caplog.records if r.levelname == 'WARNING']
        assert warnings == []


class TestPeakShavingConfigSwitches:
    """Explicit rule switches are read from the config."""

    def test_time_only(self):
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {'time_active': True, 'price_active': False}
        })
        assert cfg.time_active is True
        assert cfg.price_active is False

    def test_price_only(self):
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {
                'time_active': False, 'price_active': True,
                'price_limit': 0.05}
        })
        assert cfg.time_active is False
        assert cfg.price_active is True

    def test_solar_cap_only_keeps_other_defaults(self):
        cfg = PeakShavingConfig.from_config({
            'peak_shaving': {
                'solar_cap_active': True, 'feed_in_limit_w': 6000}
        })
        assert cfg.solar_cap_active is True
        assert cfg.time_active is True
        assert cfg.price_active is True


class TestPeakShavingConfigDefaults:
    """Test the default values of the new solar_cap fields."""

    def test_empty_dict_defaults(self):
        cfg = PeakShavingConfig.from_config({})
        assert cfg.time_active is True
        assert cfg.price_active is True
        assert cfg.solar_cap_active is False
        assert cfg.feed_in_limit_w == 0.0
        assert cfg.feed_in_limit_headroom == 1.0

    def test_empty_peak_shaving_section_defaults(self):
        cfg = PeakShavingConfig.from_config({'peak_shaving': {}})
        assert cfg.time_active is True
        assert cfg.price_active is True
        assert cfg.solar_cap_active is False
        assert cfg.feed_in_limit_w == 0.0
        assert cfg.feed_in_limit_headroom == 1.0

    def test_dataclass_defaults_match(self):
        cfg = PeakShavingConfig()
        assert cfg.time_active is True
        assert cfg.price_active is True
        assert cfg.solar_cap_active is False
        assert cfg.feed_in_limit_w == 0.0
        assert cfg.feed_in_limit_headroom == 1.0


class TestPeakShavingConfigSolarCapValidation:
    """Validation of feed_in_limit_w and feed_in_limit_headroom."""

    def test_feed_in_limit_w_negative_raises(self):
        with pytest.raises(ValueError, match='peak_shaving.feed_in_limit_w'):
            PeakShavingConfig(feed_in_limit_w=-1)

    def test_feed_in_limit_w_bool_rejected(self):
        with pytest.raises(ValueError, match='peak_shaving.feed_in_limit_w'):
            PeakShavingConfig(feed_in_limit_w=True)

    def test_feed_in_limit_w_zero_accepted(self):
        cfg = PeakShavingConfig(feed_in_limit_w=0)
        assert cfg.feed_in_limit_w == 0

    def test_feed_in_limit_w_positive_accepted(self):
        cfg = PeakShavingConfig(feed_in_limit_w=6000)
        assert cfg.feed_in_limit_w == 6000

    def test_feed_in_limit_w_string_rejected(self):
        with pytest.raises(ValueError, match='peak_shaving.feed_in_limit_w'):
            PeakShavingConfig(feed_in_limit_w='6000')

    def test_feed_in_limit_headroom_below_one_raises(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.feed_in_limit_headroom'):
            PeakShavingConfig(feed_in_limit_headroom=0.9)

    def test_feed_in_limit_headroom_bool_rejected(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.feed_in_limit_headroom'):
            PeakShavingConfig(feed_in_limit_headroom=False)

    def test_feed_in_limit_headroom_one_accepted(self):
        cfg = PeakShavingConfig(feed_in_limit_headroom=1.0)
        assert cfg.feed_in_limit_headroom == 1.0

    def test_feed_in_limit_headroom_above_one_accepted(self):
        cfg = PeakShavingConfig(feed_in_limit_headroom=1.25)
        assert cfg.feed_in_limit_headroom == 1.25

    def test_feed_in_limit_headroom_string_rejected(self):
        with pytest.raises(ValueError,
                           match='peak_shaving.feed_in_limit_headroom'):
            PeakShavingConfig(feed_in_limit_headroom='1.1')
