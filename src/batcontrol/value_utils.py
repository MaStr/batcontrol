"""Utility functions for parsing and validating config and MQTT payload values.

These helpers are independent of the Batcontrol core and raise ValueError
for values that cannot be interpreted.
"""
import logging
import math
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Canonical configuration key names and the deprecated spellings that are still
# accepted for them. batcontrol grew three spellings for a credential
# ('apikey', 'apitoken', 'api') and two for an endpoint ('url', 'base_url'),
# which made users put the right value into the wrong key (issue #451).
CONFIG_KEY_ALIASES = {
    'url': ('base_url',),
    'api_token': ('apikey', 'apitoken', 'api'),
}

# Deprecation warnings are emitted once per (context, deprecated key) so a
# provider that reads the same key on every cycle does not flood the log.
_warned_aliases = set()


def reset_alias_warnings() -> None:
    """Forget which deprecation warnings were already emitted (for tests)."""
    _warned_aliases.clear()


# Header names whose value carries a credential and must never be logged
# in full. Lower case, compared case-insensitively.
SENSITIVE_HEADERS = frozenset({
    'authorization',
    'proxy-authorization',
    'cookie',
    'set-cookie',
    'api-key',
    'x-api-key',
    'x-auth-token',
    'x-access-token',
})


def mask_secret(value, keep: int = 3) -> str:
    """Shorten a credential so it can be logged.

    Keeps the first and last ``keep`` characters, which is enough to tell
    two credentials apart or to recognise the configured one, without
    putting the secret itself into the log. Values short enough for the
    remainder to give the secret away are masked completely.

    Args:
        value: The credential, of any type
        keep: Number of characters to keep at each end

    Returns:
        The shortened representation, e.g. 'eyJ...9fQ'
    """
    if value is None:
        return '<unset>'
    text = str(value)
    if not text:
        return '<empty>'
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}...{text[-keep:]}'


def mask_sensitive_headers(headers) -> dict:
    """Copy an HTTP header mapping with every credential value shortened

    Logging a request's headers is useful for debugging, but an
    Authorization header holds a credential - for digest auth a hash of the
    password, which is replayable while the nonce is valid. Only the header
    values in SENSITIVE_HEADERS are shortened, so the remaining headers stay
    readable.
    """
    if not headers:
        return {}
    return {
        key: mask_secret(value) if str(key).lower() in SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


def get_config_value(
        config: dict,
        key: str,
        default=None,
        context: str = '') -> Any:
    """Read a config value, falling back to deprecated spellings of its key.

    An empty value (missing key, None or empty string) counts as not set. The
    empty string matters for the HomeAssistant add-on, where a field the user
    cleared in the UI arrives as '' instead of being absent.

    Args:
        config: Configuration mapping to read from
        key: Canonical key name (see CONFIG_KEY_ALIASES)
        default: Value to return when neither key nor alias is set
        context: Config section for the warning, e.g. 'pvinstallations[Haus]'

    Returns:
        The configured value, or default if nothing is set
    """
    if not isinstance(config, dict):
        return default

    value = config.get(key)
    if value or value == 0:
        return value

    for alias in CONFIG_KEY_ALIASES.get(key, ()):
        value = config.get(alias)
        if value or value == 0:
            warning_key = (context, alias, key)
            if warning_key not in _warned_aliases:
                _warned_aliases.add(warning_key)
                logger.warning(
                    '[Config] "%s"%s is deprecated, please rename it to "%s"',
                    alias,
                    f' in {context}' if context else '',
                    key
                )
            return value

    return default


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
