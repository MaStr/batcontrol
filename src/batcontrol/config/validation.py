"""Startup validation of configuration sections with Pydantic models.

The environment variable BATCONTROL_CONFIG_VALIDATION selects the mode:

- warn (default): every finding is logged and startup continues on the
  existing code path, which still raises exactly where it raised before.
  A configuration that started before keeps starting.
- strict: values the code cannot use stop batcontrol right at startup
  with a message naming the key. Plausibility findings stay warnings.

In both modes, numeric strings are replaced by their numbers in the
config dict (the Home Assistant add-on delivers some numbers as strings),
and always_allow_discharge_limit 0 stops startup, because it switches the
whole control off.

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
    check_always_allow_disabled,
    check_max_soc,
    coerce_numeric_strings,
    unknown_keys,
)

logger = logging.getLogger(__name__)

VALIDATION_ENV = 'BATCONTROL_CONFIG_VALIDATION'
MODE_WARN = 'warn'
MODE_STRICT = 'strict'
VALIDATION_MODES = (MODE_WARN, MODE_STRICT)


class ConfigValidationError(ValueError):
    """The configuration stops startup; the message names the keys."""


@dataclass
class ValidationReport:
    """Findings of one validation run, as log ready messages.

    fatal stops startup in every mode, errors only in strict mode.
    """
    fatal: list = field(default_factory=list)
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
        fatal = check_always_allow_disabled(
            section.get('always_allow_discharge_limit', 0.9))
        if fatal:
            report.fatal.append(fatal)
        warning = check_max_soc(
            section.get('always_allow_discharge_limit', 0.9),
            inverter_max_soc)
        if warning:
            report.warnings.append(warning)
    return report


def validate_battery_control(config: dict, inverter_max_soc=None,
                             mode: Optional[str] = None) -> ValidationReport:
    """Validate the battery_control sections, log the findings.

    First replaces numeric strings in config by their numbers (in every
    mode), so the core reads numbers. Raises ValueError if a value
    switches the control off (in every mode), and in strict mode also if
    a value cannot be used; every such message is logged as critical
    first. Otherwise warn mode does not raise: the code after it keeps its
    own checks. inverter_max_soc is only for callers that have the
    inverter already; Batcontrol runs this before it creates the inverter
    and calls warn_unreachable_always_allow() afterwards.
    """
    if mode is None:
        mode = validation_mode()
    try:
        for key, raw, number in coerce_numeric_strings(config):
            logger.debug('[Config] %s: string %r is used as %r',
                         key, raw, number)
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
    stopping = list(report.fatal)
    if mode == MODE_STRICT:
        stopping.extend(report.errors)
    else:
        for error in report.errors:
            if report.fatal:
                # Startup stops anyway, "not stopping here" would mislead
                logger.error('[Config] %s', error)
            else:
                logger.error(
                    '[Config] %s (%s=%s: not stopping here; the existing '
                    'checks may still stop startup, or the value fails in '
                    'the control cycle)', error, VALIDATION_ENV, MODE_WARN)
    if stopping:
        for message in stopping:
            logger.critical('[Config] %s', message)
        raise ConfigValidationError(
            'Invalid configuration: ' + '; '.join(stopping))
    return report


def warn_unreachable_always_allow(config: dict, inverter_max_soc) -> None:
    """Log the cross-check of the inverter max_soc against
    always_allow_discharge_limit (a warning in every mode).

    Separate from validate_battery_control(), because it needs the
    inverter, and the inverter must not be created (it writes to the
    device) for a configuration that stops startup. Never raises.
    """
    try:
        section = config.get(SECTION)
        if not isinstance(section, dict):
            return
        warning = check_max_soc(
            section.get('always_allow_discharge_limit', 0.9),
            inverter_max_soc)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('[Config] max_soc cross-check failed, skipped')
        return
    if warning:
        logger.warning('[Config] %s', warning)
