"""Resilient Inverter Wrapper - adds fault tolerance to any inverter backend.

During temporary outages (firmware upgrades, network blips):
- Read operations return cached values for up to 24 minutes (configurable).
- Write operations (set_mode_*) are discarded and retried next cycle.
- After the tolerance window expires, InverterOutageError is raised.

Before the first successful set_mode_* call, all errors propagate
immediately so configuration mistakes are caught at startup.
"""

import time
import logging
from typing import Optional

from .inverter_interface import InverterInterface
from .exceptions import InverterOutageError

logger = logging.getLogger(__name__)

DEFAULT_OUTAGE_TOLERANCE_SECONDS = 24 * 60
DEFAULT_RETRY_BACKOFF_SECONDS = 60


class ResilientInverterWrapper(InverterInterface):
    """Wraps any inverter and provides graceful degradation during outages."""

    def __init__(
        self,
        inverter: InverterInterface,
        outage_tolerance_seconds: float = DEFAULT_OUTAGE_TOLERANCE_SECONDS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ):
        self._inverter = inverter
        self._outage_tolerance = outage_tolerance_seconds
        self._retry_backoff = retry_backoff_seconds

        self._initialized = False        # True after first successful set_mode_*
        self._outage_start: Optional[float] = None  # timestamp of first failure
        self._backoff_until: float = 0   # don't call inverter until this time
        self._cache: dict = {}
        self._cache_time: float = time.time()

        # Forward common attributes from the wrapped inverter
        self.min_soc = None
        self.max_soc = None
        self.mqtt_api = None
        self.capacity = None
        self.inverter_num = 0
        self.max_grid_charge_rate = 0
        self.max_pv_charge_rate = 0
        for attr in ('min_soc', 'max_soc', 'mqtt_api', 'capacity',
                     'inverter_num', 'max_grid_charge_rate', 'max_pv_charge_rate'):
            if hasattr(self._inverter, attr):
                setattr(self, attr, getattr(self._inverter, attr))

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _in_backoff(self) -> bool:
        return time.time() < self._backoff_until

    def _on_success(self, mark_initialized: bool = False) -> None:
        if self._outage_start is not None:
            logger.info(
                "Inverter connection restored after %.1f min outage",
                (time.time() - self._outage_start) / 60,
            )
        self._outage_start = None
        self._backoff_until = 0
        if mark_initialized and not self._initialized:
            logger.info("Inverter initialization complete (first set_mode succeeded)")
            self._initialized = True

    def _on_failure(self, op: str, error: Exception) -> bool:
        """Record failure state. Returns True if the caller should re-raise.

        Raises InverterOutageError directly when the tolerance window expires.
        """
        self._backoff_until = time.time() + self._retry_backoff
        if not self._initialized:
            logger.error(
                "Inverter failure before initialization (config error?): %s", error
            )
            return True  # fail-fast

        if self._outage_start is None:
            self._outage_start = time.time()

        outage = time.time() - self._outage_start
        if outage > self._outage_tolerance:
            raise InverterOutageError(
                f"Inverter unreachable for {outage/60:.1f} min during '{op}'",
                outage_duration_seconds=outage,
            )

        logger.warning(
            "Inverter communication failed for '%s' (outage: %.1f min, tolerance: %.1f min)",
            op, outage / 60, self._outage_tolerance / 60,
        )
        return False

    def _read(self, key: str, method, default=None):
        """Call a read method, returning cached value during outages."""
        if self._in_backoff():
            cached = self._cache.get(key)
            if cached is not None:
                age = (time.time() - self._cache_time) / 60
                retry_in = self._backoff_until - time.time()
                logger.debug(
                    "Using cached %s value: %s (in backoff period, age: %.1f min, retry in: %.0fs)",
                    key, cached, age, retry_in,
                )
                return cached
            if default is not None:
                return default
            raise RuntimeError(f"No cached value for '{key}' during backoff")

        try:
            result = method()
            self._on_success()
            self._cache[key] = result
            self._cache_time = time.time()
            return result
        except Exception as e:  # pylint: disable=broad-exception-caught
            if self._on_failure(key, e):
                raise
            cached = self._cache.get(key)
            if cached is not None:
                logger.debug("Using cached %s value: %s (after failure)", key, cached)
                return cached
            if default is not None:
                return default
            raise RuntimeError(f"No cached value for '{key}' after failure") from e

    def _command(self, name: str, method, *args, mark_initialized: bool = False):
        """Call a write method. Discards during backoff; degrades to None on failure."""
        if self._in_backoff():
            logger.debug("Skipping command '%s' (in backoff, inverter unavailable)", name)
            return None
        try:
            result = method(*args)
            self._on_success(mark_initialized=mark_initialized)
            return result
        except Exception as e:  # pylint: disable=broad-exception-caught
            if self._on_failure(name, e):
                raise
            logger.warning(
                "Inverter command '%s' could not be applied, retrying next cycle", name
            )
            return None

    # -------------------------------------------------------------------------
    # Read operations (InverterInterface)
    # -------------------------------------------------------------------------

    def get_SOC(self) -> float:
        return self._read("soc", self._inverter.get_SOC, default=50.0)

    def get_stored_energy(self) -> float:
        return self._read("stored_energy", self._inverter.get_stored_energy)

    def get_stored_usable_energy(self) -> float:
        return self._read("stored_usable_energy", self._inverter.get_stored_usable_energy)

    def get_capacity(self) -> float:
        return self._read("capacity", self._inverter.get_capacity)

    def get_free_capacity(self) -> float:
        return self._read("free_capacity", self._inverter.get_free_capacity)

    def get_max_capacity(self) -> float:
        return self._read("max_capacity", self._inverter.get_max_capacity)

    def get_designed_capacity(self) -> float:
        if hasattr(self._inverter, 'get_designed_capacity'):
            return self._read("designed_capacity", self._inverter.get_designed_capacity)
        return self.get_capacity()

    def get_usable_capacity(self) -> float:
        if hasattr(self._inverter, 'get_usable_capacity'):
            return self._read("usable_capacity", self._inverter.get_usable_capacity)
        return self.get_max_capacity()

    # -------------------------------------------------------------------------
    # Write operations (InverterInterface)
    # -------------------------------------------------------------------------

    def set_mode_force_charge(self, chargerate: float):
        return self._command(
            "set_mode_force_charge", self._inverter.set_mode_force_charge,
            chargerate, mark_initialized=True,
        )

    def set_mode_avoid_discharge(self):
        return self._command(
            "set_mode_avoid_discharge", self._inverter.set_mode_avoid_discharge,
            mark_initialized=True,
        )

    def set_mode_allow_discharge(self):
        return self._command(
            "set_mode_allow_discharge", self._inverter.set_mode_allow_discharge,
            mark_initialized=True,
        )

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        return self._command(
            "set_mode_limit_battery_charge", self._inverter.set_mode_limit_battery_charge,
            limit_charge_rate, mark_initialized=True,
        )

    # -------------------------------------------------------------------------
    # Other methods
    # -------------------------------------------------------------------------

    def activate_mqtt(self, api_mqtt_api: object):
        self._inverter.activate_mqtt(api_mqtt_api)
        if hasattr(self._inverter, 'mqtt_api'):
            self.mqtt_api = self._inverter.mqtt_api

    def refresh_api_values(self):
        if self._in_backoff():
            logger.debug("Skipping API value refresh (in backoff)")
            return
        try:
            self._inverter.refresh_api_values()
            self._on_success()
        except Exception as e:  # pylint: disable=broad-exception-caught
            if not self._initialized:
                return  # non-fatal before init
            self._on_failure("refresh_api_values", e)  # may raise InverterOutageError
            logger.debug("Skipping API value refresh during outage")

    def shutdown(self):
        try:
            self._inverter.shutdown()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error during inverter shutdown: %s", e)

    def get_mqtt_inverter_topic(self) -> str:
        if hasattr(self._inverter, 'get_mqtt_inverter_topic'):
            return self._inverter.get_mqtt_inverter_topic()
        return f'inverters/{getattr(self, "inverter_num", 0)}/'

    def publish_inverter_discovery_messages(self):
        if hasattr(self._inverter, 'publish_inverter_discovery_messages'):
            try:
                self._inverter.publish_inverter_discovery_messages()
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to publish discovery messages: %s", e)

    # -------------------------------------------------------------------------
    # Diagnostics
    # -------------------------------------------------------------------------

    def get_outage_status(self) -> dict:
        now = time.time()
        outage_duration = (now - self._outage_start) if self._outage_start else 0
        return {
            "is_connected": self._outage_start is None,
            "initialization_complete": self._initialized,
            "outage_duration_seconds": outage_duration,
            "outage_duration_minutes": outage_duration / 60,
            "outage_tolerance_seconds": self._outage_tolerance,
            "cache_age_seconds": now - self._cache_time,
            "in_backoff_period": self._in_backoff(),
            "retry_backoff_seconds": self._retry_backoff,
            "time_until_retry_seconds": max(0, self._backoff_until - now),
        }

    @property
    def wrapped_inverter(self) -> InverterInterface:
        return self._inverter

    def __getattr__(self, name):
        return getattr(self._inverter, name)
