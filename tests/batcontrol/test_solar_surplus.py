"""Tests for Batcontrol._compute_solar_surplus_and_phase."""
import numpy as np
import pytest
from unittest.mock import MagicMock

from batcontrol.core import Batcontrol


def _make_core(time_resolution=60, before_phase_threshold_h=4.0):
    """Return a minimal stub with only the attributes used by the method."""
    stub = MagicMock(spec=Batcontrol)
    stub.time_resolution = time_resolution
    stub.before_phase_threshold_h = before_phase_threshold_h
    stub._compute_solar_surplus_and_phase = (
        Batcontrol._compute_solar_surplus_and_phase.__get__(stub, Batcontrol)
    )
    return stub


def _call(stub, production, consumption, free_cap=0.0):
    return stub._compute_solar_surplus_and_phase(
        np.array(production, dtype=float),
        np.array(consumption, dtype=float),
        free_cap,
    )


class TestPhaseDetection:
    def test_phase_during_when_production_starts_at_slot0(self):
        stub = _make_core()
        phase, _ = _call(stub, [1000, 1500, 500], [400, 400, 400])
        assert phase == 'during'

    def test_phase_before_when_production_starts_within_threshold(self):
        # production_start=2, threshold=4 slots (4h at 60-min) -> before
        stub = _make_core()
        phase, _ = _call(stub, [0, 0, 800, 1200, 0], [300, 300, 300, 300, 300])
        assert phase == 'before'

    def test_phase_after_when_no_production_at_all(self):
        stub = _make_core()
        phase, _ = _call(stub, [0, 0, 0, 0], [300, 400, 350, 300])
        assert phase == 'after'

    def test_phase_after_when_all_production_is_zero_float(self):
        stub = _make_core()
        phase, _ = _call(stub, [0.0, 0.0, 0.0], [200.0, 200.0, 200.0])
        assert phase == 'after'

    def test_phase_during_tolerates_single_slot_gap(self):
        # Slot 0 producing, slot 1 gap -> still 'during' because production_start==0
        stub = _make_core()
        phase, _ = _call(stub, [800, 0, 600, 0], [300, 300, 300, 300])
        assert phase == 'during'

    def test_phase_after_when_production_start_beyond_threshold(self):
        # threshold=4 slots, production_start=5 -> after
        stub = _make_core()
        phase, _ = _call(stub, [0, 0, 0, 0, 0, 1000, 1000, 0], [200] * 8)
        assert phase == 'after'

    def test_phase_before_at_threshold_boundary(self):
        # production_start == threshold_slots (4) -> before (inclusive)
        stub = _make_core()
        phase, _ = _call(stub, [0, 0, 0, 0, 1000, 0], [200] * 6)
        assert phase == 'before'

    def test_threshold_slots_respect_time_resolution(self):
        # 4h threshold at 15-min resolution = 16 slots
        stub = _make_core(time_resolution=15)
        # production_start=16 -> exactly at threshold -> before
        prod = [0] * 16 + [500, 800, 600]
        cons = [200] * 19
        phase, _ = _call(stub, prod, cons)
        assert phase == 'before'
        # production_start=17 -> beyond threshold -> after
        prod2 = [0] * 17 + [500, 800]
        cons2 = [200] * 19
        phase2, _ = _call(stub, prod2, cons2)
        assert phase2 == 'after'

    def test_phase_before_with_15min_resolution(self):
        stub = _make_core(time_resolution=15)
        # 4 empty slots (1h) then solar; threshold=16 slots -> before
        phase, _ = _call(stub, [0, 0, 0, 0, 500, 800, 600], [200] * 7)
        assert phase == 'before'


class TestSurplusDuring:
    def test_surplus_zero_when_net_production_fits_in_battery(self):
        # net = 1000+1000 = 2000 Wh, free_cap=3000 -> fits, no surplus
        stub = _make_core()
        _, surplus = _call(stub, [1500, 1500, 0], [500, 500, 500], free_cap=3000.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_positive_when_net_production_exceeds_free_capacity(self):
        # net = 1000+1000 = 2000 Wh, free_cap=1200 -> surplus=800
        stub = _make_core()
        _, surplus = _call(stub, [1500, 1500, 0], [500, 500, 0], free_cap=1200.0)
        assert surplus == pytest.approx(800.0)

    def test_surplus_accounts_for_consumption_in_window(self):
        # slot0: +500 net, slot1: -500 net -> total=0, no surplus
        stub = _make_core()
        _, surplus = _call(stub, [2000, 2000, 0], [1500, 2500, 400], free_cap=0.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_never_negative(self):
        stub = _make_core()
        _, surplus = _call(stub, [100, 100, 0], [800, 800, 800], free_cap=10000.0)
        assert surplus == 0.0

    def test_during_uses_only_first_production_window(self):
        # 48h forecast: today solar slots 0-1, tonight/tomorrow no solar, tomorrow solar again
        # Must NOT include tomorrow's solar in 'during' surplus
        stub = _make_core()
        today_solar = [1500, 1500]  # 2000 Wh net production
        night = [0] * 12             # 12h night
        tomorrow_solar = [1500, 1500]
        production = today_solar + night + tomorrow_solar
        consumption = [500] * len(production)
        # production_end_current stops at slot 1 (first zero is slot 2)
        # solar_net = -sum(nc[:2]) = -(500-1500 + 500-1500) = 2000 Wh
        # free_cap=1200 -> surplus=800
        _, surplus = _call(stub, production, consumption, free_cap=1200.0)
        assert surplus == pytest.approx(800.0)


class TestSurplusBeforeAfter:
    def test_surplus_zero_when_no_solar_in_forecast(self):
        stub = _make_core()
        _, surplus = _call(stub, [0, 0, 0, 0], [500, 500, 500, 500], free_cap=0.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_positive_when_solar_overflows(self):
        # bridge: 2 slots * 200 Wh = 400 Wh
        # solar_net: 2 slots * (1000-200) = 1600 Wh
        # surplus = max(0, 1600 - 500 - 400) = 700 Wh
        stub = _make_core()
        production = [0, 0, 1000, 1000, 0]
        consumption = [200, 200, 200, 200, 200]
        _, surplus = _call(stub, production, consumption, free_cap=500.0)
        assert surplus == pytest.approx(700.0)

    def test_surplus_zero_when_solar_fits_in_battery_after_night_discharge(self):
        # bridge=400, solar_net=800, free_cap=2000 -> 800-2000-400 < 0 -> surplus=0
        stub = _make_core()
        production = [0, 0, 1000, 0]
        consumption = [200, 200, 200, 200]
        _, surplus = _call(stub, production, consumption, free_cap=2000.0)
        assert surplus == pytest.approx(0.0)

    def test_night_discharge_creates_room_for_solar(self):
        # slot0: cons=500 (bridge=500, opens battery room)
        # slots1-2: 2000W prod, 500W cons -> solar_net=3000 Wh
        # surplus = max(0, 3000 - 2000 - 500) = 500
        stub = _make_core()
        production = [0, 2000, 2000, 0]
        consumption = [500, 500, 500, 500]
        _, surplus = _call(stub, production, consumption, free_cap=2000.0)
        assert surplus == pytest.approx(500.0)

    def test_before_and_after_use_same_formula(self):
        # Same inputs, only threshold changes: one gives 'before', other 'after', surplus same
        production = [0, 0, 1000, 1000, 0]
        consumption = [200, 200, 200, 200, 200]
        stub_before = _make_core(before_phase_threshold_h=4.0)  # start=2 <= 4 -> before
        stub_after = _make_core(before_phase_threshold_h=1.0)   # start=2 > 1 -> after
        phase_before, surplus_before = _call(stub_before, production, consumption, free_cap=500.0)
        phase_after, surplus_after = _call(stub_after, production, consumption, free_cap=500.0)
        assert phase_before == 'before'
        assert phase_after == 'after'
        assert surplus_before == pytest.approx(surplus_after)

    def test_surplus_never_negative(self):
        stub = _make_core()
        _, surplus = _call(stub, [0, 0, 100, 0], [500, 500, 500, 500], free_cap=10000.0)
        assert surplus == 0.0

    def test_before_only_uses_first_production_window(self):
        # 48h: night, tomorrow solar window (slots 2-3), second night, day-after solar (slots 8-9)
        # Must NOT include day-after solar in 'before' calculation
        stub = _make_core()
        production = [0, 0, 1000, 1000, 0, 0, 0, 0, 1000, 1000]
        consumption = [200] * 10
        # bridge=400 (slots 0-1), solar_net=1600 (slots 2-3), free_cap=500
        # surplus = max(0, 1600-500-400) = 700 (day-after ignored)
        _, surplus = _call(stub, production, consumption, free_cap=500.0)
        assert surplus == pytest.approx(700.0)
