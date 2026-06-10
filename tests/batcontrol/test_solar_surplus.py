"""Tests for ForecastMetrics.solar_active_and_surplus."""
import numpy as np
import pytest

from batcontrol.forecast_metrics import ForecastMetrics


def _call(production, consumption, free_cap=0.0):
    return ForecastMetrics.solar_active_and_surplus(
        np.array(production, dtype=float),
        np.array(consumption, dtype=float),
        free_cap,
    )


class TestSolarActive:
    def test_active_when_production_starts_at_slot0(self):
        active, _ = _call([1000, 1500, 500], [400, 400, 400])
        assert active is True

    def test_inactive_when_production_starts_later(self):
        active, _ = _call([0, 0, 800, 1200, 0], [300, 300, 300, 300, 300])
        assert active is False

    def test_inactive_when_no_production_at_all(self):
        active, _ = _call([0, 0, 0, 0], [300, 400, 350, 300])
        assert active is False

    def test_active_when_slot0_producing_even_with_gap_after(self):
        active, _ = _call([800, 0, 600, 0], [300, 300, 300, 300])
        assert active is True


class TestSurplusActive:
    def test_surplus_zero_when_net_production_fits_in_battery(self):
        # net = 1000+1000 = 2000 Wh, free_cap=3000 -> fits, no surplus
        _, surplus = _call([1500, 1500, 0], [500, 500, 500], free_cap=3000.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_positive_when_net_production_exceeds_free_capacity(self):
        # net = 1000+1000 = 2000 Wh, free_cap=1200 -> surplus=800
        _, surplus = _call([1500, 1500, 0], [500, 500, 0], free_cap=1200.0)
        assert surplus == pytest.approx(800.0)

    def test_surplus_accounts_for_consumption_in_window(self):
        # slot0: +500 net, slot1: -500 net -> total=0, no surplus
        _, surplus = _call([2000, 2000, 0], [1500, 2500, 400], free_cap=0.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_never_negative(self):
        _, surplus = _call([100, 100, 0], [800, 800, 800], free_cap=10000.0)
        assert surplus == 0.0

    def test_active_uses_only_first_production_window(self):
        # 48h forecast: today's solar then a long break then tomorrow's solar
        # 'during' must NOT include tomorrow's solar (production_end stops at first zero)
        today_solar = [1500, 1500]  # 2000 Wh net production
        night = [0] * 12
        tomorrow_solar = [1500, 1500]
        production = today_solar + night + tomorrow_solar
        consumption = [500] * len(production)
        # solar_net = -(500-1500 + 500-1500) = 2000 Wh, free_cap=1200 -> surplus=800
        _, surplus = _call(production, consumption, free_cap=1200.0)
        assert surplus == pytest.approx(800.0)


class TestSurplusInactive:
    def test_surplus_zero_when_no_solar_in_forecast(self):
        _, surplus = _call([0, 0, 0, 0], [500, 500, 500, 500], free_cap=0.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_positive_when_solar_overflows(self):
        # bridge: 2 slots * 200 Wh = 400 Wh
        # solar_net: 2 slots * (1000-200) = 1600 Wh
        # surplus = max(0, 1600 - 500 - 400) = 700 Wh
        production = [0, 0, 1000, 1000, 0]
        consumption = [200, 200, 200, 200, 200]
        _, surplus = _call(production, consumption, free_cap=500.0)
        assert surplus == pytest.approx(700.0)

    def test_surplus_zero_when_solar_fits_in_battery_after_night_discharge(self):
        # bridge=400, solar_net=800, free_cap=2000 -> 800-2000-400 < 0 -> surplus=0
        production = [0, 0, 1000, 0]
        consumption = [200, 200, 200, 200]
        _, surplus = _call(production, consumption, free_cap=2000.0)
        assert surplus == pytest.approx(0.0)

    def test_night_discharge_creates_room_for_solar(self):
        # slot0: cons=500 (bridge=500, opens battery room)
        # slots1-2: 2000W prod, 500W cons -> solar_net=3000 Wh
        # surplus = max(0, 3000 - 2000 - 500) = 500
        production = [0, 2000, 2000, 0]
        consumption = [500, 500, 500, 500]
        _, surplus = _call(production, consumption, free_cap=2000.0)
        assert surplus == pytest.approx(500.0)

    def test_surplus_never_negative(self):
        _, surplus = _call([0, 0, 100, 0], [500, 500, 500, 500], free_cap=10000.0)
        assert surplus == 0.0

    def test_inactive_only_uses_first_production_window(self):
        # 48h: night, tomorrow solar window (slots 2-3), second night, day-after solar
        production = [0, 0, 1000, 1000, 0, 0, 0, 0, 1000, 1000]
        consumption = [200] * 10
        # bridge=400 (slots 0-1), solar_net=1600 (slots 2-3), free_cap=500
        # surplus = max(0, 1600-500-400) = 700 (day-after ignored)
        _, surplus = _call(production, consumption, free_cap=500.0)
        assert surplus == pytest.approx(700.0)

    def test_works_with_15min_resolution(self):
        # Arrays are already Wh/slot independent of resolution
        # 4 slots night (200 Wh each) = 800 bridge
        # 4 slots solar 500 Wh prod, 200 Wh cons each = 4 * 300 = 1200 Wh solar_net
        # free_cap=0 -> surplus = max(0, 1200 - 0 - 800) = 400
        production = [0, 0, 0, 0, 500, 500, 500, 500, 0]
        consumption = [200] * 9
        _, surplus = _call(production, consumption, free_cap=0.0)
        assert surplus == pytest.approx(400.0)
