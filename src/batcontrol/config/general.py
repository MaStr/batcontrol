"""Checks for top-level keys of the configuration.

Not a Pydantic model yet (the general section is stage 2 of issue #144,
see docs/development/pydantic-config-migration.md); these checks run
first in Batcontrol.__init__, before any provider or inverter exists.
"""
import math

from .battery_control import parse_numeric_string

TIME_RESOLUTION_KEY = 'time_resolution_minutes'
ALLOWED_TIME_RESOLUTIONS = (15, 60)


def parse_time_resolution(value) -> int:
    """time_resolution_minutes as an int, 15 or 60.

    The Home Assistant add-on schema list(15|60) delivers the value as a
    string, so a numeric string ("15", " 15 ", "15.0") is read as its
    number, with the same parsing as the battery_control coercion. A
    whole float (15.0) is accepted as well. Anything else raises a
    ValueError that names the key and the allowed values.
    """
    number = value
    if isinstance(value, str):
        parsed = parse_numeric_string(value)
        if parsed is not None:
            number = parsed
    if isinstance(number, float) and math.isfinite(number) \
            and number.is_integer():
        number = int(number)
    if isinstance(number, int) and not isinstance(number, bool) \
            and number in ALLOWED_TIME_RESOLUTIONS:
        return number
    raise ValueError(
        f"{TIME_RESOLUTION_KEY} must be either 15 (quarter-hourly) "
        f"or 60 (hourly), got {value!r}. "
        "Please update your configuration file."
    )
