"""Utility functions for parsing and validating config and MQTT payload values.

These helpers are independent of the Batcontrol core and raise ValueError
for values that cannot be interpreted.
"""
import math
from typing import Optional


def parse_optional_ratio(value, config_key: str) -> Optional[float]:
    """Parse an optional 0..1 ratio config value."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"{config_key} must be numeric between 0 and 1 or None, "
            f"got {type(value).__name__}"
        )
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{config_key} must be numeric between 0 and 1 or None, "
            f"got {value!r}"
        ) from exc
    if not 0 <= ratio <= 1:
        raise ValueError(
            f"{config_key} must be between 0 and 1 or None, got {value!r}"
        )
    return ratio


def parse_positive_number(value, config_key: str) -> float:
    """Parse a positive numeric config value. ``value`` must not be None;
    callers only invoke this once they know the key was actually set,
    so an explicit null is treated as invalid rather than silently
    falling back to a default."""
    if isinstance(value, bool):
        raise ValueError(
            f"{config_key} must be a positive number, "
            f"got {type(value).__name__}"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{config_key} must be a positive number, got {value!r}"
        ) from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(
            f"{config_key} must be a positive number, got {value!r}"
        )
    return number


def parse_bool_flag(value) -> bool:
    """Parse an MQTT payload for a boolean flag: 1/0 or true/false (case-insensitive)."""
    normalized = str(value).strip().lower()
    if normalized in ('1', 'true'):
        return True
    if normalized in ('0', 'false'):
        return False
    raise ValueError(f"Invalid boolean flag payload: {value!r}")
