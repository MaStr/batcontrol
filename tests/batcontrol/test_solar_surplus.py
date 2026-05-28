"""Tests for Batcontrol._compute_solar_surplus_and_phase."""
import numpy as np
import pytest
from unittest.mock import MagicMock

from batcontrol.core import Batcontrol


def _make_core(time_resolution=60):
    """Return a minimal stub with only the attributes used by the method."""
    stub = MagicMock(spec=Batcontrol)
    stub.time_resolution = time_resolution
    stub._compute_solar_surplus_and_phase = (
        Batcontrol._compute_solar_surplus_and_phase.__get__(stub, Batcontrol)
    )
    return stub


def _call(stub, production, consumption, reserved=0.0, free_cap=0.0, stored_usable=0.0):
    return stub._compute_solar_surplus_and_phase(
        np.array(production, dtype=float),
        np.array(consumption, dtype=float),
        reserved, free_cap, stored_usable,
    )


class TestPhaseDetection:
    def test_phase_during_when_production_starts_at_slot0(self):
        stub = _make_core()
        phase, _ = _call(stub, [1000, 1500, 500], [400, 400, 400])
        assert phase == 'during'

    def test_phase_before_when_production_starts_later(self):
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
        # Slot 0 producing, slot 1 gap, slot 2 producing again
        stub = _make_core()
        phase, _ = _call(stub, [800, 0, 600, 0], [300, 300, 300, 300])
        assert phase == 'during'

    def test_phase_before_with_15min_resolution(self):
        stub = _make_core(time_resolution=15)
        # 4 empty slots (1h) then solar
        phase, _ = _call(stub, [0, 0, 0, 0, 500, 800, 600], [200] * 7)
        assert phase == 'before'


class TestSurplusDuringBefore:
    def test_surplus_zero_when_net_production_fits_in_battery(self):
        # 2h * 1000 W net = 2000 Wh net, free_capacity 3000 Wh -> fits, no surplus
        stub = _make_core()
        production = [1500, 1500, 0]
        consumption = [500, 500, 500]
        _, surplus = _call(stub, production, consumption, free_cap=3000.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_positive_when_net_production_exceeds_free_capacity(self):
        # net = 1000 + 1000 = 2000 Wh, free_cap = 1200 Wh -> surplus = 800 Wh
        stub = _make_core()
        production = [1500, 1500, 0]
        consumption = [500, 500, 0]
        _, surplus = _call(stub, production, consumption, free_cap=1200.0)
        assert surplus == pytest.approx(800.0)

    def test_surplus_accounts_for_consumption_in_window(self):
        # Slot 0: 2000 W prod, 1500 W cons -> +500 W net
        # Slot 1: 2000 W prod, 2500 W cons -> -500 W net
        # Total net = 0 Wh -> no surplus regardless of free_cap
        stub = _make_core()
        production = [2000, 2000, 0]
        consumption = [1500, 2500, 400]
        _, surplus = _call(stub, production, consumption, free_cap=0.0)
        assert surplus == pytest.approx(0.0)

    def test_surplus_never_negative(self):
        stub = _make_core()
        production = [100, 100, 0]
        consumption = [800, 800, 800]
        _, surplus = _call(stub, production, consumption, free_cap=10000.0)
        assert surplus == 0.0

    def test_before_phase_uses_full_window_including_pre_production_consumption(self):
        # Slot 0: no production, 500 W consumption (eats into net)
        # Slot 1-2: 2000 W prod, 500 W cons each -> +1500 W * 2 = 3000 Wh net
        # Net window (slots 0-2) = -500 + 1500 + 1500 = 2500 Wh
        # free_cap = 2000 -> surplus = 500
        stub = _make_core()
        production = [0, 2000, 2000, 0]
        consumption = [500, 500, 500, 500]
        _, surplus = _call(stub, production, consumption, free_cap=2000.0)
        assert surplus == pytest.approx(500.0)

    def test_15min_resolution_scales_wh_correctly(self):
        # Each slot is 15 min = 0.25 h
        # production=2000W, consumption=0 for 4 slots = 4 * 2000 * 0.25 = 2000 Wh net
        # free_cap=1500 -> surplus = 500 Wh
        stub = _make_core(time_resolution=15)
        production = [2000, 2000, 2000, 2000, 0]
        consumption = [0, 0, 0, 0, 0]
        _, surplus = _call(stub, production, consumption, free_cap=1500.0)
        assert surplus == pytest.approx(500.0)


class TestSurplusAfter:
    def test_surplus_positive_when_stored_exceeds_expected_consumption(self):
        # stored_usable=5000, reserved=1000 -> unreserved=4000
        # next 3 slots all consuming 500 Wh -> expected = 1500 Wh
        # surplus = 4000 - 1500 = 2500 Wh
        stub = _make_core()
        production = [0, 0, 0]
        consumption = [500, 500, 500]
        _, surplus = _call(stub, production, consumption,
                           reserved=1000.0, stored_usable=5000.0)
        assert surplus == pytest.approx(2500.0)

    def test_surplus_zero_when_stored_less_than_consumption(self):
        stub = _make_core()
        production = [0, 0, 0]
        consumption = [1000, 1000, 1000]
        _, surplus = _call(stub, production, consumption,
                           reserved=0.0, stored_usable=1500.0)
        assert surplus == pytest.approx(0.0)

    def test_sums_all_consumption_slots_when_no_future_solar(self):
        # No production at all -> next_solar_start = 4 (end of array)
        # All 4 slots consume 500 W = 4 * 500 * 1h = 2000 Wh expected
        # stored=5000, reserved=0 -> unreserved=5000 -> surplus=3000
        stub = _make_core()
        production = [0, 0, 0, 0]
        consumption = [500, 500, 500, 500]
        _, surplus = _call(stub, production, consumption,
                           reserved=0.0, stored_usable=5000.0)
        assert surplus == pytest.approx(3000.0)

    def test_reserved_energy_reduces_usable(self):
        stub = _make_core()
        production = [0, 0]
        consumption = [0, 0]
        # stored=2000, reserved=1000 -> unreserved=1000, consumption=0 -> surplus=1000
        _, surplus = _call(stub, production, consumption,
                           reserved=1000.0, stored_usable=2000.0)
        assert surplus == pytest.approx(1000.0)

    def test_surplus_never_negative_when_reserved_exceeds_stored(self):
        stub = _make_core()
        production = [0]
        consumption = [100]
        _, surplus = _call(stub, production, consumption,
                           reserved=5000.0, stored_usable=1000.0)
        assert surplus == 0.0
