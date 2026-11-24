"""Pydantic models for the battery_control and battery_control_expert sections.

Stage 1 of the configuration migration (issue #144, see
docs/development/pydantic-config-migration.md). The models only validate:
the core keeps reading the raw config dict. The one change made to that
dict is coerce_numeric_strings(): a number given as a string (the Home
Assistant add-on delivers some values that way) is replaced by the
number, so the core computes with it instead of failing on it.

Four kinds of findings:

- Fatal: always_allow_discharge_limit 0 or below switches the control
  off completely; it stops startup in every validation mode.
- Errors: values the code cannot use. Either it already rejects them at
  startup (the expert values, min_grid_charge_soc, ...), then the model
  uses the very same parser, or it reads them unparsed and fails later in
  the control cycle (a non-numeric string or an empty value as
  min_price_difference).
- Warnings: values the code accepts but that are almost certainly wrong.
  The limits are the ones the MQTT/API setters apply to the same values.
  They never stop batcontrol.
- Unknown keys: silently ignored by the code, most likely a typo.

Defaults mirror the .get() defaults in core.py; they are only used for the
cross-checks below, never handed back to the code.
"""
import math
from typing import Annotated, Any, Callable, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationInfo,
    field_validator,
)

from ..logic.grid_charge_target import GridChargeTargetConfig
from ..value_utils import (
    parse_bool_config,
    parse_integer,
    parse_non_negative_number,
    parse_optional_ratio,
    parse_positive_number,
    parse_time_of_day,
)

SECTION = 'battery_control'
EXPERT_SECTION = 'battery_control_expert'

KNOWN_LOGIC_TYPES = ('default', 'next')

# Upper limit of the production offset, as applied by api_set_production_offset
MAX_PRODUCTION_OFFSET = 2.0

# Numeric keys whose value is replaced by the number if it is given as a
# numeric string, see coerce_numeric_strings(). Booleans and times of day
# are not numbers and are left alone.
NUMERIC_KEYS = {
    SECTION: (
        'min_price_difference',
        'min_price_difference_rel',
        'always_allow_discharge_limit',
        'max_charging_from_grid_limit',
        'min_recharge_amount',
        'min_grid_charge_soc',
        'charge_rate_multiplier',
    ),
    EXPERT_SECTION: (
        'charge_rate_multiplier',
        'soften_price_difference_on_charging_factor',
        'round_price_digits',
        'production_offset_percent',
        'max_forecast_hours',
    ),
}


def parse_numeric_string(value):
    """The number a numeric string stands for, None for anything else.

    "60" becomes 60, " 0.05 " becomes 0.05. Not finite values ("nan",
    "inf") and anything that is not a string give None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or '_' in text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def coerce_numeric_strings(config: dict) -> list:
    """Replace numeric strings in the two sections by their numbers.

    Changes config in place and returns (key, raw value, number) for every
    replaced value. The Home Assistant add-on passes some numbers as
    strings; the core reads several values unconverted and would fail on
    them in the control cycle. Keys are never added or removed.
    """
    coerced = []
    for section_name, keys in NUMERIC_KEYS.items():
        section = config.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in keys:
            raw = section.get(key)
            number = parse_numeric_string(raw)
            if number is not None:
                section[key] = number
                coerced.append((f'{section_name}.{key}', raw, number))
    return coerced


def _with_parser(parser: Callable, key: str) -> BeforeValidator:
    """Run one of the existing value_utils parsers on the raw value.

    Using the same parser as the core keeps model and code in agreement on
    what is valid, and the error message names the config key.
    """
    return BeforeValidator(lambda value: parser(value, key))


def _parse_logic_type(value) -> str:
    """Mirror Logic.create_logic(): case-insensitive, default or next."""
    if not isinstance(value, str) or value.lower() not in KNOWN_LOGIC_TYPES:
        raise ValueError(
            f'{SECTION}.type must be one of {KNOWN_LOGIC_TYPES}, '
            f'got {value!r}')
    return value.lower()


def _parse_grid_charge_target_strategy(value) -> str:
    """Mirror GridChargeTargetConfig.from_battery_control_config()."""
    return GridChargeTargetConfig.from_battery_control_config(
        {'grid_charge_target_strategy': value}).strategy


def _add_warning(info: ValidationInfo, message: str) -> None:
    """Collect a plausibility warning in the validation context."""
    if info.context is not None:
        info.context.setdefault('warnings', []).append(message)


def _unparsed_number(value, info: ValidationInfo) -> float:
    """Accept what the core can compute with: int, float and bool.

    The core reads these values without conversion. A numeric string is
    accepted because coerce_numeric_strings() replaces it by its number
    before the core reads it. Any other string, None or a list fails
    later in the control cycle (TypeError), so it is an error.
    A bool is an int subclass and works in every consumer, so it is only
    a plausibility warning: a configuration that runs today keeps running,
    in strict mode as well.
    """
    key = f'{SECTION}.{info.field_name}'
    if isinstance(value, bool):
        _add_warning(
            info,
            f'{key} is {value!r}, expected a number; it is used as '
            f'{int(value)}.')
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    number = parse_numeric_string(value)
    if number is not None:
        return float(number)
    raise ValueError(
        f'{key} must be a number, got {value!r} '
        f'({type(value).__name__})')


# A number read by the core without conversion, see _unparsed_number
UnparsedNumber = Annotated[float, BeforeValidator(_unparsed_number)]


def _warn_outside(info: ValidationInfo, value: float, key: str,
                  limits: tuple, hint: str = '') -> None:
    """Warn if value is not finite or outside limits (lower, upper).

    Either limit may be None for an open end.
    """
    lower, upper = limits
    below = lower is not None and value < lower
    above = upper is not None and value > upper
    if math.isfinite(value) and not below and not above:
        return
    if lower is not None and upper is not None:
        expected = f'between {lower:g} and {upper:g}'
    elif lower is not None:
        expected = f'>= {lower:g}'
    else:
        expected = f'<= {upper:g}'
    _add_warning(
        info,
        f'{key} is {value!r}, expected a value {expected}.'
        + (f' {hint}' if hint else ''))


class BatteryControlSection(BaseModel):
    """The battery_control section.

    The five numeric values are read by the core without conversion, so
    they must be numbers or numeric strings (which are coerced):
    UnparsedNumber rejects other strings and empty values that would
    otherwise fail later in the control cycle, and only warns about
    booleans, which the code computes with as 0 and 1.
    """
    model_config = ConfigDict(extra='allow')

    type: Annotated[str, BeforeValidator(_parse_logic_type)] = 'default'
    min_price_difference: UnparsedNumber = 0.05
    min_price_difference_rel: UnparsedNumber = 0.0
    always_allow_discharge_limit: UnparsedNumber = 0.9
    max_charging_from_grid_limit: UnparsedNumber = 0.8
    min_recharge_amount: UnparsedNumber = 100.0
    min_grid_charge_soc: Annotated[
        Optional[float],
        _with_parser(parse_optional_ratio, f'{SECTION}.min_grid_charge_soc'),
    ] = None
    grid_charge_target_strategy: Annotated[
        str, BeforeValidator(_parse_grid_charge_target_strategy),
    ] = 'fixed'
    # Deprecated location. The core validates it only if
    # battery_control_expert does not set the value, so it is not checked
    # here either.
    charge_rate_multiplier: Any = None

    @field_validator('always_allow_discharge_limit',
                     'max_charging_from_grid_limit')
    @classmethod
    def _check_soc_ratio(cls, value, info):
        if info.field_name == 'always_allow_discharge_limit' and value <= 0:
            # Fatal, see check_always_allow_disabled(); a warning on top
            # would only repeat it
            return value
        hint = ''
        if value > 1:
            hint = ('It is a state of charge ratio (0.9 = 90 % SoC), '
                    'not a percentage.')
        _warn_outside(info, value, f'{SECTION}.{info.field_name}',
                      (0, 1), hint)
        return value

    @field_validator('min_price_difference', 'min_price_difference_rel',
                     'min_recharge_amount')
    @classmethod
    def _check_non_negative(cls, value, info):
        _warn_outside(info, value, f'{SECTION}.{info.field_name}',
                      (0, None))
        return value


class BatteryControlExpertSection(BaseModel):
    """The battery_control_expert section.

    Every value is validated by the core at startup already; the model uses
    the same parsers and adds plausibility warnings. All fields default to
    None because the core distinguishes "not written" from "written".
    """
    model_config = ConfigDict(extra='allow')

    charge_rate_multiplier: Annotated[
        Optional[float],
        _with_parser(parse_positive_number,
                     f'{EXPERT_SECTION}.charge_rate_multiplier'),
    ] = None
    soften_price_difference_on_charging: Annotated[
        Optional[bool],
        _with_parser(parse_bool_config,
                     f'{EXPERT_SECTION}.soften_price_difference_on_charging'),
    ] = None
    soften_price_difference_on_charging_factor: Annotated[
        Optional[float],
        _with_parser(
            parse_positive_number,
            f'{EXPERT_SECTION}.soften_price_difference_on_charging_factor'),
    ] = None
    spread_grid_charge_over_charge_window: Annotated[
        Optional[bool],
        _with_parser(
            parse_bool_config,
            f'{EXPERT_SECTION}.spread_grid_charge_over_charge_window'),
    ] = None
    round_price_digits: Annotated[
        Optional[int],
        _with_parser(parse_integer, f'{EXPERT_SECTION}.round_price_digits'),
    ] = None
    production_offset_percent: Annotated[
        Optional[float],
        _with_parser(parse_non_negative_number,
                     f'{EXPERT_SECTION}.production_offset_percent'),
    ] = None
    preserve_min_grid_charge_soc: Annotated[
        Optional[bool],
        _with_parser(parse_bool_config,
                     f'{EXPERT_SECTION}.preserve_min_grid_charge_soc'),
    ] = None
    market_price_refresh_time: Annotated[
        Optional[str],
        _with_parser(parse_time_of_day,
                     f'{EXPERT_SECTION}.market_price_refresh_time'),
    ] = None
    max_forecast_hours: Annotated[
        Optional[float],
        _with_parser(parse_non_negative_number,
                     f'{EXPERT_SECTION}.max_forecast_hours'),
    ] = None

    @field_validator('round_price_digits')
    @classmethod
    def _check_round_price_digits(cls, value, info):
        if value is not None and value < 0:
            _add_warning(
                info,
                f'{EXPERT_SECTION}.round_price_digits is {value}, expected '
                'a value >= 0. A negative value rounds to tens or hundreds, '
                'which turns every price into 0 and stops grid charging.')
        return value

    @field_validator('production_offset_percent')
    @classmethod
    def _check_production_offset(cls, value, info):
        if value is not None:
            _warn_outside(
                info, value, f'{EXPERT_SECTION}.production_offset_percent',
                (0, MAX_PRODUCTION_OFFSET),
                'It is a factor (1.0 = forecast unchanged), not a '
                'percentage.')
        return value


def unknown_keys(section: dict, model: type[BaseModel]) -> list:
    """Keys of a section the model does not know, in config order."""
    return [key for key in section if key not in model.model_fields]


def check_always_allow_disabled(always_allow_discharge_limit) -> Optional[str]:
    """Error text if always_allow_discharge_limit switches the control off.

    With 0 or below, discharging is always allowed at any SoC, so the
    price based control never decides anything. False counts as 0, it is
    used as 0 by the code; a numeric string is read as its number.
    """
    value = always_allow_discharge_limit
    number = parse_numeric_string(value)
    if number is None and isinstance(value, str) and '_' not in value:
        # parse_numeric_string() refuses non finite values, but a negative
        # infinity ("-inf", "-1e400") switches the control off as well
        try:
            number = float(value.strip())
        except ValueError:
            number = None
        if number is not None and not number == -math.inf:
            number = None
    if number is not None:
        value = number
    if not isinstance(value, (int, float)) or not value <= 0:
        # not value <= 0: NaN is not fatal, it gets a warning instead
        return None
    return (
        f'{SECTION}.always_allow_discharge_limit is {always_allow_discharge_limit!r}: '
        'with 0 or below, discharging would always be allowed and the '
        'price based control would never run. Set the state of charge '
        'ratio above which discharging is always allowed, e.g. 0.9 for '
        '90 % SoC; 1.0 allows it only at a full battery.')


def check_max_soc(always_allow_discharge_limit, inverter_max_soc) -> Optional[str]:
    """Warning text if the inverter max_soc makes Always-Allow unreachable.

    always_allow_discharge_limit is a real state of charge ratio (0.9 =
    90 % SoC, see CommonLogic.get_soc_capacity), the inverter max_soc is a
    percentage. A battery that never charges above max_soc never reaches
    a higher Always-Allow limit.
    """
    if not _is_number(always_allow_discharge_limit) \
            or not _is_number(inverter_max_soc):
        return None
    if inverter_max_soc <= 0 or not 0 < always_allow_discharge_limit < 1:
        # max_soc unknown (-1 until the inverter reported it), a limit
        # that already got its own warning or error, or 1.0 and above:
        # Always-Allow deliberately (almost) off, nothing to reach
        return None
    # Same comparison as CommonLogic.is_discharge_always_allowed_soc();
    # limit * 100 would round 0.55 to 55.00000000000001 and warn wrongly
    if inverter_max_soc / 100 >= always_allow_discharge_limit:
        return None
    return (
        f'The inverter max_soc ({inverter_max_soc:g} %) is below '
        f'{SECTION}.always_allow_discharge_limit '
        f'({always_allow_discharge_limit:g}); the battery never reaches '
        'the SoC above which discharging is always allowed. Lower '
        'always_allow_discharge_limit or raise max_soc.')


def _is_number(value) -> bool:
    """True for a finite int or float that is not a bool."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))
