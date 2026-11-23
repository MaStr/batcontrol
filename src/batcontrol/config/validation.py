"""Startup validation of configuration sections with Pydantic models.

The environment variable BATCONTROL_CONFIG_VALIDATION selects the mode:

- warn (default): every finding is logged and startup continues on the
  existing code path, which still raises exactly where it raised before.
  A configuration that started before keeps starting.
- strict: values the code cannot use stop batcontrol right at startup
  with a message naming the key. Plausibility findings stay warnings.

See docs/development/pydantic-config-migration.md.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ValidationError

from .battery_control import (
    EXPERT_SECTION,
    SECTION,
    BatteryControlExpertSection,
    BatteryControlSection,
    check_max_soc,
    unknown_keys,
)

logger = logging.getLogger(__name__)

VALIDATION_ENV = 'BATCONTROL_CONFIG_VALIDATION'
MODE_WARN = 'warn'
MODE_STRICT = 'strict'
VALIDATION_MODES = (MODE_WARN, MODE_STRICT)


@dataclass
class ValidationReport:
    """Findings of one validation run, as log ready messages."""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def validation_mode() -> str:
    """The configured validation mode; unknown values fall back to warn."""
    raw = os.environ.get(VALIDATION_ENV)
    if raw is None or not raw.strip():
        return MODE_WARN
    mode = raw.strip().lower()
    if mode not in VALIDATION_MODES:
        logger.warning(
            '[Config] %s=%r is not one of %s, using %r',
            VALIDATION_ENV, raw, VALIDATION_MODES, MODE_WARN)
        return MODE_WARN
    return mode


def _format_error(section: str, error: dict) -> str:
    """One readable line for a pydantic error entry."""
    location = '.'.join(str(part) for part in error.get('loc', ()))
    key = f'{section}.{location}' if location else section
    message = error.get('msg', '')
    # A ValueError raised by one of the value_utils parsers already names
    # the key, pydantic only prefixes it.
    message = message.removeprefix('Value error, ')
    if key in message:
        return message
    return f'{key}: {message}, got {error.get("input")!r}'


def _validate_section(config: dict, section: str, model: type[BaseModel],
                      report: ValidationReport) -> Optional[BaseModel]:
    """Validate one optional mapping section, collect its findings."""
    raw = config.get(section)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        report.errors.append(
            f'{section} must be a mapping of key: value settings, '
            f'got {type(raw).__name__}')
        return None
    for key in unknown_keys(raw, model):
        report.warnings.append(
            f'Unknown key {section}.{key} is ignored (typo?)')
    context = {'warnings': []}
    # A key that is not a string can only be unknown (reported above);
    # pydantic would turn it into an error the code never raised.
    known = {key: value for key, value in raw.items() if isinstance(key, str)}
    try:
        validated = model.model_validate(known, context=context)
    except ValidationError as exc:
        validated = None
        report.errors.extend(
            _format_error(section, error) for error in exc.errors())
    # Field level warnings are collected even when another field failed
    report.warnings.extend(context['warnings'])
    return validated


def check_battery_control(config: dict,
                          inverter_max_soc=None) -> ValidationReport:
    """Validate battery_control and battery_control_expert.

    Never raises and never changes config. inverter_max_soc is the
    inverter's max_soc in percent, if known, for the cross-check against
    always_allow_discharge_limit.
    """
    report = ValidationReport()
    if config.get(SECTION) is None:
        report.errors.append(f'Section {SECTION} is missing')
    else:
        _validate_section(config, SECTION, BatteryControlSection, report)
    _validate_section(
        config, EXPERT_SECTION, BatteryControlExpertSection, report)

    section = config.get(SECTION)
    if isinstance(section, dict):
        warning = check_max_soc(
            section.get('always_allow_discharge_limit', 0.9),
            inverter_max_soc)
        if warning:
            report.warnings.append(warning)
    return report


def validate_battery_control(config: dict, inverter_max_soc=None,
                             mode: Optional[str] = None) -> ValidationReport:
    """Validate the battery_control sections, log the findings.

    Raises ValueError in strict mode if a value cannot be used. In warn
    mode it never raises: the code after it keeps its own checks.
    """
    if mode is None:
        mode = validation_mode()
    try:
        report = check_battery_control(config, inverter_max_soc)
    except Exception:  # pylint: disable=broad-exception-caught
        if mode == MODE_STRICT:
            raise
        # The validation is an addition; a bug in it must never stop a
        # configuration that starts without it.
        logger.exception(
            '[Config] Validation of %s failed unexpectedly, continuing '
            'without it', SECTION)
        return ValidationReport()
    for warning in report.warnings:
        logger.warning('[Config] %s', warning)
    if not report.errors:
        return report
    if mode == MODE_STRICT:
        raise ValueError(
            'Invalid configuration: ' + '; '.join(report.errors))
    for error in report.errors:
        logger.error(
            '[Config] %s. batcontrol continues because %s=%s; this value '
            'is expected to fail at startup or in the control cycle.',
            error, VALIDATION_ENV, MODE_WARN)
    return report
