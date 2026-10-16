"""Tests for the immutable per-cycle forecast snapshot."""
import dataclasses

import numpy as np
import pytest

from batcontrol.interval_utils import align_to_interval
from batcontrol.web.snapshot import ForecastSnapshot


def test_build_copies_numpy_arrays_into_tuples():
    """The reader must not be able to see a mutated array."""
    prices = np.array([0.30, 0.25])
    snapshot = ForecastSnapshot.build(
        run_time=1000.0, interval_minutes=60, prices=prices)
    prices[0] = 99.0
    assert snapshot.prices == (0.30, 0.25)


def test_snapshot_is_frozen():
    snapshot = ForecastSnapshot.build(run_time=1000.0, interval_minutes=60)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.run_time = 2000.0


def test_missing_series_become_empty_tuples():
    snapshot = ForecastSnapshot.build(run_time=1000.0, interval_minutes=15)
    assert snapshot.prices == ()
    assert snapshot.consumption == ()
    assert snapshot.has_data() is False


def test_slot_zero_starts_at_the_interval_boundary():
    """Slot 0 is the interval containing the run time, not the run time."""
    run_time = 1792065332.0  # somewhere inside an hour
    snapshot = ForecastSnapshot.build(
        run_time=run_time, interval_minutes=60, prices=[0.3])
    assert snapshot.base_time == align_to_interval(run_time, 60)
    assert snapshot.base_time <= run_time
    assert snapshot.slot_start_ms(0) == int(snapshot.base_time * 1000)
    assert snapshot.slot_start_ms(1) - snapshot.slot_start_ms(0) == 3600 * 1000


@pytest.mark.parametrize('interval,factor', [(60, 1), (15, 4)])
def test_power_conversion_follows_the_interval(interval, factor):
    """Wh per interval and average W differ by 4x at 15-minute resolution."""
    snapshot = ForecastSnapshot.build(
        run_time=0.0, interval_minutes=interval, consumption=[250.0])
    slot = snapshot.slots(snapshot.consumption, include_power=True)[0]
    assert slot['value'] == 250.0
    assert slot['power_w'] == 250.0 * factor


def test_slots_carry_index_and_bounds():
    snapshot = ForecastSnapshot.build(
        run_time=0.0, interval_minutes=60, prices=[0.1, 0.2, 0.3])
    slots = snapshot.slots(snapshot.prices, digits=4)
    assert [slot['slot'] for slot in slots] == [0, 1, 2]
    assert slots[0]['time_end'] == slots[1]['time_start']
    assert 'power_w' not in slots[0]


def test_price_rounding_keeps_four_digits():
    snapshot = ForecastSnapshot.build(
        run_time=0.0, interval_minutes=60, prices=[0.30812345])
    assert snapshot.slots(snapshot.prices, digits=4)[0]['value'] == 0.3081
