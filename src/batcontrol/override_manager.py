"""Override Manager for Batcontrol

Manages time-bounded overrides for battery control mode.
Replaces the single-shot api_overwrite flag with duration-based overrides
that persist across multiple evaluation cycles.

Used by both MQTT API and MCP server to provide meaningful manual control.
"""
import time
import threading
import logging
from dataclasses import dataclass, field, replace
from typing import Optional

logger = logging.getLogger(__name__)

# Default duration for MQTT overrides (backward compatible)
DEFAULT_OVERRIDE_DURATION_MINUTES = 30

# Valid inverter modes accepted by the override system
VALID_OVERRIDE_MODES = {-1, 0, 8, 10}


@dataclass
class OverrideState:
    """Represents an active override."""
    mode: int
    charge_rate: Optional[int]
    duration_minutes: float
    reason: str
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = field(default=None)

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.created_at + self.duration_minutes * 60

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining before override expires."""
        return max(0.0, self.expires_at - time.time())

    @property
    def remaining_minutes(self) -> float:
        """Minutes remaining before override expires."""
        return self.remaining_seconds / 60.0

    @property
    def is_expired(self) -> bool:
        """True if the override has expired."""
        return time.time() >= self.expires_at

    def to_dict(self) -> dict:
        """Serialize override state to a dictionary."""
        now = time.time()
        remaining_secs = max(0.0, self.expires_at - now)
        return {
            'mode': self.mode,
            'charge_rate': self.charge_rate,
            'duration_minutes': self.duration_minutes,
            'reason': self.reason,
            'created_at': self.created_at,
            'expires_at': self.expires_at,
            'remaining_minutes': round(remaining_secs / 60.0, 1),
            'is_active': now < self.expires_at,
        }


class OverrideManager:
    """Manages time-bounded overrides for batcontrol operation.

    Thread-safe: all public methods acquire a lock before modifying state.
    """

    def __init__(self, default_duration_minutes: float = DEFAULT_OVERRIDE_DURATION_MINUTES):
        self._lock = threading.Lock()
        self._override: Optional[OverrideState] = None
        self.default_duration_minutes = default_duration_minutes

    def set_override(self, mode: int, duration_minutes: Optional[float] = None,
                     charge_rate: Optional[int] = None,
                     reason: str = "") -> OverrideState:
        """Set a time-bounded override.

        Args:
            mode: Inverter mode (-1, 0, 8, 10)
            duration_minutes: How long the override should last.
                              None uses default_duration_minutes.
            charge_rate: Optional charge rate in W (relevant for mode -1)
            reason: Human-readable reason for the override

        Returns:
            The created OverrideState
        """
        if mode not in VALID_OVERRIDE_MODES:
            raise ValueError(
                f"Invalid mode {mode}. Valid modes: {sorted(VALID_OVERRIDE_MODES)}")

        if duration_minutes is None:
            duration_minutes = self.default_duration_minutes

        if duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")

        with self._lock:
            self._override = OverrideState(
                mode=mode,
                charge_rate=charge_rate,
                duration_minutes=duration_minutes,
                reason=reason,
            )
            logger.info(
                'Override set: mode=%s, duration=%.1f min, charge_rate=%s, reason="%s"',
                mode, duration_minutes, charge_rate, reason
            )
            return self._override

    def clear_override(self) -> None:
        """Clear the active override, resuming autonomous logic."""
        with self._lock:
            if self._override is not None:
                logger.info('Override cleared (was mode=%s, reason="%s")',
                            self._override.mode, self._override.reason)
            self._override = None

    def get_override(self) -> Optional[OverrideState]:
        """Get a snapshot of the active override, or None if no override is active.

        Returns a defensive copy so callers cannot mutate the internal state.
        Automatically clears expired overrides.
        """
        with self._lock:
            if self._override is None:
                return None
            if self._override.is_expired:
                logger.info(
                    'Override expired: mode=%s, reason="%s"',
                    self._override.mode, self._override.reason
                )
                self._override = None
                return None
            return replace(self._override)

    def is_active(self) -> bool:
        """Check if an override is currently active (not expired)."""
        return self.get_override() is not None

    @property
    def remaining_minutes(self) -> float:
        """Minutes remaining on the current override, or 0 if none."""
        override = self.get_override()
        if override is None:
            return 0.0
        return override.remaining_minutes
