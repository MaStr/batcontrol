""" Factory for logic classes. """
import logging

from .logic_interface import LogicInterface
from .default import DefaultLogic

logger = logging.getLogger(__name__)

class Logic:
    """ Factory for logic classes. """
    print_class_message = True
    @staticmethod
    def create_logic(time_resolution_minutes: int, config: dict, timezone) -> LogicInterface:
        """ Select and configure a logic class based on the given configuration """
        request_type = config.get('type', 'default').lower()
        logic = None
        if request_type == 'default':
            if Logic.print_class_message:
                logger.info('Using default logic')
                Logic.print_class_message = False
            logic = DefaultLogic(timezone, interval_minutes=time_resolution_minutes)
            if config.get('battery_control_expert', None) is not None:
                battery_control_expert = config.get( 'battery_control_expert', {})
                attribute_list = [
                    'soften_price_difference_on_charging',
                    'soften_price_difference_on_charging_factor',
                    'round_price_digits',
                    'charge_rate_multiplier',
                ]
                for attribute in attribute_list:
                    if attribute in battery_control_expert:
                        logger.debug('Setting %s to %s', attribute ,
                                                    battery_control_expert[attribute])
                        setattr(logic, attribute, battery_control_expert[attribute])
        else:
            raise RuntimeError(f'[Logic] Unknown logic type {config["type"]}')
        return logic
