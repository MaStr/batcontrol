"""Persistence tests for the relaxed provider cache."""

import os
import stat

import pytest

from batcontrol.fetcher.relaxed_caching import CacheMissError, RelaxedCaching


class MutableWallClock:
    """Controllable wall clock for cache-age tests."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_persisted_entry_survives_restart_with_original_age(tmp_path):
    """A restarted process reloads a fresh entry without resetting its age."""
    path = tmp_path / "forecast.json"
    clock = MutableWallClock()
    first = RelaxedCaching(persistence_path=path, wall_clock=clock)
    first.store_new_entry({"result": "last-known-good"})

    clock.now += 2 * 3600
    restarted = RelaxedCaching(persistence_path=path, wall_clock=clock)

    assert restarted.get_last_entry() == {"result": "last-known-good"}
    assert restarted.get_cache_info()["cache_age_seconds"] == 2 * 3600


def test_wall_clock_jump_does_not_change_memory_only_ttl_behavior():
    """Persistence support leaves the existing monotonic memory TTL intact."""
    clock = MutableWallClock()
    cache = RelaxedCaching(wall_clock=clock)
    cache.store_new_entry({"result": "in-memory"})

    clock.now += 12 * 3600

    assert cache.get_last_entry() == {"result": "in-memory"}


def test_persisted_entry_is_usable_just_before_twelve_hours(tmp_path):
    """Reloaded data keeps the existing strict less-than-12-hour boundary."""
    path = tmp_path / "forecast.json"
    clock = MutableWallClock()
    first = RelaxedCaching(persistence_path=path, wall_clock=clock)
    first.store_new_entry({"result": "last-known-good"})

    clock.now += 12 * 3600 - 0.001
    restarted = RelaxedCaching(persistence_path=path, wall_clock=clock)

    assert restarted.get_last_entry() == {"result": "last-known-good"}


def test_persisted_entry_expires_at_original_twelve_hour_boundary(tmp_path):
    """Restarting cannot extend an entry beyond its original TTL."""
    path = tmp_path / "forecast.json"
    clock = MutableWallClock()
    first = RelaxedCaching(persistence_path=path, wall_clock=clock)
    first.store_new_entry({"result": "last-known-good"})

    clock.now += 12 * 3600
    restarted = RelaxedCaching(persistence_path=path, wall_clock=clock)

    with pytest.raises(CacheMissError):
        restarted.get_last_entry()


def test_malformed_persisted_entry_is_ignored_without_leaking_contents(
        tmp_path, caplog):
    """A corrupt cache behaves as a miss and does not expose its contents."""
    path = tmp_path / "forecast.json"
    path.write_text("not-json-secret-key", encoding="utf-8")

    cache = RelaxedCaching(persistence_path=path)

    with pytest.raises(CacheMissError):
        cache.get_last_entry()
    assert "secret-key" not in caplog.text


def test_failed_atomic_replace_keeps_previous_disk_entry_and_new_memory_entry(
        tmp_path, mocker):
    """A disk failure cannot discard either the old file or new in-memory data."""
    path = tmp_path / "forecast.json"
    clock = MutableWallClock()
    cache = RelaxedCaching(persistence_path=path, wall_clock=clock)
    cache.store_new_entry({"value": "old"})
    original_file = path.read_bytes()

    clock.now += 60
    mocker.patch(
        "batcontrol.fetcher.relaxed_caching.os.replace",
        side_effect=OSError("simulated replace failure"),
    )
    cache.store_new_entry({"value": "new"})

    assert cache.get_last_entry() == {"value": "new"}
    assert path.read_bytes() == original_file

    restarted = RelaxedCaching(persistence_path=path, wall_clock=clock)
    assert restarted.get_last_entry() == {"value": "old"}


def test_persistent_file_is_private(tmp_path):
    """Raw provider data is written with owner-only permissions on POSIX."""
    path = tmp_path / "forecast.json"
    cache = RelaxedCaching(persistence_path=path)

    cache.store_new_entry({"result": "forecast"})

    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_clear_cache_removes_persisted_entry(tmp_path):
    """Clearing a cache removes both memory and its restart copy."""
    path = tmp_path / "forecast.json"
    cache = RelaxedCaching(persistence_path=path)
    cache.store_new_entry({"result": "forecast"})

    cache.clear_cache()

    assert not path.exists()
    with pytest.raises(CacheMissError):
        cache.get_last_entry()


def test_orphaned_temporary_file_is_not_loaded(tmp_path):
    """An interrupted write cannot become the restart cache."""
    path = tmp_path / "forecast.json"
    temporary_path = tmp_path / "forecast.json.tmp"
    temporary_path.write_text(
        '{"stored_at": 1000000, "data": {"bad": true}}',
        encoding="utf-8",
    )

    cache = RelaxedCaching(
        persistence_path=path,
        wall_clock=MutableWallClock(),
    )

    with pytest.raises(CacheMissError):
        cache.get_last_entry()
