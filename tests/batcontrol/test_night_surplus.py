"""Tests for Batcontrol._compute_night_surplus."""
import numpy as np
import pytest
from unittest.mock import MagicMock

from batcontrol.core import Batcontrol


def _make_core(time_resolution=60):
    stub = MagicMock(spec=Batcontrol)
    stub.time_resolution = time_resolution
    stub._compute_night_surplus = (
        Batcontrol._compute_night_surplus.__get__(stub, Batcontrol)
    )
    return stub


def _call(stub, production, consumption, stored_usable=0.0, free_cap=0.0):
    return stub._compute_night_surplus(
        np.array(production, dtype=float),
        np.array(consumption, dtype=float),
        stored_usable,
        free_cap,
    )


class TestNightSurplusNoProduction:
    def test_zero_when_no_production_in_forecast(self):
        stub = _make_core()
        result = _call(stub, [0, 0, 0, 0], [300, 300, 300, 300])
        assert result == pytest.approx(0.0)


class TestNightSurplusSolarActive:
    def test_full_battery_exceeds_night_consumption(self):
        # Solar active (slot 0), production window slots 0-1
        # net_delta = -(500-1500 + 500-1500) = 2000 Wh net gain
        # stored_usable=2000, free_cap=3000 -> battery_at_end = min(2000+3000, 2000+2000) = 4000
        # night: slots 2-3, consumption=500 each -> night_consumption=1000
        # surplus = 4000 - 1000 = 3000
        stub = _make_core()
        production = [1500, 1500, 0, 0]
        consumption = [500, 500, 500, 500]
        result = _call(stub, production, consumption, stored_usable=2000.0, free_cap=3000.0)
        assert result == pytest.approx(3000.0)

    def test_battery_just_empty_by_morning(self):
        # Solar active, net_delta=2000, stored=0, free=2000 -> battery_at_end=2000
        # night consumption = 2000 -> surplus = 0
        stub = _make_core()
        production = [1500, 1500, 0, 0]
        consumption = [500, 500, 1000, 1000]
        result = _call(stub, production, consumption, stored_usable=0.0, free_cap=2000.0)
        assert result == pytest.approx(0.0)

    def test_surplus_never_negative(self):
        # Battery drains completely during night
        stub = _make_core()
        production = [500, 0, 0, 0]
        consumption = [400, 1000, 1000, 1000]
        result = _call(stub, production, consumption, stored_usable=100.0, free_cap=5000.0)
        assert result == 0.0

    def test_uses_only_first_production_window_not_second_day(self):
        # Today solar (slots 0-1), night (slots 2-5), tomorrow solar (slots 6-7)
        # battery_at_end should be computed at slot 1, night ends at slot 6 (next production)
        stub = _make_core()
        today = [1500, 1500]
        night = [0] * 4  # 4 slots at 200 Wh each = 800 night consumption
        tomorrow = [1500, 1500]
        production = today + night + tomorrow
        consumption = [200] * len(production)
        # net_delta during slots 0-1: -((200-1500)+(200-1500)) = 2600
        # stored=1000, free=2000 -> battery_at_end = 1000 + min(2000, 2600) = 3000
        # night consumption slots 2-5: 4*200=800
        # surplus = 3000 - 800 = 2200
        result = _call(stub, production, consumption, stored_usable=1000.0, free_cap=2000.0)
        assert result == pytest.approx(2200.0)


class TestNightSurplusSolarInactive:
    def test_solar_tomorrow_enough_to_cover_night(self):
        # slots 0-1: bridge (200 Wh each = 400 Wh discharge)
        # slots 2-3: solar production (net +800 Wh each = 1600 Wh)
        # end_idx=4, night slots 4-5: 200 Wh each = 400 Wh night consumption
        # net_delta 0-3: -(200+200 - (1000-200) - (1000-200)) = -(400-1600) = 1200
        # stored=500, free=1500 -> battery_at_end = 500 + min(1500, 1200) = 1700
        # surplus = 1700 - 400 = 1300
        stub = _make_core()
        production = [0, 0, 1000, 1000, 0, 0]
        consumption = [200, 200, 200, 200, 200, 200]
        result = _call(stub, production, consumption, stored_usable=500.0, free_cap=1500.0)
        assert result == pytest.approx(1300.0)

    def test_no_forecast_after_production_end(self):
        # Forecast ends right after production window, no night slots
        stub = _make_core()
        production = [0, 0, 1000, 1000]
        consumption = [200, 200, 200, 200]
        # net_delta 0-3: -(200+200-800-800) = 1200
        # stored=500, free=1500 -> battery_at_end=1700
        # night_end = len(production) = 4, no slots after production -> consumption=0
        # surplus = 1700
        result = _call(stub, production, consumption, stored_usable=500.0, free_cap=1500.0)
        assert result == pytest.approx(1700.0)

    def test_free_cap_limits_charging(self):
        # Large production but very little free capacity
        # net_delta would be 3000, but free_cap=100 -> battery_at_end = 300+100 = 400
        stub = _make_core()
        production = [0, 2000, 2000, 0, 0]
        consumption = [100, 100, 100, 200, 200]
        # net_delta 0-2: -(100 + (100-2000) + (100-2000)) = -(100-1900-1900) = 3700
        # stored=300, free=100 -> battery_at_end = 300+min(100, 3700) = 400
        # night slots 3-4: 200+200=400 -> surplus = 0
        result = _call(stub, production, consumption, stored_usable=300.0, free_cap=100.0)
        assert result == pytest.approx(0.0)

    def test_works_with_15min_resolution(self):
        stub = _make_core(time_resolution=15)
        # 4 night slots then 4 solar slots then 4 more night slots
        production = [0, 0, 0, 0, 500, 500, 500, 500, 0, 0, 0, 0]
        consumption = [100] * 12
        # net_delta slots 0-7: -(4*100 + 4*(100-500)) = -(400 - 1600) = 1200
        # stored=500, free=2000 -> battery_at_end = 500 + min(2000, 1200) = 1700
        # night end at slot 8 (no second production), night_end=12
        # night consumption slots 8-11: 4*100=400
        # surplus = 1700 - 400 = 1300
        result = _call(stub, production, consumption, stored_usable=500.0, free_cap=2000.0)
        assert result == pytest.approx(1300.0)

    def test_surplus_never_negative_when_consumption_huge(self):
        stub = _make_core()
        production = [0, 0, 100, 0]
        consumption = [500, 500, 500, 5000]
        result = _call(stub, production, consumption, stored_usable=100.0, free_cap=10000.0)
        assert result == 0.0
