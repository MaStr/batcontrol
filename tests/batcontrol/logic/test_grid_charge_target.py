import pytest

from batcontrol.logic.grid_charge_target import calculate_effective_grid_charge_soc


def _calculate_target(**overrides):
    values = {
        'strategy': 'forecast',
        'configured_min_grid_charge_soc': 0.55,
        'max_charging_from_grid_limit': 0.89,
        'max_capacity': 10240,
        'min_soc_energy': 1024,
        'production': [149, 569, 1488, 2678, 3500, 4000],
        'consumption': [547, 731, 3427, 3497, 3700, 500],
        'prices': [0.4635, 0.7018, 0.7018, 0.7018, 0.7018, 0.4635],
        'min_price_difference': 0.05,
        'min_price_difference_rel': 0.0,
        'pv_forecast_factor': 0.5,
    }
    values.update(overrides)
    return calculate_effective_grid_charge_soc(**values)


def test_fixed_strategy_returns_configured_target():
    target = _calculate_target(strategy='fixed')

    assert target == 0.55


def test_unset_configured_target_stays_disabled():
    target = _calculate_target(configured_min_grid_charge_soc=None)

    assert target is None


def test_sparse_forecast_dict_raises_clear_error():
    with pytest.raises(
            ValueError,
            match='consecutive integer indices starting at 0'):
        _calculate_target(production={0: 0, 2: 0})


def test_forecast_strategy_raises_target_for_slow_morning_pv_ramp():
    target = _calculate_target()

    assert target == pytest.approx(0.81, abs=0.01)


def test_lower_pv_forecast_factor_raises_target_for_ramp_uncertainty():
    optimistic_target = _calculate_target(pv_forecast_factor=1.0)
    conservative_target = _calculate_target(pv_forecast_factor=0.5)
    no_pv_target = _calculate_target(pv_forecast_factor=0.0)

    assert optimistic_target == 0.55
    assert optimistic_target < conservative_target < no_pv_target
    assert no_pv_target == 0.89


def test_forecast_strategy_ignores_current_slot_flexible_load():
    target = _calculate_target(
        production=[0, 0, 0],
        consumption=[20000, 0, 0],
        prices=[0.20, 0.30, 0.20],
    )

    assert target == 0.55


def test_forecast_strategy_defers_when_another_cheap_slot_remains():
    target = _calculate_target(
        production=[0, 0, 0, 0],
        consumption=[0, 0, 9000, 9000],
        prices=[0.20, 0.20, 0.50, 0.50],
    )

    assert target == 0.55


def test_forecast_strategy_respects_absolute_min_price_difference():
    ignored_small_spread = _calculate_target(
        production=[0, 0, 0],
        consumption=[0, 5000, 0],
        prices=[0.20, 0.249, 0.20],
        min_price_difference=0.05,
        min_price_difference_rel=0.0,
    )
    included_large_spread = _calculate_target(
        production=[0, 0, 0],
        consumption=[0, 5000, 0],
        prices=[0.20, 0.251, 0.20],
        min_price_difference=0.05,
        min_price_difference_rel=0.0,
    )

    assert ignored_small_spread == 0.55
    assert included_large_spread == pytest.approx(0.59, abs=0.01)


def test_forecast_strategy_uses_relative_min_price_difference_when_larger():
    ignored_by_relative_spread = _calculate_target(
        production=[0, 0, 0],
        consumption=[0, 5000, 0],
        prices=[0.50, 0.59, 0.50],
        min_price_difference=0.05,
        min_price_difference_rel=0.20,
    )
    included_by_relative_spread = _calculate_target(
        production=[0, 0, 0],
        consumption=[0, 5000, 0],
        prices=[0.50, 0.61, 0.50],
        min_price_difference=0.05,
        min_price_difference_rel=0.20,
    )

    assert ignored_by_relative_spread == 0.55
    assert included_by_relative_spread == pytest.approx(0.59, abs=0.01)


def test_forecast_strategy_caps_target_at_grid_charge_limit():
    target = _calculate_target(max_charging_from_grid_limit=0.65)

    assert target == 0.65


def test_forecast_strategy_keeps_configured_floor_when_forecast_need_is_small():
    target = _calculate_target(
        production=[0, 3000, 3000],
        consumption=[500, 500, 500],
        prices=[0.20, 0.30, 0.30],
    )

    assert target == 0.55
