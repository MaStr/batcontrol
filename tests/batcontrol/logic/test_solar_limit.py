"""Tests for the pure solar_cap rule functions in logic/solar_limit.py.

Covers compute_solar_limit() (Case A reservation, Case B floor/cap,
headroom, partial slot 0, 15-minute intervals) and merge_limits() (the
floor-overrides-every-cap priority rule). See
docs/development/solar-limit-evaluation.md for the algorithm spec and the
reference-day numbers reproduced in test_reference_day_first_clip_floor.
"""
import unittest

from batcontrol.logic.solar_limit import compute_solar_limit, merge_limits


class TestComputeSolarLimitNeutral(unittest.TestCase):
    """Cases where the rule must have no effect."""

    def test_feed_in_limit_zero_is_neutral(self):
        """feed_in_limit_w=0 -> (0, -1) regardless of the arrays."""
        floor, cap = compute_solar_limit(
            production_wh=[9000, 9000], consumption_wh=[400, 400],
            feed_in_limit_w=0, interval_h=1.0,
            free_capacity_wh=1000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, -1))

    def test_feed_in_limit_none_is_neutral(self):
        """feed_in_limit_w=None -> (0, -1)."""
        floor, cap = compute_solar_limit(
            production_wh=[9000, 9000], consumption_wh=[400, 400],
            feed_in_limit_w=None, interval_h=1.0,
            free_capacity_wh=1000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, -1))

    def test_no_production_now_is_neutral(self):
        """production[0] == 0 (nighttime) -> window length 0 -> (0, -1)."""
        floor, cap = compute_solar_limit(
            production_wh=[0, 5000], consumption_wh=[100, 100],
            feed_in_limit_w=1000, interval_h=1.0,
            free_capacity_wh=5000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, -1))

    def test_surplus_below_limit_no_clip(self):
        """Surplus stays below the feed-in limit everywhere -> (0, -1)."""
        floor, cap = compute_solar_limit(
            production_wh=[2000, 2000], consumption_wh=[500, 500],
            feed_in_limit_w=3000, interval_h=1.0,
            free_capacity_wh=5000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, -1))


class TestComputeSolarLimitCaseA(unittest.TestCase):
    """Case A: before the clip window -> reservation cap."""

    # Shared scenario: clip window starts at slot 2 (clip 2600 Wh/slot at
    # slots 2 and 3), total clip 5200 Wh.
    #   production = [3000, 3000, 9000, 9000, 0], consumption = [400]*5
    #   surplus    = [2600, 2600, 8600, 8600]  (prod_end=4)
    #   feed_allow = 6000 Wh/slot -> clip = [0, 0, 2600, 2600]
    PRODUCTION = [3000, 3000, 9000, 9000, 0]
    CONSUMPTION = [400] * 5
    FEED_IN_LIMIT_W = 6000

    def test_reservation_cap(self):
        """free=8000, max=10000 -> allowed=8000-5200=2800, hours_before=2
        -> cap = int(2800/2) = 1400, floor = 0."""
        floor, cap = compute_solar_limit(
            self.PRODUCTION, self.CONSUMPTION, self.FEED_IN_LIMIT_W,
            interval_h=1.0, free_capacity_wh=8000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, 1400))

    def test_reservation_blocks_when_free_capacity_too_small(self):
        """free=5000 <= total_clip(5200) -> (0, 0), PV charging blocked."""
        floor, cap = compute_solar_limit(
            self.PRODUCTION, self.CONSUMPTION, self.FEED_IN_LIMIT_W,
            interval_h=1.0, free_capacity_wh=5000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (0, 0))


class TestComputeSolarLimitCaseB(unittest.TestCase):
    """Case B: inside the clip window -> floor + capacity-preserving cap."""

    # Currently clipping: production=[8000,8000,0], consumption=[400,400,0]
    #   surplus = [7600, 7600] (prod_end=2), feed_allow = 6000/slot
    #   clip = [1600, 1600] -> floor = clip[0]/1.0 = 1600
    #   total_surplus (raw) = 15200, remaining_clip = 3200
    PRODUCTION = [8000, 8000, 0]
    CONSUMPTION = [400, 400, 0]
    FEED_IN_LIMIT_W = 6000

    def test_scarcity_cap_equals_floor(self):
        """free=2000 <= remaining_clip(3200) -> extra=0 -> cap == floor."""
        floor, cap = compute_solar_limit(
            self.PRODUCTION, self.CONSUMPTION, self.FEED_IN_LIMIT_W,
            interval_h=1.0, free_capacity_wh=2000, max_capacity_wh=10000)
        self.assertEqual(floor, 1600)
        self.assertEqual(cap, floor)

    def test_abundance_no_cap_needed(self):
        """free=20000 >= total raw surplus(15200) -> (floor, -1)."""
        floor, cap = compute_solar_limit(
            self.PRODUCTION, self.CONSUMPTION, self.FEED_IN_LIMIT_W,
            interval_h=1.0, free_capacity_wh=20000, max_capacity_wh=30000)
        self.assertEqual((floor, cap), (1600, -1))

    def test_extra_spread_over_remaining_slots(self):
        """free=5000: between remaining_clip(3200) and total surplus(15200).
        extra = 5000-3200 = 1800, remaining_prod_h = 2
        -> cap = int(1600 + 1800/2) = 2500."""
        floor, cap = compute_solar_limit(
            self.PRODUCTION, self.CONSUMPTION, self.FEED_IN_LIMIT_W,
            interval_h=1.0, free_capacity_wh=5000, max_capacity_wh=10000)
        self.assertEqual((floor, cap), (1600, 2500))


class TestComputeSolarLimitHeadroom(unittest.TestCase):
    """Headroom applied to the forecast surplus before clip computation."""

    def test_headroom_creates_a_clip_slot_that_raw_surplus_would_miss(self):
        """production=5500, consumption=500 -> raw surplus=5000 (< limit
        6000, no clip with headroom=1.0). With headroom=1.25 the
        headroom-adjusted surplus is 5000*1.25=6250 > 6000 -> clip=250,
        floor=250 (Case B). free_capacity is large enough that the raw
        total surplus (5000) still fits -> cap stays -1.
        """
        floor_neutral, cap_neutral = compute_solar_limit(
            [5500], [500], feed_in_limit_w=6000, interval_h=1.0,
            free_capacity_wh=10000, max_capacity_wh=10000, headroom=1.0)
        self.assertEqual((floor_neutral, cap_neutral), (0, -1))

        floor_headroom, cap_headroom = compute_solar_limit(
            [5500], [500], feed_in_limit_w=6000, interval_h=1.0,
            free_capacity_wh=10000, max_capacity_wh=10000, headroom=1.25)
        self.assertEqual((floor_headroom, cap_headroom), (250, -1))


class TestComputeSolarLimitPartialSlot(unittest.TestCase):
    """slot0_hours: remaining hours in the current (partial) interval."""

    def test_slot0_hours_halved_doubles_the_slot0_floor(self):
        """production=5000, consumption=500 -> surplus=4500.
        slot0_hours=0.5 -> feed_allow = 2000*0.5 = 1000
                         -> clip_wh[0] = 4500-1000 = 3500
                         -> floor = clip_wh[0] / 0.5 = 2 * clip_wh[0] = 7000
        free_capacity (20000) covers the raw total surplus (4500) -> cap=-1.
        """
        floor, cap = compute_solar_limit(
            [5000], [500], feed_in_limit_w=2000, interval_h=1.0,
            free_capacity_wh=20000, max_capacity_wh=20000,
            headroom=1.0, slot0_hours=0.5)
        clip_wh_slot0 = 3500
        self.assertEqual(floor, 2 * clip_wh_slot0)
        self.assertEqual((floor, cap), (7000, -1))


class TestComputeSolarLimit15MinInterval(unittest.TestCase):
    """15-minute resolution variant of a simple Case B scenario."""

    def test_quarter_hour_interval(self):
        """3 slots of 15 min: 1200 Wh production (4800 W), 100 Wh
        consumption (400 W) per slot; feed_in_limit_w=3000.
          surplus_wh = 1100/slot, feed_allow_wh = 3000*0.25 = 750/slot
          clip_wh = 350/slot -> floor = 350/0.25 = 1400
        total_surplus = 3300 Wh <= free_capacity(10000) -> cap = -1.
        """
        floor, cap = compute_solar_limit(
            [1200, 1200, 1200, 0], [100, 100, 100, 100],
            feed_in_limit_w=3000, interval_h=0.25,
            free_capacity_wh=10000, max_capacity_wh=15000)
        self.assertEqual((floor, cap), (1400, -1))


class TestComputeSolarLimitReferenceDay(unittest.TestCase):
    """Spot check against docs/development/solar-limit-evaluation.md scenario 1.

    At 11:00 the documented floor sequence is 1200 -> 2500 -> 2400 -> 1400 W
    as the window progresses; this test reproduces only the first (1200 W)
    value for the slot evaluated at 11:00, per the task's "keep it simple"
    guidance.
    """

    def test_reference_day_first_clip_floor(self):
        """production[0]=7600, consumption=400/slot, limit=6000 (1h slots):
        surplus[0] = 7200, feed_allow[0] = 6000 -> clip[0] = 1200
        -> floor = 1200 W. free_capacity is large enough that the whole
        window's raw surplus (37400 Wh) fits -> cap = -1.
        """
        production = [7600, 8900, 8800, 7800, 6300, 0]
        consumption = [400] * 6
        floor, cap = compute_solar_limit(
            production, consumption, feed_in_limit_w=6000, interval_h=1.0,
            free_capacity_wh=50000, max_capacity_wh=50000)
        self.assertEqual(floor, 1200)
        self.assertEqual(cap, -1)


class TestMergeLimits(unittest.TestCase):
    """merge_limits: final = max(floor, min(active caps))."""

    def test_no_caps_returns_no_limit(self):
        """Empty caps list -> -1, regardless of the floor."""
        self.assertEqual(merge_limits(1500, []), -1)

    def test_all_caps_none_or_sentinel_returns_no_limit(self):
        """None and -1 both mean 'no opinion' -> -1, floor irrelevant."""
        self.assertEqual(merge_limits(0, [None, -1]), -1)

    def test_positive_floor_with_unlimited_cap_returns_no_limit(self):
        """A single -1 ('no cap') satisfies any floor -> -1."""
        self.assertEqual(merge_limits(1500, [-1]), -1)

    def test_strictest_cap_wins_when_floor_is_zero(self):
        """floor=0, caps=[500, 300] -> 300 (the strictest cap)."""
        self.assertEqual(merge_limits(0, [500, 300]), 300)

    def test_floor_overrides_a_looser_cap(self):
        """floor=1500, caps=[500] -> 1500 (floor > cap)."""
        self.assertEqual(merge_limits(1500, [500]), 1500)

    def test_floor_overrides_a_blocking_cap(self):
        """floor=1500, caps=[0] -> 1500: the floor overrides even a cap
        that would otherwise block charging entirely."""
        self.assertEqual(merge_limits(1500, [0]), 1500)

    def test_zero_floor_with_blocking_cap_blocks(self):
        """floor=0, caps=[0] -> 0 (nothing to override with)."""
        self.assertEqual(merge_limits(0, [0]), 0)


if __name__ == '__main__':
    unittest.main()
