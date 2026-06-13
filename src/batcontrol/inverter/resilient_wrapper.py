"""Resilient Inverter Wrapper - thin fault-tolerance layer.

Concept (deliberately simple - no caching, no backoff timers):

- Before the first successful set_mode_* call every error propagates
  unchanged, so configuration mistakes fail fast at startup.
- After initialization an inverter failure raises InverterCommunicationError.
  The caller (core.run) aborts the current control cycle and retries on the
  next scheduled run - the loop interval is the natural backoff, and no
  decision is ever made on stale data.
- If the inverter stays unreachable past the tolerance window, the failure is
  escalated to InverterOutageError, which terminates batcontrol.
"""

import time
import logging

from .inverter_interface import InverterInterface
from .exceptions import InverterCommunicationError, InverterOutageError

logger = logging.getLogger(__name__)

# Default outage tolerance: 24 minutes (to handle firmware upgrades)
DEFAULT_OUTAGE_TOLERANCE_SECONDS = 24 * 60

# Attributes forwarded from the wrapped inverter
_FORWARDED_ATTRS = (
    'min_soc', 'max_soc', 'mqtt_api', 'capacity',
    'inverter_num', 'max_grid_charge_rate', 'max_pv_charge_rate',
)


class ResilientInverterWrapper(InverterInterface):
    """Wraps any inverter and turns communication failures into clear signals."""

    def __init__(
        self,
        inverter: InverterInterface,
        outage_tolerance_seconds: float = DEFAULT_OUTAGE_TOLERANCE_SECONDS,
    ):
        self._inverter = inverter
        self._outage_tolerance = outage_tolerance_seconds
        self._initialized = False           # True after first successful set_mode_*
        self._outage_start = None           # timestamp of the first failure

        # Forward common attributes from the wrapped inverter
        self.min_soc = None
        self.max_soc = None
        self.mqtt_api = None
        self.capacity = None
        self.inverter_num = 0
        self.max_grid_charge_rate = 0
        self.max_pv_charge_rate = 0
        for attr in _FORWARDED_ATTRS:
            if hasattr(self._inverter, attr):
                setattr(self, attr, getattr(self._inverter, attr))

    # -------------------------------------------------------------------------
    # Core guard
    # -------------------------------------------------------------------------

    def _guard(self, name, method, *args, is_command=False):
        """Call an inverter method, classifying any failure."""
        try:
            result = method(*args)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._on_failure(name, e)  # always raises
        else:
            if self._outage_start is not None:
                logger.info(
                    "Inverter connection restored after %.1f min outage",
                    (time.time() - self._outage_start) / 60,
                )
                self._outage_start = None
            if is_command and not self._initialized:
                logger.info("Inverter initialization complete (first set_mode succeeded)")
                self._initialized = True
            return result

    def _on_failure(self, name, error):
        """Decide how to escalate a failure. Never returns - always raises."""
        # Before initialization a failure is most likely a configuration error.
        if not self._initialized:
            logger.error(
                "Inverter failure before initialization (config error?): %s", error
            )
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
            name, outage / 60, self._outage_tolerance / 60,
        )
        raise InverterCommunicationError(name) from error

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def get_SOC(self) -> float:
        return self._guard("get_SOC", self._inverter.get_SOC)

    def get_stored_energy(self) -> float:
        return self._guard("get_stored_energy", self._inverter.get_stored_energy)

    def get_stored_usable_energy(self) -> float:
        return self._guard("get_stored_usable_energy", self._inverter.get_stored_usable_energy)

    def get_capacity(self) -> float:
        return self._guard("get_capacity", self._inverter.get_capacity)

    def get_free_capacity(self) -> float:
        return self._guard("get_free_capacity", self._inverter.get_free_capacity)

    def get_max_capacity(self) -> float:
        return self._guard("get_max_capacity", self._inverter.get_max_capacity)

    def get_designed_capacity(self) -> float:
        if hasattr(self._inverter, 'get_designed_capacity'):
            return self._guard("get_designed_capacity", self._inverter.get_designed_capacity)
        return self.get_capacity()

    def get_usable_capacity(self) -> float:
        if hasattr(self._inverter, 'get_usable_capacity'):
            return self._guard("get_usable_capacity", self._inverter.get_usable_capacity)
        return self.get_max_capacity()

    # -------------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------------

    def set_mode_force_charge(self, chargerate: float):
        return self._guard(
            "set_mode_force_charge", self._inverter.set_mode_force_charge,
            chargerate, is_command=True,
        )

    def set_mode_avoid_discharge(self):
        return self._guard(
            "set_mode_avoid_discharge", self._inverter.set_mode_avoid_discharge,
            is_command=True,
        )

    def set_mode_allow_discharge(self):
        return self._guard(
            "set_mode_allow_discharge", self._inverter.set_mode_allow_discharge,
            is_command=True,
        )

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        return self._guard(
            "set_mode_limit_battery_charge", self._inverter.set_mode_limit_battery_charge,
            limit_charge_rate, is_command=True,
        )

    # -------------------------------------------------------------------------
    # Other methods
    # -------------------------------------------------------------------------

    def activate_mqtt(self, api_mqtt_api: object):
        self._inverter.activate_mqtt(api_mqtt_api)
        if hasattr(self._inverter, 'mqtt_api'):
            self.mqtt_api = self._inverter.mqtt_api

    def refresh_api_values(self):
        """Best-effort refresh - never aborts a cycle on its own."""
        try:
            self._inverter.refresh_api_values()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Skipping API value refresh (inverter unavailable): %s", e)

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

    @property
    def wrapped_inverter(self) -> InverterInterface:
        """Access to the wrapped inverter for advanced use cases."""
        return self._inverter

    def __getattr__(self, name):
        """Forward unknown attributes to the wrapped inverter."""
        return getattr(self._inverter, name)
