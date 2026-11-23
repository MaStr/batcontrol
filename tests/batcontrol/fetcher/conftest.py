"""Shared fixtures for the fetcher tests."""

import functools
import threading

import pytest

from batcontrol.fetcher import relaxed_caching


class FakeClock:
    """Deterministic, thread-safe clock.

    Each call returns the current time and then advances it by ``tick``
    seconds, so consecutive calls yield distinct timestamps without sleeping.
    """

    def __init__(self, now=1_000_000.0, tick=0.0):
        self.now = now
        self.tick = tick
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            current = self.now
            self.now += self.tick
            return current

    def advance(self, seconds):
        """Move the clock forward by ``seconds``."""
        with self._lock:
            self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    """Drive both RelaxedCaching clocks from one FakeClock.

    RelaxedCaching uses ``wall_clock`` for entry keys and ages, while the
    in-memory expiry is handled by ``cachetools.TTLCache`` with its own
    monotonic timer. Patch the ``TTLCache`` name in the module so the cache is
    built with the fake clock as timer; pass the clock as ``wall_clock`` too.
    """
    clock = FakeClock()
    monkeypatch.setattr(
        relaxed_caching,
        'TTLCache',
        functools.partial(relaxed_caching.TTLCache, timer=clock),
    )
    return clock
