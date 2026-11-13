"""Grid charging is only deferred to later cheap slots that can deliver it.

The recharge evaluation window ends at the first slot that is not more
expensive than the current one: charging for the high price slots behind it
is left to that slot. On a price plateau (static tariff zones) this deferred
everything to the last cheap slot, which cannot charge more than
``max_grid_charge_rate`` allows - with 15-minute slots a fraction of the
demand. With a known maximum grid charge rate the logic charges the part the
later slots cannot deliver before it is too late.
"""
import datetime

import pytest

from batcontrol.logic.decision_trace import Decision, Outcome, Reason
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.next import NextLogic

from .helpers import CAPACITY_WH, make_calc_input, make_logic

LOGIC_CLASSES = [DefaultLogic, NextLogic]

CHEAP = 0.20
EXPENSIVE = 0.40
CONSUMPTION = 500  # Wh per 15-minute slot
MAX_RATE = 5000  # W
CHARGE_RATE_MULTIPLIER = 1.1
# Energy one 15-minute slot can charge at the maximum rate
SLOT_CAPACITY = MAX_RATE / CHARGE_RATE_MULTIPLIER * 0.25
EMPTY_SOC = 10  # equals MIN_SOC: no usable energy
SLOT_START = datetime.datetime(2026, 1, 15, 2, 0, tzinfo=datetime.timezone.utc)

# Eight cheap slots, then eight expensive slots with 4000 Wh demand
PLATEAU = [CHEAP] * 8 + [EXPENSIVE] * 8
PLATEAU_DEMAND = 8 * CONSUMPTION


def _logic(logic_cls, max_grid_charge_rate, **kwargs):
    kwargs.setdefault('min_grid_charge_soc', None)
    kwargs.setdefault('charge_rate_multiplier', CHARGE_RATE_MULTIPLIER)
    return make_logic(
        logic_cls,
        interval_minutes=15,
        preserve_min_grid_charge_soc=False,
        min_price_difference=0.05,
        min_price_difference_rel=0,
        max_grid_charge_rate=max_grid_charge_rate,
        **kwargs)


def _run(logic, prices, soc=EMPTY_SOC, consumption=None):
    slots = len(prices)
    calc_input = make_calc_input(
        production=[0] * slots,
        consumption=consumption or [CONSUMPTION] * slots,
        prices=prices,
        soc=soc)
    assert logic.calculate(calc_input, SLOT_START)
    return logic.get_inverter_control_settings(), logic.get_calculation_output()


def _recharge_record(logic):
    records = [record for record in logic.get_decision_trace().records
               if record.decision == Decision.GRID_RECHARGE]
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestPricePlateau:
    """Equal prices: every following cheap slot ends the evaluation window."""

    def test_early_plateau_slots_keep_waiting(self, logic_cls):
        """Seven following cheap slots can charge far more than the demand."""
        logic = _logic(logic_cls, MAX_RATE)

        settings, output = _run(logic, PLATEAU)

        assert not settings.charge_from_grid
        assert not settings.allow_discharge
        assert output.required_recharge_energy == 0
        assert _recharge_record(logic).reason == Reason.NO_HIGH_PRICE_SLOTS

    def test_charges_when_remaining_cheap_slots_are_too_short(self, logic_cls):
        """Two cheap slots follow: they deliver 2 * SLOT_CAPACITY of the
        4000 Wh, the rest has to be charged in the current slot."""
        logic = _logic(logic_cls, MAX_RATE)

        settings, output = _run(logic, PLATEAU[5:])

        shortfall = PLATEAU_DEMAND - 2 * SLOT_CAPACITY
        assert settings.charge_from_grid
        # + min_recharge_amount, as for every grid recharge
        assert output.required_recharge_energy == pytest.approx(shortfall + 100)

        record = _recharge_record(logic)
        assert record.outcome == Outcome.CHARGE
        assert record.reason == Reason.GRID_RECHARGE_CHEAPER_SLOTS_INSUFFICIENT
        assert record.decisive
        assert record.inputs['deferrable_energy'] == pytest.approx(
            2 * SLOT_CAPACITY)
        assert record.inputs['high_price_energy_demand'] == PLATEAU_DEMAND
        assert record.inputs['max_grid_charge_rate'] == MAX_RATE
        # the window itself still ends at the next cheap slot
        assert record.inputs['recharge_window_end'] == 1
        assert record.inputs['high_price_slots'] == list(range(3, 11))
        explanation = record.explanation()
        assert 'cheaper hours can charge only 2273 Wh of the 4000 Wh' \
            in explanation
        assert '5000 W' in explanation

    @pytest.mark.parametrize('multiplier', [0.8, 1.0])
    def test_multiplier_below_one_does_not_raise_slot_capacity(
            self, logic_cls, multiplier):
        """A charge_rate_multiplier below 1 is valid, but a later slot can
        never charge more than max_grid_charge_rate: its capacity is planned
        as for a multiplier of 1, not as max_rate / 0.8."""
        logic = _logic(logic_cls, MAX_RATE, charge_rate_multiplier=multiplier)

        settings, output = _run(logic, PLATEAU[5:])

        physical_slot_capacity = MAX_RATE * 0.25
        shortfall = PLATEAU_DEMAND - 2 * physical_slot_capacity
        assert settings.charge_from_grid
        assert output.required_recharge_energy == pytest.approx(shortfall + 100)
        assert _recharge_record(logic).inputs['deferrable_energy'] == \
            pytest.approx(2 * physical_slot_capacity)

    def test_last_plateau_slot_is_the_plain_recharge(self, logic_cls):
        """Nothing is deferred any more: same result with and without a
        known maximum rate."""
        results = []
        for rate in (0, MAX_RATE):
            logic = _logic(logic_cls, rate)
            settings, output = _run(logic, PLATEAU[7:])
            record = _recharge_record(logic)
            assert record.reason == Reason.GRID_RECHARGE_REQUIRED
            assert record.inputs['deferrable_energy'] == 0
            results.append((settings.charge_rate,
                            output.required_recharge_energy,
                            record.inputs['high_price_slots']))

        assert results[0] == results[1]
        assert results[0][1] == pytest.approx(PLATEAU_DEMAND + 100)

    @pytest.mark.parametrize('unknown_rate', [0, 0.0, -1])
    def test_unknown_rate_keeps_previous_behaviour(self, logic_cls, unknown_rate):
        """Without a known maximum rate a later cheap slot is assumed to
        deliver everything: nothing is charged before the last cheap slot."""
        logic = _logic(logic_cls, unknown_rate)

        for start in range(7):
            settings, output = _run(logic, PLATEAU[start:])
            assert not settings.charge_from_grid, f'slot {start}'
            assert output.required_recharge_energy == 0
            assert _recharge_record(logic).reason == Reason.NO_HIGH_PRICE_SLOTS

        settings, _ = _run(logic, PLATEAU[7:])
        assert settings.charge_from_grid

    @pytest.mark.parametrize('rate, covered', [(MAX_RATE, True), (0, False)])
    def test_demand_is_covered_before_the_expensive_block(
            self, logic_cls, rate, covered):
        """Walk through the plateau slot by slot, charging what the inverter
        can deliver at its maximum rate. With a known rate the demand of the
        expensive block is in the battery when it starts; without it only
        the last cheap slot charges and most of the demand is missing."""
        logic = _logic(logic_cls, rate)
        stored = CAPACITY_WH * EMPTY_SOC / 100
        charging_slots = []

        for start in range(8):
            settings, output = _run(
                logic, PLATEAU[start:], soc=stored / CAPACITY_WH * 100)
            if settings.charge_from_grid:
                charging_slots.append(start)
                stored += min(output.required_recharge_energy, SLOT_CAPACITY)

        usable = stored - CAPACITY_WH * EMPTY_SOC / 100
        if covered:
            assert usable >= PLATEAU_DEMAND
            # planned backwards: the early slots are not used
            assert charging_slots == [4, 5, 6, 7]
        else:
            assert charging_slots == [7]
            assert usable == pytest.approx(SLOT_CAPACITY)


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestCheaperSlotTooShort:
    """A more expensive slot before a single cheaper one that cannot charge
    the whole demand of the expensive block behind it."""

    # 0.22 now, 0.20 next (cheaper, but by less than min_price_difference,
    # so the battery is still held), then the expensive block
    PRICES = [0.22, CHEAP] + [EXPENSIVE] * 8

    def test_shortfall_is_charged_now(self, logic_cls):
        logic = _logic(logic_cls, MAX_RATE)

        settings, output = _run(logic, self.PRICES)

        shortfall = PLATEAU_DEMAND - SLOT_CAPACITY
        assert settings.charge_from_grid
        assert output.required_recharge_energy == pytest.approx(shortfall + 100)
        record = _recharge_record(logic)
        assert record.reason == Reason.GRID_RECHARGE_CHEAPER_SLOTS_INSUFFICIENT
        assert record.inputs['deferrable_energy'] == pytest.approx(SLOT_CAPACITY)

    def test_unknown_rate_waits_for_the_cheaper_slot(self, logic_cls):
        logic = _logic(logic_cls, 0)

        settings, output = _run(logic, self.PRICES)

        assert not settings.charge_from_grid
        assert output.required_recharge_energy == 0

    def test_no_charge_if_the_cheaper_slot_is_sufficient(self, logic_cls):
        """Demand of 1000 Wh fits into the one cheaper slot."""
        logic = _logic(logic_cls, MAX_RATE)

        settings, output = _run(logic, [0.22, CHEAP] + [EXPENSIVE] * 2)

        assert not settings.charge_from_grid
        assert output.required_recharge_energy == 0

    def test_price_gap_too_small_is_not_charged_for(self, logic_cls):
        """The block behind the cheaper slot is not min_price_difference
        above the current price: nothing to charge for, however short the
        cheaper slot is."""
        logic = _logic(logic_cls, MAX_RATE)

        settings, output = _run(logic, [0.22, CHEAP] + [0.26] * 8)

        assert not settings.charge_from_grid
        assert output.required_recharge_energy == 0

    def test_stored_energy_reduces_the_shortfall(self, logic_cls):
        logic = _logic(logic_cls, MAX_RATE)
        usable = 1500
        soc = EMPTY_SOC + usable / CAPACITY_WH * 100

        _, output = _run(logic, self.PRICES, soc=soc)

        shortfall = PLATEAU_DEMAND - SLOT_CAPACITY - usable
        assert output.required_recharge_energy == pytest.approx(shortfall + 100)


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestWindowDemandUnchanged:
    """High price slots inside the evaluation window: the known maximum
    rate must not change anything."""

    @pytest.mark.parametrize('prices', [
        [CHEAP] + [EXPENSIVE] * 4,
        [CHEAP] + [EXPENSIVE] * 4 + [CHEAP] * 6,
        [CHEAP, 0.22, EXPENSIVE, EXPENSIVE, 0.10, 0.10, 0.10, 0.30],
    ])
    def test_same_result_with_and_without_known_rate(self, logic_cls, prices):
        results = []
        for rate in (0, MAX_RATE):
            logic = _logic(logic_cls, rate)
            settings, output = _run(logic, prices)
            record = _recharge_record(logic)
            results.append((settings.charge_from_grid, settings.charge_rate,
                            output.required_recharge_energy, record.reason,
                            record.inputs['high_price_slots']))

        assert results[0] == results[1]
        assert results[0][0]
        assert results[0][3] == Reason.GRID_RECHARGE_REQUIRED


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestSoftenedWindow:
    """With soften enabled the plateau is one window already; the cheaper
    slots that end the softened window are the ones charging is deferred to."""

    def test_plateau_is_charged_over_the_window_as_before(self, logic_cls):
        results = []
        for rate in (0, MAX_RATE):
            logic = _logic(logic_cls, rate)
            logic.set_soften_price_difference_on_charging(True, 5)
            settings, output = _run(logic, PLATEAU)
            results.append((settings.charge_from_grid, settings.charge_rate,
                            output.required_recharge_energy,
                            _recharge_record(logic).reason))

        assert results[0] == results[1]
        assert results[0][0]
        assert results[0][3] == Reason.GRID_RECHARGE_REQUIRED

    def test_slot_within_soften_margin_is_no_deferral_target(self, logic_cls):
        """0.195 is cheaper, but within the softened margin (0.05 / 5): it
        belongs to the window and adds no deferral capacity."""
        logic = _logic(logic_cls, MAX_RATE)
        logic.set_soften_price_difference_on_charging(True, 5)

        _, output = _run(logic, [CHEAP, 0.195] + [EXPENSIVE] * 8)

        assert output.required_recharge_energy == pytest.approx(
            PLATEAU_DEMAND + 100)
        assert _recharge_record(logic).reason == Reason.GRID_RECHARGE_REQUIRED


@pytest.mark.parametrize('logic_cls', LOGIC_CLASSES)
class TestMinGridChargeSocWithDeferral:
    """The min_grid_charge_soc target is deferred like the demand itself."""

    def test_target_is_reduced_by_what_cheaper_slots_can_charge(self, logic_cls):
        """Target 55 % = 4608 Wh above the empty battery. The two following
        cheap slots can charge 2 * SLOT_CAPACITY of it, the rest is due now
        and exceeds the plain shortfall of the demand."""
        logic = _logic(logic_cls, MAX_RATE, min_grid_charge_soc=0.55)

        _, output = _run(logic, PLATEAU[5:])

        target_energy = CAPACITY_WH * 0.55 - CAPACITY_WH * EMPTY_SOC / 100
        assert target_energy > PLATEAU_DEMAND
        assert output.required_recharge_energy == pytest.approx(
            target_energy - 2 * SLOT_CAPACITY + 100)

    def test_target_applies_in_full_inside_the_window(self, logic_cls):
        logic = _logic(logic_cls, MAX_RATE, min_grid_charge_soc=0.55)

        _, output = _run(logic, PLATEAU[7:])

        target_energy = CAPACITY_WH * 0.55 - CAPACITY_WH * EMPTY_SOC / 100
        assert output.required_recharge_energy == pytest.approx(
            target_energy + 100)
