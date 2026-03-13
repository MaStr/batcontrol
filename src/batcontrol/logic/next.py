"""NextLogic — Extended battery control logic with peak shaving.

This module provides the NextLogic class, which extends the DefaultLogic
behavior with a peak shaving post-processing step. Peak shaving manages
PV battery charging rate so the battery fills up gradually, reaching full
capacity by a configurable target hour (allow_full_battery_after).

This prevents the battery from being full too early in the day,
avoiding excessive feed-in during midday PV peak hours.

Usage:
    Select via ``type: next`` in the battery_control config section.
"""
import logging
import datetime
import numpy as np
from typing import Optional

from .logic_interface import LogicInterface
from .logic_interface import CalculationParameters, CalculationInput
from .logic_interface import CalculationOutput, InverterControlSettings
from .common import CommonLogic

# Minimum remaining time in hours to prevent division by very small numbers
# when calculating charge rates. This constant serves as a safety threshold:
# - Prevents extremely high charge rates at the end of intervals
# - Ensures charge rate calculations remain within reasonable bounds
# - 1 minute (1/60 hour) is chosen as it allows adequate time for the inverter
#   to respond while preventing numerical instability in the calculation
MIN_REMAINING_TIME_HOURS = 1.0 / 60.0  # 1 minute expressed in hours

logger = logging.getLogger(__name__)


class NextLogic(LogicInterface):
    """Extended logic class for Batcontrol with peak shaving support.

    Contains all DefaultLogic behaviour plus a peak shaving post-processing
    step that limits PV charge rate to spread battery charging over time.
    """

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
        self.common.max_capacity = parameters.max_capacity

    def set_timezone(self, timezone: datetime.timezone):
        """ Set the timezone for the logic calculations """
        self.timezone = timezone

    def calculate(self, input_data: CalculationInput,
                  calc_timestamp: Optional[datetime.datetime] = None) -> bool:
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

    # ------------------------------------------------------------------ #
    #  Main control logic (same as DefaultLogic)                          #
    # ------------------------------------------------------------------ #

    def calculate_inverter_mode(self, calc_input: CalculationInput,
                                calc_timestamp: Optional[datetime.datetime] = None
                                ) -> InverterControlSettings:
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

        if self._is_discharge_allowed(calc_input, net_consumption, prices, calc_timestamp):
            inverter_control_settings.allow_discharge = True
            inverter_control_settings.limit_battery_charge_rate = -1  # no limit

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
                             charging_limit_percent)
                required_recharge_energy = self._get_required_recharge_energy(
                    calc_input,
                    net_consumption[:max_slot],
                    prices
                )
            else:
                logger.debug('Charging is NOT allowed, because SOC is above %.0f%%',
                             charging_limit_percent)

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
                current_minute = calc_timestamp.minute
                current_second = calc_timestamp.second

                if self.interval_minutes == 15:
                    current_interval_start = (current_minute // 15) * 15
                    remaining_minutes = (current_interval_start + 15
                                         - current_minute - current_second / 60)
                else:  # 60 minutes
                    remaining_minutes = 60 - current_minute - current_second / 60

                remaining_time = remaining_minutes / 60
                remaining_time = max(remaining_time, MIN_REMAINING_TIME_HOURS)

                charge_rate = required_recharge_energy / remaining_time
                charge_rate = self.common.calculate_charge_rate(charge_rate)

                inverter_control_settings.charge_from_grid = True
                inverter_control_settings.charge_rate = charge_rate
            else:
                # keep current charge level. recharge if solar surplus available
                inverter_control_settings.allow_discharge = False

        # ----- Peak Shaving Post-Processing ----- #
        if self.calculation_parameters.peak_shaving_enabled:
            inverter_control_settings = self._apply_peak_shaving(
                inverter_control_settings, calc_input, calc_timestamp)

        return inverter_control_settings

    # ------------------------------------------------------------------ #
    #  Peak Shaving                                                       #
    # ------------------------------------------------------------------ #

    def _apply_peak_shaving(self, settings: InverterControlSettings,
                            calc_input: CalculationInput,
                            calc_timestamp: datetime.datetime
                            ) -> InverterControlSettings:
        """Limit PV charge rate to spread battery charging until target hour.

        Peak shaving uses MODE 8 (limit_battery_charge_rate with
        allow_discharge=True). It is only applied when the main logic
        already allows discharge — meaning no upcoming high-price slots
        require preserving battery energy.

        The algorithm has two components that are combined (stricter wins):
        - Price-based: reserve battery capacity for upcoming cheap-price PV
          slots so they can be absorbed fully (primary driver). Requires
          peak_shaving_price_limit to be configured; disabled when None.
        - Time-based: spread remaining capacity over slots until
          allow_full_battery_after (secondary constraint).

        Skipped when:
        - price_limit is not configured (peak shaving disabled)
        - No production right now (nighttime)
        - Currently in a cheap slot (price <= price_limit) — charge freely
        - Past the target hour (allow_full_battery_after)
        - Battery is in always_allow_discharge region (high SOC)
        - Force charge from grid is active (MODE -1)
        - Discharge not allowed (battery preserved for high-price hours)

        Note: EVCC checks (charging, connected+pv mode) are handled in
              core.py, not here.
        """
        price_limit = self.calculation_parameters.peak_shaving_price_limit

        # price_limit not configured → peak shaving is disabled
        if price_limit is None:
            logger.debug('[PeakShaving] Skipped: price_limit not configured')
            return settings

        # No production right now: skip calculation (avoid unnecessary work at night)
        if calc_input.production[0] <= 0:
            return settings

        # Currently in a cheap slot — charge battery freely
        if calc_input.prices[0] <= price_limit:
            logger.debug('[PeakShaving] Skipped: currently in cheap-price slot '
                         '(price %.3f <= limit %.3f)',
                         calc_input.prices[0], price_limit)
            return settings

        # After target hour: no limit
        if calc_timestamp.hour >= self.calculation_parameters.peak_shaving_allow_full_after:
            return settings

        # In always_allow_discharge region: skip peak shaving
        if self.common.is_discharge_always_allowed_capacity(calc_input.stored_energy):
            logger.debug('[PeakShaving] Skipped: battery in always_allow_discharge region')
            return settings

        # Force charge takes priority over peak shaving
        if settings.charge_from_grid:
            logger.warning('[PeakShaving] Skipped: force_charge (MODE -1) active, '
                           'grid charging takes priority')
            return settings

        # Battery preserved for high-price hours — don't limit PV charging
        if not settings.allow_discharge:
            logger.debug('[PeakShaving] Skipped: discharge not allowed, '
                         'battery preserved for high-price hours')
            return settings

        # Compute both limits, take stricter (lower non-negative value)
        price_limit_w = self._calculate_peak_shaving_charge_limit_price_based(calc_input)
        time_limit_w = self._calculate_peak_shaving_charge_limit(calc_input, calc_timestamp)

        if price_limit_w < 0 and time_limit_w < 0:
            logger.debug('[PeakShaving] Evaluated: no limit needed')
            return settings

        if price_limit_w >= 0 and time_limit_w >= 0:
            charge_limit = min(price_limit_w, time_limit_w)
        elif price_limit_w >= 0:
            charge_limit = price_limit_w
        else:
            charge_limit = time_limit_w

        # Apply PV charge rate limit
        if settings.limit_battery_charge_rate < 0:
            # No existing limit — apply peak shaving limit
            settings.limit_battery_charge_rate = charge_limit
        else:
            # Keep the more restrictive limit
            settings.limit_battery_charge_rate = min(
                settings.limit_battery_charge_rate, charge_limit)

        # Note: allow_discharge is already True here (checked above).
        # MODE 8 requires allow_discharge=True to work correctly.

        logger.info('[PeakShaving] PV charge limit: %d W '
                    '(price-based=%s W, time-based=%s W, full by %d:00)',
                    settings.limit_battery_charge_rate,
                    price_limit_w if price_limit_w >= 0 else 'off',
                    time_limit_w if time_limit_w >= 0 else 'off',
                    self.calculation_parameters.peak_shaving_allow_full_after)

        return settings

    def _calculate_peak_shaving_charge_limit_price_based(
            self, calc_input: CalculationInput) -> int:
        """Reserve battery free capacity for upcoming cheap-price PV slots.

        Finds upcoming slots where price <= peak_shaving_price_limit and
        calculates a charge rate limit that keeps enough free capacity to
        absorb the expected PV surplus during those cheap slots fully.

        Algorithm:
            1. Find upcoming cheap slots (price <= price_limit).
            2. Sum PV surplus during cheap slots → target_reserve_wh.
            3. additional_charging_allowed = free_capacity - target_reserve_wh
            4. If additional_charging_allowed <= 0: block PV charging (return 0).
            5. Spread additional_charging_allowed evenly over slots before
               the cheap window starts.

        Returns:
            int: charge rate limit in W, or -1 if no limit needed.
        """
        price_limit = self.calculation_parameters.peak_shaving_price_limit
        prices = calc_input.prices
        interval_hours = self.interval_minutes / 60.0

        # Find all cheap slots
        cheap_slots = [i for i, p in enumerate(prices)
                       if p is not None and p <= price_limit]
        if not cheap_slots:
            return -1  # No cheap slots ahead

        first_cheap_slot = cheap_slots[0]
        if first_cheap_slot == 0:
            return -1  # Already in cheap slot (caller checks this too)

        # Calculate expected PV surplus during cheap slots
        total_cheap_surplus_wh = 0.0
        for i in cheap_slots:
            if i < len(calc_input.production) and i < len(calc_input.consumption):
                surplus = float(calc_input.production[i]) - float(calc_input.consumption[i])
                if surplus > 0:
                    total_cheap_surplus_wh += surplus * interval_hours

        if total_cheap_surplus_wh <= 0:
            return -1  # No PV surplus expected during cheap slots

        # Reserve capacity (capped at full battery capacity)
        target_reserve_wh = min(total_cheap_surplus_wh, self.common.max_capacity)

        # How much more can we charge before the cheap window starts?
        additional_charging_allowed = calc_input.free_capacity - target_reserve_wh

        if additional_charging_allowed <= 0:
            logger.debug(
                '[PeakShaving] Price-based: battery full relative to cheap-window '
                'reserve (free=%.0f Wh, reserve=%.0f Wh), blocking PV charge',
                calc_input.free_capacity, target_reserve_wh)
            return 0

        # Spread allowed charging evenly over slots before cheap window
        wh_per_slot = additional_charging_allowed / first_cheap_slot
        charge_rate_w = wh_per_slot / interval_hours  # Convert Wh/slot → W

        logger.debug(
            '[PeakShaving] Price-based: cheap window at slot %d, '
            'reserve=%.0f Wh, allowed=%.0f Wh → limit=%d W',
            first_cheap_slot, target_reserve_wh, additional_charging_allowed,
            int(charge_rate_w))

        return int(charge_rate_w)

    def _calculate_peak_shaving_charge_limit(self, calc_input: CalculationInput,
                                             calc_timestamp: datetime.datetime) -> int:
        """Calculate PV charge rate limit to fill battery by target hour.

        Returns:
            int: charge rate limit in W, or -1 if no limit needed.
        """
        slot_start = calc_timestamp.replace(
            minute=(calc_timestamp.minute // self.interval_minutes) * self.interval_minutes,
            second=0, microsecond=0
        )
        target_time = calc_timestamp.replace(
            hour=self.calculation_parameters.peak_shaving_allow_full_after,
            minute=0, second=0, microsecond=0
        )

        if target_time <= slot_start:
            return -1  # Past target hour, no limit

        slots_remaining = int(
            (target_time - slot_start).total_seconds() / (self.interval_minutes * 60)
        )
        slots_remaining = min(slots_remaining, len(calc_input.production))

        if slots_remaining <= 0:
            return -1

        # Calculate PV surplus per slot (only count positive surplus)
        pv_surplus = (calc_input.production[:slots_remaining]
                      - calc_input.consumption[:slots_remaining])
        pv_surplus = np.clip(pv_surplus, 0, None)  # Only positive surplus counts

        # Sum expected PV surplus energy (Wh) over remaining slots
        interval_hours = self.interval_minutes / 60.0
        expected_surplus_wh = float(np.sum(pv_surplus)) * interval_hours

        free_capacity = calc_input.free_capacity

        if expected_surplus_wh <= free_capacity:
            return -1  # PV surplus won't fill battery early, no limit needed

        if free_capacity <= 0:
            return 0  # Battery is full, block PV charging

        # Spread charging evenly across remaining slots
        wh_per_slot = free_capacity / slots_remaining
        charge_rate_w = wh_per_slot / interval_hours  # Convert Wh/slot → W

        return int(charge_rate_w)

    # ------------------------------------------------------------------ #
    #  Discharge evaluation (same as DefaultLogic)                        #
    # ------------------------------------------------------------------ #

    def _is_discharge_allowed(self, calc_input: CalculationInput,
                              net_consumption: np.ndarray,
                              prices: dict,
                              calc_timestamp: Optional[datetime.datetime] = None) -> bool:
        """Evaluate if the battery is allowed to discharge.

        - Check if battery is above always_allow_discharge_limit
        - Calculate required energy to shift toward high price hours
        """
        if calc_timestamp is None:
            calc_timestamp = datetime.datetime.now().astimezone(self.timezone)

        if self.common.is_discharge_always_allowed_capacity(calc_input.stored_energy):
            logger.info(
                "[Rule] Discharge allowed due to always_allow_discharge_limit")
            return True

        current_price = prices[0]

        min_dynamic_price_difference = self._calculate_min_dynamic_price_difference(
            current_price)

        self.calculation_output.min_dynamic_price_difference = min_dynamic_price_difference

        max_slots = len(net_consumption)
        # relevant time range : until next recharge possibility
        for slot in range(1, max_slots):
            future_price = prices[slot]
            if future_price <= current_price - min_dynamic_price_difference:
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

        self.calculation_output.reserved_energy = reserved_storage

        if len(higher_price_slots) > 0:
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
            logger.debug(
                "[Rule] Discharge allowed. Stored usable energy %0.1f Wh >"
                " Reserved energy %0.1f Wh",
                calc_input.stored_usable_energy,
                reserved_storage
            )
            return True

        logger.debug(
            "[Rule] Discharge forbidden. Stored usable energy %0.1f Wh <= Reserved energy %0.1f Wh",
            calc_input.stored_usable_energy,
            reserved_storage
        )

        return False

    # ------------------------------------------------------------------ #
    #  Recharge energy calculation (same as DefaultLogic)                 #
    # ------------------------------------------------------------------ #

    def _get_required_recharge_energy(self, calc_input: CalculationInput,
                                      net_consumption: list,
                                      prices: dict) -> float:
        """Calculate the required energy to shift toward high price slots.

        If a recharge price window is detected, the energy required to
        recharge the battery to the next high price slots is calculated.

        Returns:
            float: Energy in Wh
        """
        current_price = prices[0]
        max_slot = len(net_consumption)
        consumption = np.array(net_consumption)
        consumption[consumption < 0] = 0

        production = -np.array(net_consumption)
        production[production < 0] = 0
        min_price_difference = self.calculation_parameters.min_price_difference
        min_dynamic_price_difference = self._calculate_min_dynamic_price_difference(
            current_price)

        # evaluation period until price is first time lower then current price
        for slot in range(1, max_slot):
            future_price = prices[slot]
            found_lower_price = False
            # Soften the price difference to avoid too early charging
            if self.soften_price_difference_on_charging:
                modified_price = current_price - min_price_difference / \
                    self.soften_price_difference_on_charging_factor
                found_lower_price = future_price <= modified_price
            else:
                found_lower_price = future_price <= current_price

            if found_lower_price:
                max_slot = slot
                break

        logger.debug(
            "[Rule] Evaluation window for recharge energy until slot %d with price %0.3f",
            max_slot - 1,
            prices[max_slot - 1]
        )

        # get high price slots
        high_price_slots = []
        for slot in range(max_slot):
            future_price = prices[slot]
            if future_price > current_price + min_dynamic_price_difference:
                high_price_slots.append(slot)

        # start with nearest slot
        high_price_slots.sort()
        required_energy = 0.0
        for high_price_slot in high_price_slots:
            energy_to_shift = consumption[high_price_slot]

            # correct energy to shift with potential production
            for slot in range(1, high_price_slot):
                if production[slot] == 0:
                    continue
                if production[slot] >= energy_to_shift:
                    production[slot] -= energy_to_shift
                    energy_to_shift = 0
                else:
                    energy_to_shift -= production[slot]
                    production[slot] = 0
            required_energy += energy_to_shift

        if required_energy > 0.0:
            logger.debug("[Rule] Required Energy: %0.1f Wh is based on next 'high price' slots %s",
                         required_energy,
                         high_price_slots)
            recharge_energy = required_energy - calc_input.stored_usable_energy
            logger.debug("[Rule] Stored usable Energy: %0.1f , Recharge Energy: %0.1f Wh",
                         calc_input.stored_usable_energy,
                         recharge_energy)
        else:
            logger.debug(
                "[Rule] No additional energy required, because stored energy is sufficient."
            )
            recharge_energy = 0.0
            self.calculation_output.required_recharge_energy = recharge_energy
            return recharge_energy

        free_capacity = calc_input.free_capacity

        if recharge_energy > free_capacity:
            recharge_energy = free_capacity
            logger.debug(
                "[Rule] Recharge limited by free capacity: %0.1f Wh", recharge_energy)

        if not self.common.is_charging_above_minimum(recharge_energy):
            recharge_energy = 0.0
        else:
            recharge_energy = recharge_energy + self.common.min_charge_energy

        self.calculation_output.required_recharge_energy = recharge_energy
        return recharge_energy

    def _calculate_min_dynamic_price_difference(self, price: float) -> float:
        """ Calculate the dynamic limit for the current price """
        return round(
            max(self.calculation_parameters.min_price_difference,
                self.calculation_parameters.min_price_difference_rel * abs(price)),
            self.round_price_digits
        )
