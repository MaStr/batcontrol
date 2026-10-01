""" Aggregation of several inverters/batteries into one logical inverter.

batcontrol's logic layer works on energy numbers only (capacity, stored
energy, charge rates) and therefore does not need to know how many physical
batteries are behind them. This module provides InverterGroup, which
implements InverterInterface by:

  - summing all energy/capacity values of its members,
  - deriving the group SOC as a capacity-weighted average,
  - splitting a requested charge rate across the members.

Rate distribution ("water filling"):
  A requested rate is split proportional to each member's free capacity (so a
  nearly full battery gets less power than an empty one) and capped by that
  member's own rate limit. Power that does not fit is redistributed over the
  remaining members until nothing can be placed any more.

Minimum charge rate:
  The split never pushes an inverter below its minimum charge rate. The logic
  layer already raises a group charge rate to MIN_CHARGE_RATE because charging
  at a few hundred watts is inefficient; splitting 500 W into 250 W + 250 W
  would throw that guarantee away. So the group first decides how many
  inverters the rate can actually feed - in order of free capacity - gives each
  of them its minimum, and only spreads what is left over proportionally. A
  500 W request therefore goes to one inverter, 1000 W to two, and the
  requested total is always kept exactly.

Idle members during grid charging:
  A member that receives 0 W during a group force-charge is put into
  avoid-discharge mode instead, so a full battery cannot discharge into the
  battery that is being charged from the grid.
"""

import logging

from .inverter_interface import InverterInterface

logger = logging.getLogger(__name__)

# Numeric tolerance for the water-filling loop (in W).
_RATE_EPSILON = 1e-9


def _water_fill(total_rate: float, weights: list, caps: list,
                members: list, count: int) -> list:
    """ Split total_rate over the given members proportional to weights.

    Each member is capped by its entry in caps. Power that does not fit into
    a capped member is redistributed over the remaining members.
    """
    result = [0.0] * count
    remaining = float(total_rate)
    open_members = list(members)

    while remaining > _RATE_EPSILON and open_members:
        weight_sum = sum(weights[index] for index in open_members)
        if weight_sum <= 0:
            break
        placed = 0.0
        capped = []
        for index in open_members:
            share = remaining * weights[index] / weight_sum
            headroom = caps[index] - result[index]
            if share >= headroom:
                share = headroom
                capped.append(index)
            result[index] += share
            placed += share
        remaining -= placed
        if placed <= _RATE_EPSILON:
            break
        for index in capped:
            open_members.remove(index)

    return result


def distribute_rate(total_rate: float, weights: list, caps: list,
                    mins: list = None) -> list:
    """ Split total_rate over the members proportional to weights.

    Each member is capped by its entry in caps. Power that does not fit
    into a capped member is redistributed over the remaining members.

    If mins is given, no member is left with a positive rate below its
    minimum. The rate first buys as many members as can each be served with
    their minimum - taken in order of weight, so the members with the most
    free capacity come first - every chosen member is floored at its minimum,
    and only the remainder is spread proportionally. The members that the
    rate cannot reach get 0 W, and the requested total is always kept.

    Args:
        total_rate: Rate in W to distribute.
        weights: Relative share per member (e.g. free capacity in Wh).
                 A member with weight <= 0 receives nothing.
        caps: Upper rate limit in W per member. A cap <= 0 means
              "no limit" and is replaced by total_rate.
        mins: Optional minimum rate in W per member. None means no minimum.

    Returns:
        list: Allocated rate in W per member.
    """
    count = len(weights)
    if count == 0 or total_rate <= 0:
        return [0.0] * count

    # A cap <= 0 means the member has no own limit.
    effective_caps = [cap if cap > 0 else float(total_rate) for cap in caps]

    # If nobody has a positive weight, fall back to the caps as weights so
    # the requested rate is still placed somewhere.
    if sum(weight for weight in weights if weight > 0) <= 0:
        weights = list(effective_caps)

    candidates = [
        index for index in range(count)
        if weights[index] > 0 and effective_caps[index] > 0
    ]
    if not candidates:
        return [0.0] * count

    if mins is None:
        return _water_fill(
            total_rate, weights, effective_caps, candidates, count)

    # An inverter whose own rate limit is below its minimum cannot charge
    # efficiently at all - only fall back to it if there is no better option.
    usable = [
        index for index in candidates
        if effective_caps[index] >= mins[index]
    ]
    if not usable:
        usable = candidates

    # Most free capacity first: the rate buys these members in this order.
    usable = sorted(usable, key=lambda index: weights[index], reverse=True)

    chosen = []
    required = 0.0
    for index in usable:
        if required + mins[index] > total_rate + _RATE_EPSILON:
            # Not enough left for this member's minimum. The members behind it
            # have even less free capacity, so none of them get a share.
            break
        chosen.append(index)
        required += mins[index]
    if not chosen:
        # The total is below even the best member's minimum. The logic layer
        # already checked the group total, so put everything on that member.
        chosen = [usable[0]]

    # Floor every chosen member at its minimum, then spread what is left
    # proportionally over the same members.
    result = [0.0] * count
    budget = float(total_rate)
    for index in chosen:
        base = min(mins[index], effective_caps[index], budget)
        result[index] = base
        budget -= base

    if budget > _RATE_EPSILON:
        headrooms = [
            effective_caps[index] - result[index] for index in range(count)
        ]
        extra = _water_fill(budget, weights, headrooms, chosen, count)
        for index in chosen:
            result[index] += extra[index]

    return result


def to_watts(rate: float, cap: float) -> int:
    """ Round an allocated rate to whole watts without exceeding the cap.

    Truncating would silently drop up to 1 W per inverter, so a group total of
    1000 W could leave as 999 W. A cap <= 0 means the member has no own limit.
    """
    watts = int(round(rate))
    if cap > 0:
        watts = min(watts, int(cap))
    return max(0, watts)


def inverter_members(inverter) -> list:
    """ Return the individual inverters behind a possibly grouped inverter. """
    if isinstance(inverter, InverterGroup):
        return list(inverter.inverters)
    return [inverter]


class InverterGroup(InverterInterface):
    """ Presents several inverters as a single logical inverter. """

    def __init__(self, inverters: list):
        if not inverters:
            raise ValueError('InverterGroup needs at least one inverter')
        self._inverters = list(inverters)
        logger.info('Inverter group created with %d inverters',
                    len(self._inverters))

    @property
    def inverters(self) -> list:
        """ The member inverters of this group. """
        return list(self._inverters)

    def __len__(self):
        return len(self._inverters)

    # ----------------------------------------------------------------- reads

    def _sum(self, getter_name: str) -> float:
        return float(sum(
            getattr(inverter, getter_name)() for inverter in self._inverters
        ))

    def get_capacity(self) -> float:
        """ Total installed capacity of all members in Wh. """
        return self._sum('get_capacity')

    def get_max_capacity(self) -> float:
        """ Total capacity of all members reduced by their MAX_SOC, in Wh. """
        return self._sum('get_max_capacity')

    def get_free_capacity(self) -> float:
        """ Total chargeable capacity of all members in Wh. """
        return self._sum('get_free_capacity')

    def get_stored_energy(self) -> float:
        """ Total stored energy of all members in Wh. """
        return self._sum('get_stored_energy')

    def get_stored_usable_energy(self) -> float:
        """ Total stored energy above MIN_SOC of all members in Wh. """
        return self._sum('get_stored_usable_energy')

    def get_usable_capacity(self) -> float:
        """ Total capacity between MIN_SOC and MAX_SOC of all members in Wh. """
        return float(sum(
            inverter.get_usable_capacity() for inverter in self._inverters
        ))

    def get_designed_capacity(self) -> float:
        """ Total designed capacity of all members in Wh. """
        return float(sum(
            inverter.get_designed_capacity() for inverter in self._inverters
        ))

    def get_SOC(self) -> float:  # pylint: disable=invalid-name
        """ Capacity-weighted SOC of the group in percent. """
        capacity = self.get_capacity()
        if capacity <= 0:
            return 0.0
        return self.get_stored_energy() / capacity * 100

    def _weighted_soc_limit(self, attribute: str) -> float:
        """ Capacity-weighted average of a member SOC limit, in percent. """
        capacity = 0.0
        weighted = 0.0
        for inverter in self._inverters:
            member_capacity = inverter.get_capacity()
            capacity += member_capacity
            weighted += getattr(inverter, attribute) * member_capacity
        if capacity <= 0:
            return 0.0
        return weighted / capacity

    @property
    def min_soc(self) -> float:
        """ Capacity-weighted minimum SOC of the group in percent. """
        return self._weighted_soc_limit('min_soc')

    @property
    def max_soc(self) -> float:
        """ Capacity-weighted maximum SOC of the group in percent. """
        return self._weighted_soc_limit('max_soc')

    @property
    def max_grid_charge_rate(self) -> float:
        """ Sum of the grid charge rate limits of all members in W. """
        return float(sum(
            getattr(inverter, 'max_grid_charge_rate', 0) or 0
            for inverter in self._inverters
        ))

    @property
    def max_pv_charge_rate(self) -> float:
        """ Sum of the PV charge rate limits, or 0 if any member is unlimited.

        A value of 0 means "no limit" for a single inverter, so a single
        unlimited member makes the whole group unlimited.
        """
        rates = [
            getattr(inverter, 'max_pv_charge_rate', 0) or 0
            for inverter in self._inverters
        ]
        if any(rate <= 0 for rate in rates):
            return 0
        return float(sum(rates))

    # -------------------------------------------------------------- commands

    def set_mode_allow_discharge(self):
        """ Allow discharging on all members. """
        for inverter in self._inverters:
            inverter.set_mode_allow_discharge()

    def set_mode_avoid_discharge(self):
        """ Avoid discharging on all members. """
        for inverter in self._inverters:
            inverter.set_mode_avoid_discharge()

    def _member_rates(self, total_rate: float, cap_attribute: str,
                      min_attribute: str) -> list:
        """ Split total_rate over the members, free capacity weighted.

        Args:
            total_rate: Rate in W to distribute over the group.
            cap_attribute: Member attribute holding its upper rate limit.
            min_attribute: Member attribute holding its minimum rate.

        Returns:
            list: Allocated rate in whole W per member.
        """
        weights = [
            max(0.0, inverter.get_free_capacity())
            for inverter in self._inverters
        ]
        caps = [
            getattr(inverter, cap_attribute, 0) or 0
            for inverter in self._inverters
        ]
        mins = [
            getattr(inverter, min_attribute, 0) or 0
            for inverter in self._inverters
        ]
        rates = distribute_rate(total_rate, weights, caps, mins)
        return [to_watts(rate, cap) for rate, cap in zip(rates, caps)]

    def set_mode_force_charge(self, chargerate: float):
        """ Charge the group from grid with a total rate of chargerate W.

        Members that cannot take any power are set to avoid-discharge, so
        they do not feed the members that are charging from the grid.
        """
        rates = self._member_rates(
            chargerate, 'max_grid_charge_rate', 'min_charge_rate')
        for inverter, rate in zip(self._inverters, rates):
            if rate > 0:
                inverter.set_mode_force_charge(rate)
            else:
                logger.debug(
                    'Inverter %s gets no share of the %d W grid charge rate, '
                    'setting avoid discharge instead',
                    getattr(inverter, 'inverter_num', '?'), chargerate)
                inverter.set_mode_avoid_discharge()

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """ Limit PV charging of the group to limit_charge_rate W in total. """
        if limit_charge_rate <= 0:
            # 0 blocks PV charging completely - no need to distribute.
            for inverter in self._inverters:
                inverter.set_mode_limit_battery_charge(0)
            return
        rates = self._member_rates(
            limit_charge_rate, 'max_pv_charge_rate', 'min_pv_charge_rate')
        for inverter, rate in zip(self._inverters, rates):
            inverter.set_mode_limit_battery_charge(rate)

    # --------------------------------------------------------------- plumbing

    def activate_mqtt(self, api_mqtt_api: object):
        """ Activate the MQTT connection on all members. """
        for inverter in self._inverters:
            inverter.activate_mqtt(api_mqtt_api)

    def refresh_api_values(self):
        """ Refresh the API values of all members (best effort). """
        for inverter in self._inverters:
            try:
                inverter.refresh_api_values()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    'refresh_api_values failed for inverter %s: %s',
                    getattr(inverter, 'inverter_num', '?'), exc)

    def shutdown(self):
        """ Shut down all members, even if one of them fails. """
        for inverter in self._inverters:
            try:
                inverter.shutdown()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    'shutdown failed for inverter %s: %s',
                    getattr(inverter, 'inverter_num', '?'), exc)
