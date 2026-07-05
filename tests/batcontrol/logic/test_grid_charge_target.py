import pytest

from batcontrol.logic.grid_charge_target import (
    GridChargeTargetConfig,
    apply_grid_charge_target_to_recharge,
    apply_grid_charge_target_to_reserve,
)


CAPACITY_WH = 10000


def _recharge(**overrides):
    values = {
        'config': GridChargeTargetConfig(strategy='forecast'),
        'recharge_energy': 2500.0,
        'required_energy': 3000.0,
        'stored_energy': 2000.0,
        'configured_min_grid_charge_soc': 0.50,
        'max_capacity': CAPACITY_WH,
        'max_charging_from_grid_limit': 0.90,
    }
    values.update(overrides)
    return apply_grid_charge_target_to_recharge(**values)


def _reserve(**overrides):
    values = {
        'config': GridChargeTargetConfig(strategy='forecast'),
        'reserved_energy': 3000.0,
        'min_soc_energy': 1000.0,
        'configured_min_grid_charge_soc': 0.50,
        'max_capacity': CAPACITY_WH,
        'max_charging_from_grid_limit': 0.90,
        'active': True,
    }
    values.update(overrides)
    return apply_grid_charge_target_to_reserve(**values)


def test_config_parses_strategy_case_and_whitespace():
    config = GridChargeTargetConfig.from_battery_control_config({
        'grid_charge_target_strategy': ' forecast ',
    })

    assert config.strategy == 'forecast'


def test_config_defaults_to_fixed_strategy():
    config = GridChargeTargetConfig.from_battery_control_config({})

    assert config.strategy == 'fixed'


def test_config_rejects_unknown_strategy():
    with pytest.raises(
            ValueError,
            match='battery_control.grid_charge_target_strategy must be one of'):
        GridChargeTargetConfig.from_battery_control_config({
            'grid_charge_target_strategy': 'dynamic',
        })


def test_recharge_without_configured_soc_stays_unchanged():
    result = _recharge(configured_min_grid_charge_soc=None)

    assert result.energy == 2500.0
    assert result.effective_soc is None


def test_fixed_recharge_uses_configured_soc_floor():
    result = _recharge(
        config=GridChargeTargetConfig(strategy='fixed'),
        recharge_energy=1000.0,
    )

    assert result.energy == pytest.approx(3000.0)
    assert result.effective_soc == 0.50


def test_forecast_recharge_adds_high_price_need_to_soc_floor():
    result = _recharge()

    assert result.energy == pytest.approx(6000.0)
    assert result.effective_soc == pytest.approx(0.80)


def test_forecast_recharge_keeps_existing_need_when_floor_is_lower():
    result = _recharge(
        configured_min_grid_charge_soc=0.05,
        recharge_energy=2500.0,
    )

    assert result.energy == pytest.approx(2500.0)
    assert result.effective_soc == pytest.approx(0.35)


def test_forecast_recharge_caps_target_at_grid_charge_limit():
    result = _recharge(max_charging_from_grid_limit=0.70)

    assert result.energy == pytest.approx(5000.0)
    assert result.effective_soc == pytest.approx(0.70)


def test_recharge_with_no_high_price_need_does_not_fill_floor():
    result = _recharge(
        recharge_energy=0.0,
        required_energy=0.0,
        stored_energy=2000.0,
    )

    assert result.energy == 0.0
    assert result.effective_soc == 0.50


def test_fixed_reserve_uses_configured_soc_floor():
    result = _reserve(config=GridChargeTargetConfig(strategy='fixed'))

    assert result.energy == pytest.approx(4000.0)
    assert result.effective_soc == 0.50


def test_forecast_reserve_adds_high_price_need_to_soc_floor():
    result = _reserve()

    assert result.energy == pytest.approx(7000.0)
    assert result.effective_soc == pytest.approx(0.80)


def test_forecast_reserve_caps_target_at_grid_charge_limit():
    result = _reserve(max_charging_from_grid_limit=0.70)

    assert result.energy == pytest.approx(6000.0)
    assert result.effective_soc == pytest.approx(0.70)


def test_inactive_reserve_stays_unchanged():
    result = _reserve(active=False)

    assert result.energy == 3000.0
    assert result.effective_soc == 0.50


def test_invalid_strategy_raises_clear_error():
    with pytest.raises(ValueError, match='grid_charge_target_strategy must be one of'):
        _recharge(config=GridChargeTargetConfig(strategy='dynamic'))
