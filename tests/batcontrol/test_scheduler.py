"""Tests for the batcontrol scheduler module.

These tests guard against the regression described in issue #325 where
scheduled jobs were stored on the ``schedule`` library's global singleton
and therefore leaked state across test cases.
"""

import schedule

from batcontrol import scheduler


def _noop():
    """Trivial callable used for scheduling."""


def test_scheduler_uses_private_instance():
    """batcontrol must not share state with ``schedule.default_scheduler``."""
    schedule.default_scheduler.clear()
    scheduler.reset_scheduler()

    scheduler.schedule_every(1, "minutes", _noop, "private-instance-job")

    assert len(scheduler.get_jobs()) == 1
    assert len(schedule.default_scheduler.jobs) == 0


def test_reset_scheduler_drops_pending_jobs():
    """reset_scheduler() must guarantee an empty scheduler instance."""
    scheduler.schedule_every(1, "minutes", _noop, "job-before-reset")
    assert len(scheduler.get_jobs()) >= 1

    scheduler.reset_scheduler()

    assert scheduler.get_jobs() == []


def test_clear_jobs_clears_batcontrol_only():
    """clear_jobs() must only clear our instance, not the library singleton."""
    schedule.default_scheduler.clear()
    schedule.default_scheduler.every(1).minutes.do(_noop)

    scheduler.schedule_every(1, "minutes", _noop, "isolated-job")
    scheduler.clear_jobs()

    assert scheduler.get_jobs() == []
    # The external singleton remains untouched.
    assert len(schedule.default_scheduler.jobs) == 1

    # House-keeping so we do not leak into other tests.
    schedule.default_scheduler.clear()


def test_schedule_every_rejects_invalid_unit():
    """Invalid time units must raise ValueError."""
    import pytest

    with pytest.raises(ValueError):
        scheduler.schedule_every(1, "fortnights", _noop, "bad-unit")
