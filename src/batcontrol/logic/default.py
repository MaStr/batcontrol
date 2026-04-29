import logging
import datetime
import numpy as np
from typing import Optional

from .logic_interface import LogicInterface
from .logic_interface import CalculationParameters, CalculationInput
from .logic_interface import CalculationOutput, InverterControlSettings
from .common import CommonLogic
from .decision_logging import GridRechargeDecision, log_grid_recharge_decision

# Minimum remaining time in hours to prevent division by very small numbers
# when calculating charge rates. This constant serves as a safety threshold:
# - Prevents extremely high charge rates at the end of intervals
# - Ensures charge rate calculations remain within reasonable bounds
# - 1 minute (1/60 hour) is chosen as it allows adequate time for the inverter
#   to respond while preventing numerical instability in the calculation
MIN_REMAINING_TIME_HOURS = 1.0 / 60.0  # 1 minute expressed in hours

logger = logging.getLogger(__name__)

class DefaultLogic(LogicInterface):
    """ Default logic class for Batcontrol. """

    def __init__(self, timezone: datetime.timezone = datetime.timezone.utc,
                 interval_minutes: int = 60):
        self.calculation_parameters = None
        self.calculation_output = None
        self.inverter_control_settings = None
        self.round_price_digits = 4  # Default rounding for prices
        self.soften_price_difference_on_charging = False
        self.soften_price_difference_on_charging_factor = 5.0  # Default factor
        self.timezone = timezone
        self.interval_minutes = interval_minutes
        self.common = CommonLogic.get_instance()

    def set_round_price_digits(self, digits: int):
        """ Set the number of digits to round prices to """
        self.round_price_digits = digits

    def set_soften_price_difference_on_charging(self, soften: bool, factor: float = 5):
        """ Set if the price difference should be softened on charging """
        self.soften_price_difference_on_charging = soften
        self.soften_price_difference_on_charging_factor = factor

    def set_calculation_parameters(self, parameters: CalculationParameters):
        """ Set the calculation parameters for the logic """
        self.calculation_parameters = parameters
        self.common.max_capacity= parameters.max_capacity

    def set_timezone(self, timezone: datetime.timezone):
        """ Set the timezone for the logic calculations """
        self.timezone = timezone

    def calculate(self, input_data: CalculationInput, calc_timestamp: Optional[datetime.datetime] = None) -> bool:
        """ Calculate the inverter control settings based on the input data """

        logger.debug("Calculating inverter control settings...")

        if calc_timestamp is None:
            calc_timestamp = datetime.datetime.now().astimezone(self.timezone)

        self.calculation_output = CalculationOutput(
            reserved_energy=0.0,
            required_recharge_energy=0.0,
            min_dynamic_price_difference=0.0
       )

        self.inverter_control_settings = self.calculate_inverter_mode(
            input_data,
            calc_timestamp
        )
        return True

    def get_calculation_output(self) -> CalculationOutput:
        """ Get the calculation output from the last calculation """
        return self.calculation_output

    def get_inverter_control_settings(self) -> InverterControlSettings:
        """ Get the inverter control settings from the last calculation """
        return self.inverter_control_settings

    def calculate_inverter_mode(self, calc_input: CalculationInput,
                                calc_timestamp: Optional[datetime.datetime] = None) -> InverterControlSettings:
        """ Main control logic for battery control """
        # default settings
        inverter_control_settings = InverterControlSettings(
            allow_discharge=False,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1
        )

        if self.calculation_output is None:
            logger.error("Calculation output is not set. Please call calculate() first.")
            raise ValueError("Calculation output is not set. Please call calculate() first.")

        net_consumption = calc_input.consumption - calc_input.production
        prices = calc_input.prices

        if calc_timestamp is None:
            calc_timestamp = datetime.datetime.now().astimezone(self.timezone)

        # ensure availability of data
        max_slot = min(len(net_consumption), len(prices))

        if self.__is_discharge_allowed(calc_input, net_consumption, prices, calc_timestamp):
            inverter_control_settings.allow_discharge = True
            inverter_control_settings.limit_battery_charge_rate = -1 # no limit

            return inverter_control_settings
        else:  # discharge not allowed
            logger.debug('Discharging is NOT allowed')
            inverter_control_settings.allow_discharge = False
            charging_limit_percent = self.calculation_parameters.max_charging_from_grid_limit * 100
            charge_limit_capacity = self.common.max_capacity * \
                self.calculation_parameters.max_charging_from_grid_limit
            is_charging_possible = calc_input.stored_energy < charge_limit_capacity

            # Defaults to 0, only calculate if charging is possible
            required_recharge_energy = 0

            logger.debug('Charging allowed: %s', is_charging_possible)
            if is_charging_possible:
                logger.debug('Charging is allowed, because SOC is below %.0f%%',
                             charging_limit_percent
                             )
                required_recharge_energy = self.__get_required_recharge_energy(
                    calc_input,
                    net_consumption[:max_slot],
                    prices
                )
            else:
                logger.debug('Charging is NOT allowed, because SOC is above %.0f%%',
                             charging_limit_percent
                             )

            if required_recharge_energy > 0:
                allowed_charging_energy = charge_limit_capacity - calc_input.stored_energy
                if required_recharge_energy > allowed_charging_energy:
                    required_recharge_energy = allowed_charging_energy
                    logger.debug(
                        'Required recharge energy limited by max. charging limit to %0.1f Wh',
                        required_recharge_energy
                    )
                logger.info(
                    'Get additional energy via grid: %0.1f Wh',
                    required_recharge_energy
                )
            elif required_recharge_energy == 0 and is_charging_possible:
                logger.debug(
                    'No additional energy required or possible price found.')

            # charge if battery capacity available and more stored energy is required
            if is_charging_possible and required_recharge_energy > 0:
                # Calculate remaining time in current interval to determine charge rate
                # The charge rate must be sufficient to reach target energy before the
                # current price interval ends, while staying within safe operating limits
                current_minute = calc_timestamp.minute
                current_second = calc_timestamp.second

                if self.interval_minutes == 15:
                    # For 15-minute intervals: find start of current interval (0, 15, 30, or 45)
                    # and calculate time remaining until the next interval boundary
                    current_interval_start = (current_minute // 15) * 15
                    remaining_minutes = (current_interval_start + 15
                                         - current_minute - current_second / 60)
                else:  # 60 minutes
                    # For 60-minute intervals: calculate time remaining until next hour
                    remaining_minutes = 60 - current_minute - current_second / 60

                # Convert remaining time to hours for charge rate calculation
                remaining_time = remaining_minutes / 60

                # Apply minimum time threshold to prevent extreme charge rates
                # Near the end of an interval (e.g., at XX:59:59), the remaining time
                # approaches zero, which would cause charge_rate = energy / time to spike
                # to unrealistic values. MIN_REMAINING_TIME_HOURS ensures we never divide
                # by less than 1 minute, keeping charge rates within practical bounds.
                # Note: interval_minutes is validated in core.py (must be 15 or 60)
                remaining_time = max(remaining_time, MIN_REMAINING_TIME_HOURS)

                # Calculate required charge rate: energy needed / time available
                charge_rate = required_recharge_energy / remaining_time

                charge_rate = self.common.calculate_charge_rate(charge_rate)

                log_grid_recharge_decision(
                    logger,
                    self.calculation_output,
                    calc_input,
                    prices,
                    GridRechargeDecision(
                        recharge_energy=required_recharge_energy,
                        allowed_charging_energy=allowed_charging_energy,
                        remaining_time=remaining_time,
                        charge_rate=charge_rate
                    )
                )

                #self.force_charge(charge_rate)
                inverter_control_settings.charge_from_grid = True
                inverter_control_settings.charge_rate = charge_rate
            else:
                # keep current charge level. recharge if solar surplus available
                inverter_control_settings.allow_discharge = False
        #
        return inverter_control_settings

    def __is_discharge_allowed(self, calc_input: CalculationInput,
                                    net_consumption: np.ndarray,
                                    prices: dict,
                                    calc_timestamp: Optional[datetime.datetime] = None) -> bool:
        """ Evaluate if the battery is allowed to discharge

            - Check if battery is above always_allow_discharge_limit
            - Calculate required energy to shift toward high price hours
            - Check if discharge is blocked by external source

            return: bool
        """
        if calc_timestamp is None:
            calc_timestamp = datetime.datetime.now().astimezone(self.timezone)

        if self.common.is_discharge_always_allowed_capacity(calc_input.stored_energy):
            logger.info(
                "[Rule] Discharge allowed due to always_allow_discharge_limit")
            return True

        current_price = prices[0]

        min_dynamic_price_difference = self.__calculate_min_dynamic_price_difference(
            current_price)

        self.calculation_output.min_dynamic_price_difference = min_dynamic_price_difference

        max_slots = len(net_consumption)
        # relevant time range : until next recharge possibility
        for slot in range(1, max_slots):
            future_price = prices[slot]
            if future_price <= current_price-min_dynamic_price_difference:
                max_slots = slot
                logger.debug(
                    "[Rule] Recharge possible in %d slots, limiting evaluation window.",
                    slot)
                logger.debug(
                    "[Rule] Future price: %.3f < Current price: %.3f - dyn_price_diff. %.3f ",
                    future_price,
                    current_price,
                    min_dynamic_price_difference
                )
                break

        slot_start = calc_timestamp.replace(
            minute=(calc_timestamp.minute // self.interval_minutes) * self.interval_minutes,
            second=0,
            microsecond=0
        )
        last_time = (slot_start + datetime.timedelta(
            minutes=max_slots * self.interval_minutes
        )).astimezone(self.timezone).strftime("%H:%M")

        logger.debug(
            'Evaluating next %d slots until %s',
            max_slots,
            last_time
        )
        # distribute remaining energy
        consumption = np.array(net_consumption)
        consumption[consumption < 0] = 0

        production = -np.array(net_consumption)
        production[production < 0] = 0

        # get slots with higher price
        higher_price_slots = []
        for slot in range(max_slots):
            future_price = prices[slot]
            # !!! different formula compared to detect relevant slots
            if future_price > current_price:
                higher_price_slots.append(slot)

        higher_price_slots.sort()
        higher_price_slots.reverse()

        reserved_storage = 0
        for higher_price_slot in higher_price_slots:
            if consumption[higher_price_slot] == 0:
                continue
            required_energy = consumption[higher_price_slot]

            # correct reserved_storage with potential production
            # start with latest slot
            for slot in list(range(higher_price_slot))[::-1]:
                if production[slot] == 0:
                    continue
                if production[slot] >= required_energy:
                    production[slot] -= required_energy
                    required_energy = 0
                    break
                else:
                    required_energy -= production[slot]
                    production[slot] = 0
            # add_remaining required_energy to reserved_storage
            reserved_storage += required_energy

        min_grid_charge_soc_active = (
            self.calculation_parameters.preserve_min_grid_charge_soc
            and reserved_storage > 0
            and self.__has_grid_charge_soc_price_signal(
                consumption,
                prices,
                max_slots,
                current_price,
                min_dynamic_price_difference
            )
        )
        reserved_storage = self.common.apply_min_grid_charge_soc_reserve(
            reserved_storage,
            calc_input.stored_energy,
            calc_input.stored_usable_energy,
            self.calculation_parameters.min_grid_charge_soc,
            min_grid_charge_soc_active
        )

        self.calculation_output.reserved_energy = reserved_storage

        if len(higher_price_slots) > 0:
            # This message refers to relative slots (e.g. "next 2 slots"),
            # not specific clock times (e.g. "at 2 o'clock").
            logger.debug("[Rule] Reserved Energy will be used in the next slots: %s",
                         higher_price_slots[::-1])
            logger.debug(
                "[Rule] Reserved Energy: %0.1f Wh. Usable in Battery: %0.1f Wh",
                reserved_storage,
                calc_input.stored_usable_energy
            )
        else:
            logger.debug("[Rule] No reserved energy required, because no "
                         "'high price' slots in evaluation window.")


        if calc_input.stored_usable_energy > reserved_storage:
            # allow discharging
            logger.debug(
                "[Rule] Discharge allowed. Stored usable energy %0.1f Wh >"
                " Reserved energy %0.1f Wh",
                calc_input.stored_usable_energy,
                reserved_storage
            )
            return True

        # forbid discharging
        logger.debug(
            "[Rule] Discharge forbidden. Stored usable energy %0.1f Wh <= Reserved energy %0.1f Wh",
            calc_input.stored_usable_energy,
            reserved_storage
        )

        return False

    def __has_grid_charge_soc_price_signal(self, consumption: np.ndarray,
                                           prices: dict,
                                           max_slots: int,
                                           current_price: float,
                                           min_dynamic_price_difference: float) -> bool:
        """Return True when a future price justifies preserving a grid-charge SoC target."""
        for slot in range(max_slots):
            future_price = prices[slot]
            if (consumption[slot] > 0
                    and future_price > current_price + min_dynamic_price_difference):
                return True
        return False

 # %%
    def __get_required_recharge_energy(self, calc_input: CalculationInput,
                                              net_consumption: list, prices: dict) -> float:
        """ Calculate the required energy to shift toward high price slots.

            If a recharge price window is detected, the energy required to
            recharge the battery to the next high price slots is calculated.

            return: float (Energy in Wh)
         """
        current_price = prices[0]
        max_slot = len(net_consumption)
        consumption = np.array(net_consumption)
        consumption[consumption < 0] = 0

        production = -np.array(net_consumption)
        production[production < 0] = 0
        min_price_difference = self.calculation_parameters.min_price_difference
        min_dynamic_price_difference = self.__calculate_min_dynamic_price_difference(
            current_price)

        # evaluation period until price is first time lower then current price
        for slot in range(1, max_slot):
            future_price = prices[slot]
            found_lower_price = False
            # Soften the price difference to avoid too early charging
            if self.soften_price_difference_on_charging:
                modified_price = current_price-min_price_difference / \
                    self.soften_price_difference_on_charging_factor
                found_lower_price = future_price <= modified_price
            else:
                found_lower_price = future_price <= current_price

            if found_lower_price:
                max_slot = slot
                break

        # As max_slot is 1 at minimum, no protection against out of range needed.
        logger.debug(
            "[Rule] Evaluation window for recharge energy until slot %d with price %0.3f",
            max_slot-1,
            prices[max_slot-1]
        )

        # get high price slots
        high_price_slots = []
        for slot in range(max_slot):
            future_price = prices[slot]
            if future_price > current_price+min_dynamic_price_difference:
                high_price_slots.append(slot)

        # start with nearest slot
        high_price_slots.sort()
        required_energy = 0.0
        for high_price_slot in high_price_slots:
            energy_to_shift = consumption[high_price_slot]

            # correct energy to shift with potential production
            # start with nearest slot
            for slot in range(1, high_price_slot):
                if production[slot] == 0:
                    continue
                if production[slot] >= energy_to_shift:
                    production[slot] -= energy_to_shift
                    energy_to_shift = 0
                else:
                    energy_to_shift -= production[slot]
                    production[slot] = 0
            # add_remaining energy to shift to recharge amount
            required_energy += energy_to_shift

        if required_energy > 0.0:
            logger.debug("[Rule] Required Energy: %0.1f Wh is based on next 'high price' slots %s",
                         required_energy,
                         high_price_slots
                         )
            recharge_energy = required_energy-calc_input.stored_usable_energy
            logger.debug("[Rule] Stored usable Energy: %0.1f , Recharge Energy: %0.1f Wh",
                         calc_input.stored_usable_energy,
                         recharge_energy
                         )
        else:
            logger.debug(
                "[Rule] No additional energy required, because stored energy is sufficient."
            )
            recharge_energy = 0.0
            self.calculation_output.required_recharge_energy = recharge_energy
            return recharge_energy

        recharge_energy = self.common.apply_min_grid_charge_soc_target(
            recharge_energy,
            calc_input.stored_energy,
            self.calculation_parameters.min_grid_charge_soc
        )

        free_capacity = calc_input.free_capacity

        if recharge_energy > free_capacity:
            recharge_energy = free_capacity
            logger.debug(
                "[Rule] Recharge limited by free capacity: %0.1f Wh", recharge_energy)

        if not self.common.is_charging_above_minimum(recharge_energy):
            recharge_energy = 0.0
        else:
            # We are adding that minimum charge energy here, so that we are not stuck
            #   between limits.
            recharge_energy = recharge_energy + self.common.min_charge_energy

        self.calculation_output.required_recharge_energy = recharge_energy
        return recharge_energy

    def __calculate_min_dynamic_price_difference(self, price: float) -> float:
        """ Calculate the dynamic limit for the current price """
        return round(
            max(self.calculation_parameters.min_price_difference,
                self.calculation_parameters.min_price_difference_rel * abs(price)),
            self.round_price_digits
        )
