"""Shared pytest fixtures for the batcontrol test suite.

The ``schedule`` library historically kept all scheduled jobs on a
module-level singleton.  Even though ``batcontrol.scheduler`` now owns a
private ``schedule.Scheduler`` instance (see issue #325), jobs from one test
can still accumulate on that instance if the test code under exercise calls
``schedule_every`` / ``schedule_once`` and the test itself does not clean up.

To keep tests reliably isolated we reset the scheduler both before and after
every test.  ``reset_scheduler`` swaps in a brand new ``schedule.Scheduler``
instance which guarantees there are no leftover jobs regardless of what a
previous test did.
"""

import pytest

from batcontrol.scheduler import reset_scheduler


@pytest.fixture(autouse=True)
def _isolate_scheduler():
    """Reset the batcontrol scheduler before and after each test."""
    reset_scheduler()
    try:
        yield
    finally:
        reset_scheduler()
