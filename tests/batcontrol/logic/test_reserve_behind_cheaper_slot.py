"""The discharge reserve looks behind a cheaper slot that is too short.

The reserve window ends at the first slot that is cheaper than the current
one by at least the minimum price difference: the battery can be recharged
there, so nothing behind it has to be kept. With 15-minute slots a single
cheap slot cannot recharge much. The demand of more expensive slots behind
it that the cheaper slots cannot charge at ``max_grid_charge_rate`` is
therefore reserved as well.
"""
import datetime

import numpy as np
import pytest

from batcontrol.logic.decision_trace import Decision, Outcome, Reason
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.next import NextLogic

from .helpers import (
    CAPACITY_WH, MAX_CHARGING_FROM_GRID_LIMIT, MIN_SOC,
    make_calc_input, make_logic)

LOGIC_CLASSES = [DefaultLogic, NextLogic]

NOW = 0.30
DIP = 0.20       # cheaper than NOW by more than the minimum difference
EXPENSIVE = 0.40
CONSUMPTION = 500  # Wh per 15-minute slot
MAX_RATE = 5000  # W
CHARGE_RATE_MULTIPLIER = 1.1
# Energy one 15-minute slot can charge at the maximum rate
SLOT_CAPACITY = MAX_RATE / CHARGE_RATE_MULTIPLIER * 0.25
SLOT_START = datetime.datetime(2026, 1, 15, 2, 0, tzinfo=datetime.timezone.utc)

# Usable energy the battery holds when charged from the grid to the limit
GRID_CHARGEABLE = CAPACITY_WH * MAX_CHARGING_FROM_GRID_LIMIT - CAPACITY_WH * MIN_SOC

# Current slot, one cheap slot, then eight expensive slots (4000 Wh demand)
ONE_DIP = [NOW, DIP] + [EXPENSIVE] * 8
DEMAND_BEHIND = 8 * CONSUMPTION


def _logic(logic_cls, max_grid_charge_rate=MAX_RATE, interval_minutes=15,
           min_grid_charge_soc=None, preserve_min_grid_charge_soc=False):
    return make_logic(
        logic_cls,
        interval_minutes=interval_minutes,
        min_grid_charge_soc=min_grid_charge_soc,
        preserve_min_grid_charge_soc=preserve_min_grid_charge_soc,
        min_price_difference=0.05,
        min_price_difference_rel=0,
        charge_rate_multiplier=CHARGE_RATE_MULTIPLIER,
        max_grid_charge_rate=max_grid_charge_rate)


def _soc_for_usable(usable_wh):
    return (usable_wh / CAPACITY_WH + MIN_SOC) * 100


def _run(logic, prices, usable_wh, consumption=None, production=None):
    slots = len(prices)
    calc_input = make_calc_input(
        production=production or [0] * slots,
        consumption=consumption or [CONSUMPTION] * slots,
        prices=prices,
        soc=_soc_for_usable(usable_wh))
    return _calculate(logic, calc_input)


def _calculate(logic, calc_input):
    assert logic.calculate(calc_input, SLOT_START)
    return logic.get_inverter_control_settings(), logic.get_calculation_output()


def _discharge_record(logic):
    records = [record for record in logic.get_decision_trace().records
               if record.decision == Decision.DISCHARGE]
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestCheaperSlotTooShort:
    """One cheap 15-minute slot in front of an expensive block."""

    def test_uncovered_demand_is_reserved(self, logic_cls):
        """2000 Wh usable is less than what the cheap slot cannot recharge:
        discharging now would be paid for in the expensive block."""
        logic = _logic(logic_cls)

        settings, output = _run(logic, ONE_DIP, usable_wh=2000)

        uncovered = DEMAND_BEHIND - SLOT_CAPACITY
        assert not settings.allow_discharge
        assert output.reserved_energy == pytest.approx(uncovered)

        record = _discharge_record(logic)
        assert record.outcome == Outcome.FORBIDDEN
        assert record.reason == Reason.RESERVE_REQUIRED
        # the window itself still ends at the cheaper slot
        assert record.inputs['evaluation_slots'] == 1
        assert record.inputs['cheaper_price_slot'] == 1
        assert record.inputs['higher_price_slots'] == []
        assert record.inputs['reserve_behind_cheaper_slot'] == \
            pytest.approx(uncovered)
        assert record.inputs['cheaper_slots_charge_capacity'] == \
            pytest.approx(SLOT_CAPACITY)
        assert record.inputs['higher_price_slots_behind'] == list(range(2, 10))

    def test_discharge_allowed_above_the_uncovered_demand(self, logic_cls):
        logic = _logic(logic_cls)

        settings, output = _run(logic, ONE_DIP, usable_wh=3000)

        assert settings.allow_discharge
        assert output.reserved_energy == pytest.approx(
            DEMAND_BEHIND - SLOT_CAPACITY)

    @pytest.mark.parametrize('unknown_rate', [0, -1])
    def test_unknown_rate_keeps_previous_behaviour(self, logic_cls, unknown_rate):
        """Without a known maximum grid charge rate the cheaper slot is
        assumed to recharge any amount, as before."""
        logic = _logic(logic_cls, max_grid_charge_rate=unknown_rate)

        settings, output = _run(logic, ONE_DIP, usable_wh=2000)

        assert settings.allow_discharge
        assert output.reserved_energy == 0
        record = _discharge_record(logic)
        assert record.inputs['reserve_behind_cheaper_slot'] == 0
        assert record.inputs['higher_price_slots_behind'] == []

    def test_shortfall_is_then_charged_from_the_grid(self, logic_cls):
        """Holding the battery leads into the grid recharge rule, which
        charges what the cheap slot cannot deliver (0.30 -> 0.40 pays)."""
        logic = _logic(logic_cls)

        settings, _ = _run(logic, ONE_DIP, usable_wh=500)

        assert not settings.allow_discharge
        assert settings.charge_from_grid


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestCheaperSlotsSufficient:
    """Nothing is reserved if the cheaper slots can recharge the demand."""

    def test_enough_cheap_slots(self, logic_cls):
        logic = _logic(logic_cls)
        prices = [NOW] + [DIP] * 4 + [EXPENSIVE] * 8   # 4 * 1136 Wh > 4000 Wh

        settings, output = _run(logic, prices, usable_wh=500)

        assert settings.allow_discharge
        assert output.reserved_energy == 0

    def test_hourly_slot_is_long_enough(self, logic_cls):
        """The same shape with 60-minute slots: one cheap hour recharges
        4545 Wh, more than the 4000 Wh behind it."""
        logic = _logic(logic_cls, interval_minutes=60)

        settings, output = _run(logic, ONE_DIP, usable_wh=500)

        assert settings.allow_discharge
        assert output.reserved_energy == 0

    def test_second_cheap_slot_covers_later_demand(self, logic_cls):
        """Capacity and demand are compared in time order: a cheap slot
        behind the first expensive slots does not help those."""
        logic = _logic(logic_cls)
        prices = [NOW, DIP] + [EXPENSIVE] * 4 + [DIP] + [EXPENSIVE] * 2

        _, output = _run(logic, prices, usable_wh=500)

        # 2000 Wh behind the first cheap slot, 1136 Wh of it rechargeable;
        # the two last slots (1000 Wh) are covered by the second cheap slot.
        assert output.reserved_energy == pytest.approx(
            4 * CONSUMPTION - SLOT_CAPACITY)


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestWhatCountsBehindTheCheaperSlot:

    def test_slots_not_above_the_current_price_do_not_count(self, logic_cls):
        logic = _logic(logic_cls)
        prices = [NOW, DIP] + [NOW] * 8

        settings, output = _run(logic, prices, usable_wh=500)

        assert settings.allow_discharge
        assert output.reserved_energy == 0

    def test_pv_surplus_reduces_the_demand_behind(self, logic_cls):
        """PV surplus in the cheap slot covers part of the expensive block."""
        logic = _logic(logic_cls)
        production = [0, CONSUMPTION + 1500] + [0] * 8

        _, output = _run(logic, ONE_DIP, usable_wh=500, production=production)

        assert output.reserved_energy == pytest.approx(
            DEMAND_BEHIND - 1500 - SLOT_CAPACITY)

    def test_reserve_inside_the_window_is_kept(self, logic_cls):
        """A more expensive slot before the cheap one is reserved as
        before; the part behind the cheap slot is added on top."""
        logic = _logic(logic_cls)
        prices = [NOW, EXPENSIVE, DIP] + [EXPENSIVE] * 8

        _, output = _run(logic, prices, usable_wh=500)

        assert output.reserved_energy == pytest.approx(
            CONSUMPTION + DEMAND_BEHIND - SLOT_CAPACITY)
        record = _discharge_record(logic)
        assert record.inputs['higher_price_slots'] == [1]
        assert record.inputs['higher_price_slots_behind'] == list(range(3, 11))


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestCheaperSlotsCanFillTheBattery:
    """Twelve cheap slots charge more than the battery holds: it is full
    behind them whatever is kept or charged now. However much demand
    follows, it is no reason to hold energy or to charge at today's price."""

    VALLEY = [NOW] + [DIP] * 12 + [EXPENSIVE] * 40

    def test_nothing_is_reserved(self, logic_cls):
        logic = _logic(logic_cls)

        settings, output = _run(logic, self.VALLEY, usable_wh=3000)

        assert settings.allow_discharge
        assert output.reserved_energy == 0
        assert _discharge_record(logic).inputs[
            'reserve_behind_cheaper_slot'] == 0

    def test_empty_battery_is_not_charged_before_the_valley(self, logic_cls):
        """Regression: the grid recharge rule charged 6.4 kWh at 0.30 right
        before three hours at 0.20."""
        logic = _logic(logic_cls)

        settings, output = _run(logic, self.VALLEY, usable_wh=0)

        assert not settings.charge_from_grid
        assert output.required_recharge_energy == 0

    def test_short_valley_reserves_the_room_left_in_the_battery(self, logic_cls):
        """Two cheap slots cannot fill the battery. The demand behind them
        (20 kWh) is far more than it holds, so the reserve is the room that
        is left once the two slots have charged."""
        logic = _logic(logic_cls)
        prices = [NOW] + [DIP] * 2 + [EXPENSIVE] * 40

        settings, output = _run(logic, prices, usable_wh=3000)

        assert not settings.allow_discharge
        assert output.reserved_energy == pytest.approx(
            GRID_CHARGEABLE - 2 * SLOT_CAPACITY)

    def test_valley_that_almost_fills_the_battery(self, logic_cls):
        """Seven cheap slots charge 7955 Wh of the 8090 Wh the battery
        takes: only the missing 135 Wh are worth keeping, not the whole
        demand behind the valley."""
        logic = _logic(logic_cls)
        prices = [NOW] + [DIP] * 7 + [EXPENSIVE] * 40

        settings, output = _run(logic, prices, usable_wh=3000)

        assert settings.allow_discharge
        assert output.reserved_energy == pytest.approx(
            GRID_CHARGEABLE - 7 * SLOT_CAPACITY)

    def test_demand_inside_the_window_is_still_charged(self, logic_cls):
        """An expensive slot before the valley is charged for now, the
        block behind the filling valley is not."""
        logic = _logic(logic_cls)
        prices = [NOW, EXPENSIVE] + [DIP] * 12 + [EXPENSIVE] * 40

        settings, output = _run(logic, prices, usable_wh=0)

        assert settings.charge_from_grid
        # + min_recharge_amount, as for every grid recharge
        assert output.required_recharge_energy == pytest.approx(
            CONSUMPTION + 100)


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestInputsAndOtherRules:

    def test_prices_as_numpy_array(self, logic_cls):
        """core.py passes arrays, the scenario helpers pass dicts."""
        logic = _logic(logic_cls)
        calc_input = make_calc_input(
            production=[0] * len(ONE_DIP),
            consumption=[CONSUMPTION] * len(ONE_DIP),
            prices=ONE_DIP, soc=_soc_for_usable(2000))
        calc_input.prices = np.array(ONE_DIP, dtype=float)

        settings, output = _calculate(logic, calc_input)

        assert not settings.allow_discharge
        assert output.reserved_energy == pytest.approx(
            DEMAND_BEHIND - SLOT_CAPACITY)

    def test_prices_shorter_than_the_forecast(self, logic_cls):
        """Only slots with a price are evaluated."""
        logic = _logic(logic_cls)
        calc_input = make_calc_input(
            production=[0] * 10, consumption=[CONSUMPTION] * 10,
            prices=[NOW, DIP, EXPENSIVE, EXPENSIVE], soc=_soc_for_usable(500))

        _, output = _calculate(logic, calc_input)

        assert output.reserved_energy == 0   # 1000 Wh < 1136 Wh rechargeable

    def test_no_cheaper_slot_is_the_plain_window_reserve(self, logic_cls):
        logic = _logic(logic_cls)
        prices = [NOW] + [EXPENSIVE] * 4

        _, output = _run(logic, prices, usable_wh=500)

        assert output.reserved_energy == 4 * CONSUMPTION
        record = _discharge_record(logic)
        assert record.inputs['cheaper_price_slot'] is None
        assert record.inputs['reserve_behind_cheaper_slot'] == 0

    def test_reserve_behind_does_not_activate_the_soc_target(self, logic_cls):
        """preserve_min_grid_charge_soc is tied to a reserve inside the
        window. PV covers the expensive slot there, so the small reserve
        behind the cheap slot must not raise the reserve to the SoC target."""
        logic = _logic(logic_cls, min_grid_charge_soc=0.55,
                       preserve_min_grid_charge_soc=True)
        prices = [NOW, EXPENSIVE, DIP] + [0.31] * 3 + [NOW] * 5
        # 500 Wh surplus in the current slot, exactly the 0.40 slot's demand
        production = [2 * CONSUMPTION] + [0] * 10

        settings, output = _run(logic, prices, usable_wh=3000,
                                production=production)

        assert settings.allow_discharge
        assert output.reserved_energy == pytest.approx(
            3 * CONSUMPTION - SLOT_CAPACITY)
