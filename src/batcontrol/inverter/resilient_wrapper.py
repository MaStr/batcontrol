"""Resilient inverter proxy - normalizes backend communication failures.

This is a thin __getattr__ proxy around any inverter backend. It does not
re-declare the inverter interface; it simply delegates every attribute and
method to the wrapped inverter and only intercepts method calls that raise.

Behaviour:
- Before the first successful set_mode_* call, errors propagate unchanged, so
  configuration mistakes fail fast at startup.
- After that an inverter failure becomes InverterCommunicationError; the caller
  (core.run) skips the control cycle and retries on the next scheduled run, so
  the loop interval is the natural backoff and no decision is made on stale
  data.
- If the inverter stays unreachable past the tolerance window, the failure is
  escalated to InverterOutageError, which terminates batcontrol.
- shutdown() and refresh_api_values() are best-effort: they never abort a cycle.
"""

import time
import logging
import functools

from .exceptions import InverterCommunicationError, InverterOutageError

logger = logging.getLogger(__name__)

# Default outage tolerance: 24 minutes (to handle firmware upgrades)
DEFAULT_OUTAGE_TOLERANCE_SECONDS = 24 * 60

# Commands whose first success means the inverter is initialized. From then on
# failures are tolerated instead of treated as fail-fast configuration errors.
_COMMAND_METHODS = frozenset({
    'set_mode_force_charge', 'set_mode_avoid_discharge',
    'set_mode_allow_discharge', 'set_mode_limit_battery_charge',
})

# Best-effort methods never abort a control cycle - just log and move on.
_BEST_EFFORT_METHODS = frozenset({'refresh_api_values', 'shutdown'})


class ResilientInverterWrapper:
    """Delegates to an inverter, turning communication failures into signals."""

    def __init__(self, inverter, outage_tolerance_seconds=DEFAULT_OUTAGE_TOLERANCE_SECONDS):
        self._inverter = inverter
        self._outage_tolerance = outage_tolerance_seconds
        self._initialized = False           # True after first successful set_mode_*
        self._outage_start = None            # timestamp of the first failure

    @property
    def wrapped_inverter(self):
        """Access to the wrapped inverter for advanced use cases."""
        return self._inverter

    def __getattr__(self, name):
        """Delegate to the wrapped inverter; guard method calls."""
        inverter = self.__dict__.get('_inverter')
        if inverter is None:
            raise AttributeError(name)
        attr = getattr(inverter, name)
        if not callable(attr) or name.startswith('__'):
            return attr
        return self._guard(name, attr)

    def _guard(self, name, method):
        best_effort = name in _BEST_EFFORT_METHODS
        is_command = name in _COMMAND_METHODS

        @functools.wraps(method)
        def guarded(*args, **kwargs):
            try:
                result = method(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-exception-caught
                if best_effort:
                    logger.debug(
                        "Inverter '%s' failed (best-effort, ignored): %s", name, e)
                    return None
                self._on_failure(name, e)  # always raises
                return None  # unreachable, keeps linters happy
            if self._outage_start is not None:
                logger.info(
                    "Inverter connection restored after %.1f min outage",
                    (time.time() - self._outage_start) / 60)
                self._outage_start = None
            if is_command and not self._initialized:
                logger.info("Inverter initialization complete (first set_mode succeeded)")
                self._initialized = True
            return result
        return guarded

    def _on_failure(self, name, error):
        """Classify a failure. Never returns - always raises."""
        # Before initialization a failure is most likely a configuration error.
        if not self._initialized:
            logger.error(
                "Inverter failure before initialization (config error?): %s", error)
            raise error

        now = time.time()
        if self._outage_start is None:
            self._outage_start = now
        outage = now - self._outage_start

        if outage > self._outage_tolerance:
            raise InverterOutageError(
                f"Inverter unreachable for {outage / 60:.1f} min during '{name}'",
                outage_duration_seconds=outage,
            ) from error

        logger.warning(
            "Inverter communication failed for '%s' (outage: %.1f min, "
            "tolerance: %.1f min). Skipping this control cycle.",
            name, outage / 60, self._outage_tolerance / 60)
        raise InverterCommunicationError(name) from error
