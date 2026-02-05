"""Resilient Inverter Wrapper

This module provides a wrapper class that adds resilience to any inverter
implementation. It handles temporary connection outages by caching values
and providing graceful degradation during firmware upgrades or network issues.

Key features:
- Failures before first successful set_mode call propagate immediately
  (config errors should fail fast - all API calls are validated during init)
- After initialization, failures use cached values for up to 24 minutes (configurable)
- After timeout, raises InverterOutageError to signal permanent failure
- Automatic recovery when connection is restored
"""

import time
import logging
from typing import Optional, Any, Callable
from dataclasses import dataclass, field

from .inverter_interface import InverterInterface
from .exceptions import InverterOutageError

logger = logging.getLogger(__name__)

# Default outage tolerance: 24 minutes (to handle firmware upgrades)
DEFAULT_OUTAGE_TOLERANCE_SECONDS = 24 * 60

# Default retry backoff: 60 seconds (don't hammer inverter after failure)
DEFAULT_RETRY_BACKOFF_SECONDS = 60


@dataclass
class CachedValues:
    """Container for cached inverter values during outages."""
    soc: Optional[float] = None
    stored_energy: Optional[float] = None
    stored_usable_energy: Optional[float] = None
    capacity: Optional[float] = None
    free_capacity: Optional[float] = None
    max_capacity: Optional[float] = None
    designed_capacity: Optional[float] = None
    usable_capacity: Optional[float] = None
    last_update_time: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        """Check if we have at least the essential cached values."""
        return self.soc is not None and self.capacity is not None


class ResilientInverterWrapper(InverterInterface):
    """
    Wrapper that adds resilience to any inverter implementation.

    This wrapper intercepts all inverter calls and provides:
    1. Immediate failure on first run (config errors)
    2. Cached value fallback during temporary outages
    3. Timeout after configurable period (default 24 minutes)
    4. Automatic recovery when connection is restored

    Usage:
        real_inverter = FroniusWR(config)
        resilient = ResilientInverterWrapper(real_inverter)
        # Use resilient instead of real_inverter
    """

    def __init__(
        self,
        inverter: InverterInterface,
        outage_tolerance_seconds: float = DEFAULT_OUTAGE_TOLERANCE_SECONDS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    ):
        """
        Initialize the resilient wrapper.

        Args:
            inverter: The actual inverter implementation to wrap
            outage_tolerance_seconds: Max time to tolerate outage (default: 24 min)
            retry_backoff_seconds: Time to wait before retrying after failure (default: 60s)
        """
        self._inverter = inverter
        self._outage_tolerance_seconds = outage_tolerance_seconds
        self._retry_backoff_seconds = retry_backoff_seconds
        # Initialization is complete after first successful set_mode_* call
        # Until then, all errors propagate immediately (fail-fast for config errors)
        self._initialization_complete = False
        self._first_failure_time: Optional[float] = None
        self._last_failure_time: Optional[float] = None
        self._cache = CachedValues()
        self._consecutive_failures = 0

        # Initialize attributes that will be forwarded from wrapped inverter
        self.min_soc = None
        self.max_soc = None
        self.mqtt_api = None
        self.capacity = None
        self.inverter_num = 0
        self.max_grid_charge_rate = 0
        self.max_pv_charge_rate = 0

        # Forward common attributes from wrapped inverter
        self._forward_attributes()

    def _forward_attributes(self):
        """Forward common attributes from the wrapped inverter."""
        # These are typically set during inverter initialization
        attrs_to_forward = [
            'min_soc', 'max_soc', 'mqtt_api', 'capacity',
            'inverter_num', 'max_grid_charge_rate', 'max_pv_charge_rate'
        ]
        for attr in attrs_to_forward:
            if hasattr(self._inverter, attr):
                setattr(self, attr, getattr(self._inverter, attr))

    def _is_in_backoff_period(self) -> bool:
        """
        Check if we're still in the backoff period after a failure.

        After a failure, we wait for retry_backoff_seconds before attempting
        another call to the inverter. This prevents hammering an unavailable
        inverter and allows time for it to recover.

        Returns:
            True if we should skip the actual call and use cached values
        """
        if self._last_failure_time is None:
            return False

        time_since_failure = time.time() - self._last_failure_time
        return time_since_failure < self._retry_backoff_seconds

    def _handle_failure(self, operation: str, error: Exception) -> bool:
        """
        Handle an inverter communication failure.

        Args:
            operation: Name of the failed operation (for logging)
            error: The exception that was raised

        Returns:
            True if the failure should be re-raised (first run or timeout)

        Raises:
            InverterOutageError if outage tolerance exceeded
        """
        self._consecutive_failures += 1
        self._last_failure_time = time.time()

        # Before initialization complete (first successful set_mode call),
        # fail fast - this is likely a configuration error
        if not self._initialization_complete:
            logger.error(
                "Inverter communication failed before initialization complete. "
                "This may be a configuration error: %s", error
            )
            return True  # Signal to caller to re-raise

        # Start tracking outage time
        if self._first_failure_time is None:
            self._first_failure_time = time.time()
            logger.warning(
                "Inverter communication failed for '%s': %s. "
                "Starting outage tolerance window.",
                operation, error
            )

        # Calculate outage duration
        outage_duration = time.time() - self._first_failure_time
        outage_minutes = outage_duration / 60

        # Check if we've exceeded tolerance
        if outage_duration > self._outage_tolerance_seconds:
            logger.error(
                "Inverter has been unreachable for %.1f minutes "
                "(tolerance: %.1f minutes). Giving up.",
                outage_minutes,
                self._outage_tolerance_seconds / 60
            )
            raise InverterOutageError(
                f"Inverter unreachable for {outage_minutes:.1f} minutes "
                f"during '{operation}'",
                outage_duration_seconds=outage_duration
            )

        logger.warning(
            "Inverter communication failed for '%s' (outage: %.1f min, "
            "tolerance: %.1f min, failures: %d)",
            operation, outage_minutes,
            self._outage_tolerance_seconds / 60,
            self._consecutive_failures
        )

        return False  # Don't re-raise, try cache/default

    def _handle_success(self, mark_initialized: bool = False) -> None:
        """
        Handle a successful inverter communication.

        Args:
            mark_initialized: If True, mark initialization as complete.
                              This should only be True for set_mode_* calls.
        """
        if self._first_failure_time is not None:
            outage_duration = time.time() - self._first_failure_time
            logger.info(
                "Inverter connection restored after %.1f minutes outage",
                outage_duration / 60
            )

        if mark_initialized and not self._initialization_complete:
            logger.info("Inverter initialization complete (first set_mode succeeded)")
            self._initialization_complete = True

        self._first_failure_time = None
        self._last_failure_time = None
        self._consecutive_failures = 0

    def _call_with_resilience(
        self,
        method: Callable,
        operation_name: str,
        cache_attr: Optional[str] = None,
        default_value: Any = None,
        method_args: tuple = (),
        mark_initialized: bool = False
    ) -> Any:
        """
        Call an inverter method with resilience handling.

        Args:
            method: The inverter method to call
            operation_name: Name for logging
            cache_attr: Attribute name in CachedValues to use for fallback
            default_value: Value to return if no cache available
            method_args: Arguments to pass to the method
            mark_initialized: If True, mark initialization complete on success
                              (should only be True for set_mode_* calls)

        Returns:
            The method result, cached value, or default value
        """
        # Check if we're in backoff period - skip actual call to avoid
        # hammering an unavailable inverter
        if self._is_in_backoff_period():
            return self._get_cached_or_default(
                operation_name, cache_attr, default_value, is_backoff=True
            )

        try:
            result = method(*method_args)
            self._handle_success(mark_initialized=mark_initialized)

            # Update cache if applicable
            if cache_attr is not None:
                setattr(self._cache, cache_attr, result)
                self._cache.last_update_time = time.time()

            return result

        except Exception as e:  # pylint: disable=broad-exception-caught
            should_reraise = self._handle_failure(operation_name, e)

            if should_reraise:
                raise

            return self._get_cached_or_default(
                operation_name, cache_attr, default_value, is_backoff=False
            )

    def _get_cached_or_default(
        self,
        operation_name: str,
        cache_attr: Optional[str],
        default_value: Any,
        is_backoff: bool
    ) -> Any:
        """
        Get cached value or default when inverter is unavailable.

        Args:
            operation_name: Name for logging
            cache_attr: Attribute name in CachedValues
            default_value: Fallback value if no cache
            is_backoff: True if skipping due to backoff period

        Returns:
            Cached value, default value, or raises if neither available
        """
        reason = "in backoff period" if is_backoff else "after failure"

        # Try to use cached value
        if cache_attr is not None:
            cached_value = getattr(self._cache, cache_attr, None)
            if cached_value is not None:
                cache_age = time.time() - self._cache.last_update_time
                time_until_retry = 0
                if self._last_failure_time:
                    time_until_retry = max(
                        0,
                        self._retry_backoff_seconds - (time.time() - self._last_failure_time)
                    )
                logger.debug(
                    "Using cached %s value: %s (%s, age: %.1f min, retry in: %.0fs)",
                    cache_attr, cached_value, reason, cache_age / 60, time_until_retry
                )
                return cached_value

        # No cache available
        if default_value is not None:
            logger.warning(
                "No cached value for %s (%s), using default: %s",
                operation_name, reason, default_value
            )
            return default_value

        # Re-raise if no fallback available
        raise RuntimeError(
            f"No cached value or default available for {operation_name} ({reason})"
        )

    # =========================================================================
    # InverterInterface Implementation - Read Operations (with caching)
    # =========================================================================

    def get_SOC(self) -> float:
        """Get state of charge with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_SOC,
            "get_SOC",
            cache_attr="soc",
            default_value=50.0  # Safe middle value if no cache
        )

    def get_stored_energy(self) -> float:
        """Get stored energy with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_stored_energy,
            "get_stored_energy",
            cache_attr="stored_energy"
        )

    def get_stored_usable_energy(self) -> float:
        """Get stored usable energy with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_stored_usable_energy,
            "get_stored_usable_energy",
            cache_attr="stored_usable_energy"
        )

    def get_capacity(self) -> float:
        """Get capacity with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_capacity,
            "get_capacity",
            cache_attr="capacity"
        )

    def get_free_capacity(self) -> float:
        """Get free capacity with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_free_capacity,
            "get_free_capacity",
            cache_attr="free_capacity"
        )

    def get_max_capacity(self) -> float:
        """Get max capacity with resilience handling."""
        return self._call_with_resilience(
            self._inverter.get_max_capacity,
            "get_max_capacity",
            cache_attr="max_capacity"
        )

    # =========================================================================
    # InverterInterface Implementation - Write Operations (no caching)
    # =========================================================================

    def set_mode_force_charge(self, chargerate: float):
        """Set force charge mode with resilience handling."""
        return self._call_with_resilience(
            self._inverter.set_mode_force_charge,
            "set_mode_force_charge",
            None, None,
            method_args=(chargerate,),
            mark_initialized=True
        )

    def set_mode_avoid_discharge(self):
        """Set avoid discharge mode with resilience handling."""
        return self._call_with_resilience(
            self._inverter.set_mode_avoid_discharge,
            "set_mode_avoid_discharge",
            mark_initialized=True
        )

    def set_mode_allow_discharge(self):
        """Set allow discharge mode with resilience handling."""
        return self._call_with_resilience(
            self._inverter.set_mode_allow_discharge,
            "set_mode_allow_discharge",
            mark_initialized=True
        )

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """Set limit battery charge mode with resilience handling."""
        return self._call_with_resilience(
            self._inverter.set_mode_limit_battery_charge,
            "set_mode_limit_battery_charge",
            None, None,
            method_args=(limit_charge_rate,),
            mark_initialized=True
        )

    # =========================================================================
    # InverterInterface Implementation - Other Methods
    # =========================================================================

    def activate_mqtt(self, api_mqtt_api: object):
        """Activate MQTT - delegate to wrapped inverter."""
        self._inverter.activate_mqtt(api_mqtt_api)
        # Update forwarded mqtt_api attribute
        if hasattr(self._inverter, 'mqtt_api'):
            self.mqtt_api = self._inverter.mqtt_api

    def refresh_api_values(self):
        """Refresh API values with resilience handling."""
        try:
            self._inverter.refresh_api_values()
            self._handle_success(mark_initialized=False)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._handle_failure("refresh_api_values", e)
            # This is non-critical, just log and continue
            logger.debug("Skipping API value refresh during outage")

    def shutdown(self):
        """Shutdown - delegate to wrapped inverter."""
        try:
            self._inverter.shutdown()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error during inverter shutdown: %s", e)

    # =========================================================================
    # Additional Methods (forward to wrapped inverter)
    # =========================================================================

    def get_designed_capacity(self) -> float:
        """Get designed capacity with resilience handling."""
        if hasattr(self._inverter, 'get_designed_capacity'):
            return self._call_with_resilience(
                self._inverter.get_designed_capacity,
                "get_designed_capacity",
                cache_attr="designed_capacity"
            )
        return self.get_capacity()

    def get_usable_capacity(self) -> float:
        """Get usable capacity with resilience handling."""
        if hasattr(self._inverter, 'get_usable_capacity'):
            return self._call_with_resilience(
                self._inverter.get_usable_capacity,
                "get_usable_capacity",
                cache_attr="usable_capacity"
            )
        # Fallback calculation
        return self.get_max_capacity()

    def get_mqtt_inverter_topic(self) -> str:
        """Get MQTT topic - delegate to wrapped inverter."""
        if hasattr(self._inverter, 'get_mqtt_inverter_topic'):
            return self._inverter.get_mqtt_inverter_topic()
        return f'inverters/{getattr(self, "inverter_num", 0)}/'

    def publish_inverter_discovery_messages(self):
        """Publish discovery messages - delegate to wrapped inverter."""
        if hasattr(self._inverter, 'publish_inverter_discovery_messages'):
            try:
                self._inverter.publish_inverter_discovery_messages()
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to publish discovery messages: %s", e
                )

    # =========================================================================
    # Status and Diagnostics
    # =========================================================================

    def get_outage_status(self) -> dict:
        """
        Get current outage status for diagnostics.

        Returns:
            dict with outage information
        """
        outage_duration = 0
        if self._first_failure_time is not None:
            outage_duration = time.time() - self._first_failure_time

        time_until_retry = 0
        if self._last_failure_time is not None:
            time_until_retry = max(
                0,
                self._retry_backoff_seconds - (time.time() - self._last_failure_time)
            )

        return {
            "is_connected": self._first_failure_time is None,
            "initialization_complete": self._initialization_complete,
            "outage_duration_seconds": outage_duration,
            "outage_duration_minutes": outage_duration / 60,
            "outage_tolerance_seconds": self._outage_tolerance_seconds,
            "consecutive_failures": self._consecutive_failures,
            "cache_valid": self._cache.is_valid(),
            "cache_age_seconds": time.time() - self._cache.last_update_time,
            "in_backoff_period": self._is_in_backoff_period(),
            "retry_backoff_seconds": self._retry_backoff_seconds,
            "time_until_retry_seconds": time_until_retry
        }

    @property
    def wrapped_inverter(self) -> InverterInterface:
        """Access to the wrapped inverter for advanced use cases."""
        return self._inverter

    def __getattr__(self, name):
        """Forward unknown attributes to the wrapped inverter."""
        # This is called when an attribute is not found on the wrapper
        # Forward to the wrapped inverter
        return getattr(self._inverter, name)
