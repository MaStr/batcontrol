"""Typed configuration models (Pydantic), introduced section by section.

Stage 1 covers battery_control and battery_control_expert only, in warn
mode. See docs/development/pydantic-config-migration.md.
"""
from .general import (
    ALLOWED_TIME_RESOLUTIONS,
    parse_time_resolution,
)
from .validation import (
    ConfigValidationError,
    MODE_STRICT,
    MODE_WARN,
    VALIDATION_ENV,
    ValidationReport,
    check_battery_control,
    validate_battery_control,
    validation_mode,
    warn_unreachable_always_allow,
)

__all__ = [
    'ALLOWED_TIME_RESOLUTIONS',
    'ConfigValidationError',
    'MODE_STRICT',
    'MODE_WARN',
    'VALIDATION_ENV',
    'ValidationReport',
    'check_battery_control',
    'parse_time_resolution',
    'validate_battery_control',
    'validation_mode',
    'warn_unreachable_always_allow',
]
