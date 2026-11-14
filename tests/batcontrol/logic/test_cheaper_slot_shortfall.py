"""CheaperSlotShortfall: demand against the charge capacity of cheaper slots.

The balance is shared by the discharge reserve and the grid recharge rule,
see test_reserve_behind_cheaper_slot.py and test_grid_charge_deferral.py
for the rules themselves.
"""
import pytest

from batcontrol.logic.cheaper_slot_shortfall import CheaperSlotShortfall

SLOT_CAPACITY = 1000
BATTERY = 8000


def _shortfall():
    return CheaperSlotShortfall(SLOT_CAPACITY, BATTERY)


def test_demand_before_any_cheaper_slot_counts_in_full():
    """No cheaper slot has charged yet, so the battery room is no limit."""
    shortfall = _shortfall()

    assert shortfall.add_demand(20000)

    assert shortfall.energy == 20000
    assert shortfall.charge_capacity_at_shortfall == 0
    assert shortfall.battery_room_at_shortfall == 0


def test_cheaper_slots_reduce_the_shortfall():
    shortfall = _shortfall()
    assert shortfall.add_cheaper_slot()
    assert shortfall.add_cheaper_slot()

    assert shortfall.add_demand(3500)

    assert shortfall.energy == 1500
    assert shortfall.demand_at_shortfall == 3500
    assert shortfall.charge_capacity_at_shortfall == 2000
    assert shortfall.battery_room_at_shortfall == 0


def test_covered_demand_is_no_shortfall():
    shortfall = _shortfall()
    shortfall.add_cheaper_slot()

    assert not shortfall.add_demand(800)

    assert shortfall.energy == 0


def test_largest_gap_is_kept():
    """A later slot that the cheaper slots cover does not lower the
    shortfall, and does not replace the balance recorded for it."""
    shortfall = _shortfall()
    shortfall.add_cheaper_slot()
    assert shortfall.add_demand(1500)
    shortfall.add_cheaper_slot()
    shortfall.add_cheaper_slot()

    assert not shortfall.add_demand(1000)

    assert shortfall.energy == 500
    assert shortfall.demand_at_shortfall == 1500
    assert shortfall.charge_capacity_at_shortfall == 1000


def test_shortfall_is_limited_to_the_room_in_the_battery():
    """Three slots charge 3000 Wh of the 8000 Wh the battery takes: of the
    17000 Wh that are missing only 5000 Wh fit."""
    shortfall = _shortfall()
    for _ in range(3):
        assert shortfall.add_cheaper_slot()

    assert shortfall.add_demand(20000)

    assert shortfall.energy == 5000
    assert shortfall.battery_room_at_shortfall == 5000


def test_room_does_not_grow_again_with_later_demand():
    """Demand between the cheaper slots empties the battery again, but
    energy kept now had to fit at the tightest point before."""
    shortfall = _shortfall()
    for _ in range(6):
        shortfall.add_cheaper_slot()
    shortfall.add_demand(4000)
    shortfall.add_cheaper_slot()

    shortfall.add_demand(9000)

    assert shortfall.headroom == 2000
    assert shortfall.energy == 2000


def test_walk_ends_when_the_cheaper_slots_fill_the_battery():
    shortfall = _shortfall()
    for _ in range(7):
        assert shortfall.add_cheaper_slot()

    assert not shortfall.add_cheaper_slot()
    assert shortfall.headroom == 0


@pytest.mark.parametrize('battery', [0, -500])
def test_battery_that_takes_nothing_ends_the_walk_at_once(battery):
    shortfall = CheaperSlotShortfall(SLOT_CAPACITY, battery)

    assert not shortfall.add_cheaper_slot()
