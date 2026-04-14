"""Scheduler Thread Module

This module provides a scheduler thread that runs alongside the main control loop.
It uses the schedule library to run periodic tasks at specific intervals.

The module exposes global functions for scheduling jobs that can be called from any context:
- schedule_every(): Schedule a job to run at regular intervals
- schedule_at(): Schedule a job to run at a specific time each day
- schedule_once(): Schedule a job to run once at a specific date and time
- clear_jobs(): Clear all scheduled jobs
- get_jobs(): Get all currently scheduled jobs
- reset_scheduler(): Replace the internal scheduler instance (primarily for tests)

Implementation note:
    The ``schedule`` library keeps its scheduled jobs on a module-level
    ``default_scheduler`` singleton.  Sharing that singleton between batcontrol
    and its test suite caused jobs from one test to leak into the next and
    slowed the test run down considerably (see issue #325).  To keep our
    state isolated from the library's global state we maintain our own
    ``schedule.Scheduler`` instance and route all scheduling calls through it.
"""

import threading
import logging
import schedule
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Private module-level job registry.  Using our own ``schedule.Scheduler``
# instance (instead of ``schedule.default_scheduler``) keeps our scheduled
# jobs isolated from any other consumer of the ``schedule`` library and makes
# it possible to fully reset the state between tests.  The variable is named
# ``_job_registry`` on purpose so it cannot be confused with either the
# ``schedule`` library module or the ``SchedulerThread`` class below.
_job_registry: schedule.Scheduler = schedule.Scheduler()


def _get_job_registry() -> schedule.Scheduler:
    """Return the module-level ``schedule.Scheduler`` instance used by batcontrol."""
    return _job_registry


def reset_scheduler() -> schedule.Scheduler:
    """Replace the internal job registry with a fresh ``schedule.Scheduler``.

    This is mainly useful in tests to guarantee a clean slate between test
    cases, even if a previous test forgot to clear its jobs.

    Returns:
        The new ``schedule.Scheduler`` instance.
    """
    global _job_registry
    logger.debug("Resetting batcontrol job registry")
    _job_registry = schedule.Scheduler()
    return _job_registry


# Global scheduling functions that can be called from any context

def schedule_every(interval: int, unit: str, job: Callable, job_name: str = ""):
    """
    Schedule a job to run at regular intervals (globally accessible)

    Args:
        interval: The interval value (e.g., 5 for "every 5 minutes")
        unit: The unit of time ('seconds', 'minutes', 'hours', 'days', 'weeks')
        job: The callable function to execute
        job_name: Optional name for the job (for logging purposes)

    Returns:
        The scheduled job object
    """
    name = job_name or job.__name__
    logger.info("Scheduling job '%s' to run every %d %s", name, interval, unit)

    if unit not in ['seconds', 'minutes', 'hours', 'days', 'weeks']:
        raise ValueError(
            f"Invalid unit '{unit}'. Must be one of: "
            "['seconds', 'minutes', 'hours', 'days', 'weeks']"
        )

    # Create the scheduler based on the unit
    task = _get_job_registry().every(interval)
    obtained_unit = task.__getattribute__(unit)

    # Wrap the job to catch exceptions and add logging
    def wrapped_job():
        try:
            logger.info("Running scheduled job: %s", name)
            job()
            logger.info("Completed scheduled job: %s", name)
        except Exception as e:
            logger.error("Error in scheduled job '%s': %s", name, e, exc_info=True)

    wrapped_job.__name__ = name
    return obtained_unit.do(wrapped_job)


def schedule_at(time_str: str, job: Callable, job_name: str = ""):
    """
    Schedule a job to run at a specific time each day (globally accessible)

    Args:
        time_str: Time string in HH:MM format (e.g., "14:30")
        job: The callable function to execute
        job_name: Optional name for the job (for logging purposes)

    Returns:
        The scheduled job object
    """
    name = job_name or job.__name__
    logger.info("Scheduling job '%s' to run daily at %s", name, time_str)

    # Wrap the job to catch exceptions and add logging
    def wrapped_job():
        try:
            logger.debug("Running scheduled job: %s", name)
            job()
            logger.debug("Completed scheduled job: %s", name)
        except Exception as e:
            logger.error("Error in scheduled job '%s': %s", name, e, exc_info=True)

    return _get_job_registry().every().day.at(time_str).do(wrapped_job)


def schedule_once(time: str, job: Callable, job_name: str = ""):
    """
    Schedule a job to run once at a specific date and time (globally accessible)

    Args:
        time: The date and time to run the job
        job: The callable function to execute
        job_name: Optional name for the job (for logging purposes)

    Returns:
        The scheduled job object
    """
    name = job_name or job.__name__
    logger.info("Scheduling job '%s' to run once at %s", name, time)

    # Wrap the job to catch exceptions and add logging
    def wrapped_job():
        try:
            logger.info("Running scheduled one-time job: %s", name)
            job()
            logger.info("Completed scheduled one-time job: %s", name)
            return schedule.CancelJob
        except Exception as e:
            logger.error("Error in scheduled one-time job '%s': %s", name, e, exc_info=True)

    return (
        _get_job_registry()
        .every()
        .day.at(time)
        .do(wrapped_job)
        .tag(f"one_time_{name}")
    )


def clear_jobs():
    """Clear all scheduled jobs (globally accessible)"""
    logger.info("Clearing all scheduled jobs")
    _get_job_registry().clear()


def get_jobs():
    """Get all currently scheduled jobs (globally accessible)"""
    return _get_job_registry().get_jobs()


class SchedulerThread:
    """Thread-based scheduler that runs periodic tasks using the schedule library.

    This class manages the scheduler thread that executes the scheduled jobs.
    For scheduling jobs, use the global functions schedule_every(), schedule_at(), etc.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        logger.info("Scheduler thread initialized")

    def start(self):
        """Start the scheduler thread"""
        with self._lock:
            if self._running:
                logger.warning("Scheduler thread is already running")
                return

            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="SchedulerThread")
            self._thread.start()
            logger.info("Scheduler thread started")

    def stop(self):
        """Stop the scheduler thread"""
        with self._lock:
            if not self._running:
                logger.warning("Scheduler thread is not running")
                return

            logger.info("Stopping scheduler thread...")
            self._stop_event.set()
            self._running = False

        # Wait for thread to finish (with timeout)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Scheduler thread did not stop gracefully")
            else:
                logger.info("Scheduler thread stopped")

    def _run(self):
        """Main loop for the scheduler thread"""
        logger.debug("Scheduler thread loop started")
        while not self._stop_event.is_set():
            try:
                logger.debug("Scheduler thread checking for pending jobs")
                job_registry = _get_job_registry()
                job_registry.run_pending()
                # Currently, we do not schedule any jobs dynamically here.
                # ``idle_seconds`` is a property on ``schedule.Scheduler``
                # (not a method) and returns either ``None`` when no jobs
                # are scheduled or a ``float`` number of seconds otherwise.
                n = job_registry.idle_seconds
                if n is None:
                    n = 10  # Default sleep time if no jobs are scheduled
                if n > 0:
                    n = min(n, 300)  # Cap sleep time to 300 seconds
                    logger.debug("Scheduler thread sleeping for %s seconds.", n)
                    # Use the stop event as an interruptible sleep so that a
                    # call to stop() does not have to wait up to 300 seconds.
                    if self._stop_event.wait(timeout=n):
                        break
            except Exception as e:
                logger.error("Error in scheduler thread: %s", e, exc_info=True)

        logger.debug("Scheduler thread loop ended")

    def is_running(self) -> bool:
        """Check if the scheduler thread is running"""
        return self._running

    # Convenience methods that delegate to the global functions
    # These methods are kept for backward compatibility

    def schedule_every(self, interval: int, unit: str, job: Callable, job_name: str = ""):
        """
        Schedule a job to run at regular intervals

        Note: This method delegates to the global schedule_every() function.
        You can also call schedule_every() directly from any context.

        Args:
            interval: The interval value (e.g., 5 for "every 5 minutes")
            unit: The unit of time ('seconds', 'minutes', 'hours', 'days', 'weeks')
            job: The callable function to execute
            job_name: Optional name for the job (for logging purposes)

        Returns:
            The scheduled job object
        """
        return schedule_every(interval, unit, job, job_name)

    def schedule_at(self, time_str: str, job: Callable, job_name: str = ""):
        """
        Schedule a job to run at a specific time each day

        Note: This method delegates to the global schedule_at() function.
        You can also call schedule_at() directly from any context.

        Args:
            time_str: Time string in HH:MM format (e.g., "14:30")
            job: The callable function to execute
            job_name: Optional name for the job (for logging purposes)

        Returns:
            The scheduled job object
        """
        return schedule_at(time_str, job, job_name)

    def schedule_once(self, time: str, job: Callable, job_name: str = ""):
        """
        Schedule a job to run once at a specific date and time

        Note: This method delegates to the global schedule_once() function.
        You can also call schedule_once() directly from any context.

        Args:
            time: The date and time to run the job
            job: The callable function to execute
            job_name: Optional name for the job (for logging purposes)

        Returns:
            The scheduled job object
        """
        return schedule_once(time, job, job_name)

    def clear_jobs(self):
        """
        Clear all scheduled jobs

        Note: This method delegates to the global clear_jobs() function.
        You can also call clear_jobs() directly from any context.
        """
        return clear_jobs()

    def get_jobs(self):
        """
        Get all currently scheduled jobs

        Note: This method delegates to the global get_jobs() function.
        You can also call get_jobs() directly from any context.
        """
        return get_jobs()
