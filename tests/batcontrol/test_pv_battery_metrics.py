"""Tests for ForecastMetrics.pv_start_battery and ForecastMetrics.forecast_min_battery."""
import numpy as np
import pytest

from batcontrol.forecast_metrics import ForecastMetrics


def _net(production, consumption):
    return np.array(consumption, dtype=float) - np.array(production, dtype=float)


# ---------------------------------------------------------------------------
# pv_start_battery
# ---------------------------------------------------------------------------

class TestPvStartBattery:
    def test_returns_battery_just_before_first_net_charging_slot(self):
        # 2 discharge slots (net=+300), then net charging starts
        # stored=2000, discharge 2x300=600 -> battery=1400 at pv start
        net = _net([0, 0, 1000], [300, 300, 200])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=2000.0, free_capacity=3000.0)
        assert result == pytest.approx(1400.0)

    def test_returns_zero_when_no_net_charging_in_forecast(self):
        net = _net([0, 0, 0], [300, 300, 300])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=1000.0, free_capacity=3000.0)
        assert result == 0.0

    def test_returns_zero_when_battery_depleted_before_pv_start(self):
        # stored=500, 2x300 discharge exhausts it before net<0
        net = _net([0, 0, 1000], [300, 300, 200])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=500.0, free_capacity=3000.0)
        assert result == 0.0

    def test_returns_stored_when_first_slot_already_net_charging(self):
        # slot 0 already net<0 (solar active with surplus)
        net = _net([1000, 500, 0], [200, 600, 300])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=3000.0, free_capacity=2000.0)
        assert result == pytest.approx(3000.0)

    def test_floor_clamp_at_zero(self):
        # battery drains to 0, stays there, then net charging starts
        net = _net([0, 0, 0, 1000], [300, 300, 300, 200])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=500.0, free_capacity=3000.0)
        assert result == 0.0

    def test_works_with_15min_resolution(self):
        # 4 night slots at 100 Wh each, then net charging
        net = _net([0, 0, 0, 0, 600], [100, 100, 100, 100, 100])
        result = ForecastMetrics.pv_start_battery(net, stored_usable_energy=1000.0, free_capacity=2000.0)
        assert result == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# forecast_min_battery
# ---------------------------------------------------------------------------

class TestForecastMinBattery:
    def test_returns_stored_when_always_charging(self):
        # All slots net charging: battery only grows, minimum = stored
        net = _net([1000, 1000, 1000], [200, 200, 200])
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=2000.0, free_capacity=3000.0)
        assert result == pytest.approx(2000.0)

    def test_returns_zero_when_battery_depleted(self):
        net = _net([0, 0, 0, 0], [500, 500, 500, 500])
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=1000.0, free_capacity=3000.0)
        assert result == 0.0

    def test_tracks_trough_not_final_value(self):
        # Discharge to trough, then solar recharges above trough
        # stored=3000, 4x400 discharge -> trough=1400, then solar restores
        net = _net([0, 0, 0, 0, 2000, 2000], [400, 400, 400, 400, 200, 200])
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=3000.0, free_capacity=5000.0)
        assert result == pytest.approx(1400.0)

    def test_cap_limits_charging(self):
        # Minimum is during initial discharge; cap is irrelevant for the trough
        net = _net([0, 0, 3000, 3000, 0, 0], [300, 300, 200, 200, 300, 300])
        # stored=3000, trough after 2x discharge: 3000-300-300=2400
        result = ForecastMetrics.forecast_min_battery(
            net, stored_usable_energy=3000.0, free_capacity=2000.0)
        assert result == pytest.approx(2400.0)

    def test_multi_day_tracks_deepest_trough(self):
        # Night1 discharges 1000, Solar1 recharges, Night2 discharges 3200 (deeper)
        production = [0, 0, 1500, 1500, 0, 0, 0, 0]
        consumption = [500, 500, 200, 200, 800, 800, 800, 800]
        net = _net(production, consumption)
        # stored=4000: 4000-500-500=3000, +1300+1300=5600, -800x4=2400 (deepest)
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=4000.0, free_capacity=4000.0)
        assert result == pytest.approx(2400.0)

    def test_returns_zero_not_negative(self):
        net = _net([0], [10000])
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=1000.0, free_capacity=500.0)
        assert result == 0.0

    def test_initial_stored_counts_as_potential_minimum(self):
        # stored=0: minimum starts at 0, solar later does not change that
        net = _net([1000, 1000], [200, 200])
        result = ForecastMetrics.forecast_min_battery(net, stored_usable_energy=0.0, free_capacity=5000.0)
        assert result == 0.0
