""" Factory for logic classes. """
import logging

from .logic_interface import LogicInterface
from .default import DefaultLogic
from .next import NextLogic
from ..value_utils import (
    parse_bool_config,
    parse_integer,
    parse_positive_number,
)

logger = logging.getLogger(__name__)

# Expert tuning attributes that are set directly on the logic instance, with
# the parser that validates their value.
_EXPERT_ATTRIBUTE_PARSERS = {
    'soften_price_difference_on_charging': parse_bool_config,
    # Used as a divisor, so zero and negative values are invalid
    'soften_price_difference_on_charging_factor': parse_positive_number,
    'spread_grid_charge_over_charge_window': parse_bool_config,
    'round_price_digits': parse_integer,
}


def parse_logic_expert_settings(battery_control_expert) -> dict:
    """ Validate the battery_control_expert values the logic uses.

    Returns the parsed value for every logic attribute present in the
    section. Raises ValueError naming the config key and the value for
    anything that cannot be used. Called at startup by the core, so a typo
    stops batcontrol right away instead of failing in the middle of a later
    grid recharge calculation.
    """
    if battery_control_expert is None:
        return {}
    if not isinstance(battery_control_expert, dict):
        raise ValueError(
            'battery_control_expert must be a mapping of key: value '
            f'settings, got {type(battery_control_expert).__name__}'
        )
    settings = {}
    for attribute, parser in _EXPERT_ATTRIBUTE_PARSERS.items():
        if attribute in battery_control_expert:
            settings[attribute] = parser(
                battery_control_expert[attribute],
                f'battery_control_expert.{attribute}')
    return settings

class Logic:
    """ Factory for logic classes. """
    print_class_message = True
    @staticmethod
    def create_logic(time_resolution_minutes: int, config: dict, timezone) -> LogicInterface:
        """ Select and configure a logic class based on the given configuration """
        battery_control = config.get('battery_control', {})
        request_type = battery_control.get('type', 'default').lower()
        logic = None
        if request_type == 'default':
            if Logic.print_class_message:
                logger.info('Using "default" logic (incl. peak shaving)')
                Logic.print_class_message = False
            logic = DefaultLogic(timezone, interval_minutes=time_resolution_minutes)
        elif request_type == 'next':
            if Logic.print_class_message:
                logger.info('Using "next" logic (alias for "default"; '
                            'peak shaving is now part of "default")')
                Logic.print_class_message = False
            logic = NextLogic(timezone, interval_minutes=time_resolution_minutes)
        else:
            raise RuntimeError(
                f'[Logic] Unknown logic type "{request_type}" specified in configuration')

        # Apply expert tuning attributes (validated, see
        # parse_logic_expert_settings)
        expert_settings = parse_logic_expert_settings(
            config.get('battery_control_expert', None))
        for attribute, value in expert_settings.items():
            logger.debug('Setting %s to %s', attribute, value)
            setattr(logic, attribute, value)
        return logic
