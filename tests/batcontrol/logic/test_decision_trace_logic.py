"""Decision traces produced by DefaultLogic and NextLogic.

The rules shared by both logic types run against both classes.  The peak
shaving / solar limit steps live in DefaultLogic as well, so they are
exercised against DefaultLogic; NextLogic is only a subclass alias kept
for backwards compatibility with ``type: next`` configs.
"""
import datetime
import logging

import numpy as np
import pytest

from batcontrol.logic.decision_trace import (
    Decision, DecisionTrace, Outcome, Reason,
)
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.next import NextLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    InverterControlSettings,
    PeakShavingConfig,
)
from .helpers import make_logic

CAPACITY = 10000
NOON = datetime.datetime(2025, 6, 20, 12, 30, 0, tzinfo=datetime.timezone.utc)
MORNING = datetime.datetime(2025, 6, 20, 8, 0, 0, tzinfo=datetime.timezone.utc)


def _logic(logic_cls, **kwargs):
    defaults = {
        'capacity_wh': CAPACITY,
        'max_charging_from_grid_limit': 0.79,
        'always_allow_discharge_limit': 0.80,
        'min_grid_charge_soc': None,
        'preserve_min_grid_charge_soc': False,
        'min_price_difference': 0.05,
        'min_price_difference_rel': 0.2,
    }
    defaults.update(kwargs)
    return make_logic(logic_cls, **defaults)


def _input(stored_energy, consumption, prices, production=None):
    production = production if production is not None else [0] * len(consumption)
    return CalculationInput(
        consumption=np.array(consumption, dtype=float),
        production=np.array(production, dtype=float),
        prices=prices,
        stored_energy=stored_energy,
        stored_usable_energy=stored_energy - CAPACITY * 0.05,
        free_capacity=CAPACITY - stored_energy,
    )


def _steps(logic):
    return [(r.decision, r.outcome, r.reason)
            for r in logic.get_decision_trace().records]


@pytest.mark.parametrize('logic_cls', [DefaultLogic, NextLogic])
class TestSharedDecisionSteps:
    """Discharge and grid recharge steps, identical in both logic types."""

    def test_grid_recharge_is_traced_with_inputs(self, logic_cls):
        logic = _logic(logic_cls)
        calc_input = _input(2000, [1000, 2000, 1500], {0: 0.20, 1: 0.35, 2: 0.30})

        assert logic.calculate(calc_input, NOON)

        settings = logic.get_inverter_control_settings()
        trace = logic.get_decision_trace()
        assert settings.charge_from_grid
        assert _steps(logic) == [
            (Decision.DISCHARGE, Outcome.FORBIDDEN, Reason.RESERVE_REQUIRED),
            (Decision.GRID_RECHARGE, Outcome.CHARGE, Reason.GRID_RECHARGE_REQUIRED),
        ]
        discharge, recharge = trace.records
        assert not discharge.decisive
        assert recharge.decisive
        assert trace.decisive_record() is recharge
        assert trace.timestamp == NOON
        assert recharge.inputs['current_price'] == 0.20
        assert recharge.inputs['stored_energy'] == 2000
        assert recharge.inputs['charge_rate'] == settings.charge_rate
        assert discharge.inputs['reserved_energy'] > discharge.inputs['stored_usable_energy']
        # high_price_slots/recharge_window_end are relative slot indices, not
        # clock times -- ported from the removed "[Rule] Required Energy ..."/
        # "[Rule] Evaluation window ..." debug lines, see _get_required_recharge_energy.
        assert recharge.inputs['high_price_slots'] == [1, 2]
        assert recharge.inputs['high_price_energy_demand'] == 3500
        assert recharge.inputs['recharge_window_end'] == 3
        assert recharge.inputs['interval_minutes'] == 60

    def test_grid_recharge_is_logged_at_info(self, logic_cls, caplog):
        logic = _logic(logic_cls)
        calc_input = _input(2000, [1000, 2000, 1500], {0: 0.20, 1: 0.35, 2: 0.30})

        with caplog.at_level(logging.INFO):
            logic.calculate(calc_input, NOON)

        lines = [r.getMessage() for r in caplog.records
                 if r.levelno == logging.INFO
                 and '[Rule] Grid recharge decision:' in r.getMessage()]
        assert len(lines) == 1
        assert 'GRID_RECHARGE_REQUIRED' in lines[0]

    def test_always_allow_discharge_limit(self, logic_cls):
        logic = _logic(logic_cls)
        calc_input = _input(9500, [500, 600, 700], {0: 0.20, 1: 0.35, 2: 0.30})

        logic.calculate(calc_input, NOON)

        assert logic.get_inverter_control_settings().allow_discharge
        assert _steps(logic)[:1] == [
            (Decision.DISCHARGE, Outcome.ALLOWED,
             Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT)]
        assert logic.get_decision_trace().decisive_record().inputs[
            'always_allow_discharge_limit'] == 0.80

    def test_discharge_allowed_when_no_reserve_needed(self, logic_cls):
        logic = _logic(logic_cls)
        calc_input = _input(5000, [500, 500, 500], {0: 0.20, 1: 0.20, 2: 0.20})

        logic.calculate(calc_input, NOON)

        assert logic.get_inverter_control_settings().allow_discharge
        record = logic.get_decision_trace().decisive_record()
        assert (record.decision, record.outcome, record.reason) == (
            Decision.DISCHARGE, Outcome.ALLOWED,
            Reason.USABLE_ENERGY_EXCEEDS_RESERVE)
        assert record.inputs['reserved_energy'] == 0

    def test_cheaper_price_slot_is_recorded(self, logic_cls):
        logic = _logic(logic_cls)
        calc_input = _input(5000, [500, 500, 500], {0: 0.30, 1: 0.10, 2: 0.30})

        logic.calculate(calc_input, NOON)

        discharge = logic.get_decision_trace().records[0]
        assert discharge.inputs['cheaper_price_slot'] == 1
        assert discharge.inputs['evaluation_slots'] == 1
        # cheaper_price is the price found at cheaper_price_slot, ported
        # from the removed "[Rule] Future price: ..." debug line.
        assert discharge.inputs['cheaper_price'] == 0.10
        assert discharge.inputs['interval_minutes'] == 60

    def test_no_high_price_slots_keeps_battery(self, logic_cls):
        logic = _logic(logic_cls)
        # almost empty battery, flat prices: nothing to shift, nothing to save
        calc_input = _input(400, [500, 500, 500], {0: 0.20, 1: 0.20, 2: 0.20})

        logic.calculate(calc_input, NOON)

        settings = logic.get_inverter_control_settings()
        assert not settings.allow_discharge
        assert not settings.charge_from_grid
        assert _steps(logic) == [
            (Decision.DISCHARGE, Outcome.FORBIDDEN, Reason.RESERVE_REQUIRED),
            (Decision.GRID_RECHARGE, Outcome.NO_CHARGE,
             Reason.NO_HIGH_PRICE_SLOTS),
        ]
        trace = logic.get_decision_trace()
        assert trace.decisive_record().decision == Decision.GRID_RECHARGE
        recharge = trace.records[1]
        assert recharge.inputs['high_price_slots'] == []
        assert recharge.inputs['high_price_energy_demand'] == 0
        assert recharge.inputs['recharge_energy_before_minimum'] == 0

    def test_high_price_demand_covered_by_production_keeps_battery(
            self, logic_cls):
        """A high-price slot exists, but forecast PV production (an earlier
        slot's surplus) is expected to cover its demand -- told apart from
        "no high-price slots at all" (see test above)."""
        logic = _logic(logic_cls)
        # slot1 (price 0.21) and slot3 (price 0.22) sit between current_price
        # and the stricter grid-recharge threshold (0.25): the discharge rule
        # reserves against them too (forcing "forbidden"), but
        # _get_required_recharge_energy's own high_price_slots is only [2]
        # (price 0.50). Slot1's 1200 Wh surplus production fully covers
        # slot2's 1000 Wh demand there.
        calc_input = _input(1800, [500, 0, 1000, 1600],
                            {0: 0.20, 1: 0.21, 2: 0.50, 3: 0.22},
                            production=[0, 1200, 0, 0])

        logic.calculate(calc_input, NOON)

        assert not logic.get_inverter_control_settings().charge_from_grid
        recharge = logic.get_decision_trace().decisive_record()
        assert recharge.decision == Decision.GRID_RECHARGE
        assert recharge.reason == Reason.HIGH_PRICE_DEMAND_COVERED_BY_PRODUCTION
        assert recharge.inputs['high_price_slots'] == [2]
        assert recharge.inputs['high_price_energy_demand'] == 0
        assert recharge.explanation() == (
            'battery is held for upcoming more expensive hours (usable '
            '1300 Wh, reserve 1400 Wh); no grid charging: solar is forecast '
            'to cover the demand of the hours where it would pay off')

    def test_no_recharge_required_keeps_battery_when_demand_is_stored(
            self, logic_cls):
        """Demand remains after production, but stored usable energy
        already covers it -- the precise meaning of NO_RECHARGE_REQUIRED,
        as opposed to the two cases above where there was no net demand
        to begin with."""
        logic = _logic(logic_cls)
        # slot2 (price 0.22) widens the discharge rule's reserve (forcing
        # "forbidden") without being a high-price slot for
        # _get_required_recharge_energy's stricter threshold (0.25); its
        # own high_price_slots stays [1], with 1600 Wh demand, fully
        # covered by the 2000 Wh stored_usable_energy.
        calc_input = _input(2500, [500, 1600, 900],
                            {0: 0.20, 1: 0.35, 2: 0.22})

        logic.calculate(calc_input, NOON)

        assert not logic.get_inverter_control_settings().charge_from_grid
        recharge = logic.get_decision_trace().decisive_record()
        assert recharge.decision == Decision.GRID_RECHARGE
        assert recharge.reason == Reason.NO_RECHARGE_REQUIRED
        assert recharge.inputs['high_price_slots'] == [1]
        assert recharge.inputs['high_price_energy_demand'] == 1600
        assert recharge.inputs['recharge_energy_before_minimum'] < 0

    def test_recharge_below_minimum_keeps_battery(self, logic_cls):
        """A real but small recharge need (below min_charge_energy) must be
        told apart from "nothing needed at all" (see test above)."""
        logic = _logic(logic_cls)
        # one high-price slot needs 1550 Wh; stored usable energy (1500 Wh)
        # covers all but 50 Wh of it -- below the 100 Wh minimum charge
        # amount (see helpers.make_logic's default min_charge_energy).
        calc_input = _input(2000, [500, 1550], {0: 0.20, 1: 0.35})

        logic.calculate(calc_input, NOON)

        settings = logic.get_inverter_control_settings()
        assert not settings.charge_from_grid
        recharge = logic.get_decision_trace().decisive_record()
        assert recharge.decision == Decision.GRID_RECHARGE
        assert recharge.reason == Reason.RECHARGE_BELOW_MINIMUM
        assert recharge.inputs['high_price_slots'] == [1]
        assert recharge.inputs['high_price_energy_demand'] == 1550
        assert recharge.inputs['recharge_energy_before_minimum'] == 50
        assert recharge.explanation() == (
            'battery is held for upcoming more expensive hours (usable '
            '1500 Wh, reserve 1550 Wh); no grid charging: the missing 50 Wh '
            'are below the minimum charge amount')

    def test_grid_charge_limit_reached(self, logic_cls):
        logic = _logic(logic_cls)
        # 7950 Wh is above the 79 % grid charge limit but below the always
        # allow discharge limit; the expensive slot needs more than is usable
        calc_input = _input(7950, [1000, 9000, 1000], {0: 0.20, 1: 0.50, 2: 0.50})

        logic.calculate(calc_input, NOON)

        assert not logic.get_inverter_control_settings().charge_from_grid
        assert _steps(logic) == [
            (Decision.DISCHARGE, Outcome.FORBIDDEN, Reason.RESERVE_REQUIRED),
            (Decision.GRID_RECHARGE, Outcome.NO_CHARGE,
             Reason.GRID_CHARGE_LIMIT_REACHED),
        ]
        # _get_required_recharge_energy is never called here (SoC already
        # above the grid-charging limit), so these stay at their defaults.
        recharge = logic.get_decision_trace().records[1]
        assert recharge.inputs['high_price_slots'] == []
        assert recharge.inputs['high_price_energy_demand'] == 0
        assert recharge.inputs['recharge_energy_before_minimum'] == 0

    def test_trace_is_reset_on_every_calculation(self, logic_cls):
        logic = _logic(logic_cls)
        calc_input = _input(2000, [1000, 2000, 1500], {0: 0.20, 1: 0.35, 2: 0.30})

        logic.calculate(calc_input, NOON)
        first = logic.get_decision_trace()
        logic.calculate(calc_input, NOON)

        assert logic.get_decision_trace() is not first
        assert len(logic.get_decision_trace().records) == len(first.records)

    def test_trace_is_available_before_first_calculation(self, logic_cls):
        logic = _logic(logic_cls)

        assert isinstance(logic.get_decision_trace(), DecisionTrace)
        assert logic.get_decision_trace().records == []


class TestPeakShavingSteps:
    """Peak shaving and solar limit steps of DefaultLogic."""

    @staticmethod
    def _logic(**peak_shaving_kwargs):
        peak_shaving_kwargs.setdefault('enabled', True)
        peak_shaving_kwargs.setdefault('allow_full_battery_after', 14)
        return _logic(DefaultLogic, always_allow_discharge_limit=0.90,
                      peak_shaving=PeakShavingConfig(**peak_shaving_kwargs))

    @staticmethod
    def _settings(**overrides):
        values = {'allow_discharge': True, 'charge_from_grid': False,
                  'charge_rate': 0, 'limit_battery_charge_rate': -1}
        values.update(overrides)
        return InverterControlSettings(**values)

    @staticmethod
    def _pv_input(stored_energy=7000, production=None):
        production = production if production is not None else [5000] * 8
        return _input(stored_energy, [500] * len(production),
                      np.ones(len(production)) * 10.0, production=production)

    def _apply_peak_shaving(self, logic, settings, calc_input, timestamp=MORNING):
        logic.decision_trace = DecisionTrace()
        logic._apply_peak_shaving(settings, calc_input, timestamp)  # pylint: disable=protected-access
        return logic.get_decision_trace().records

    def test_limit_set_is_decisive(self):
        logic = self._logic()

        records = self._apply_peak_shaving(logic, self._settings(), self._pv_input())

        assert len(records) == 1
        record = records[0]
        assert (record.decision, record.outcome, record.reason) == (
            Decision.PEAK_SHAVING, Outcome.LIMIT_SET, Reason.PV_CHARGE_LIMITED)
        assert record.decisive
        assert record.inputs['final_limit_w'] == 500
        assert record.inputs['price_limit_w'] is None
        assert record.inputs['time_limit_w'] == 142

    @pytest.mark.parametrize('case, expected_reason', [
        ('no_production', Reason.NO_PV_PRODUCTION),
        ('past_hour', Reason.PAST_FULL_BATTERY_HOUR),
        ('always_allow_region', Reason.ALWAYS_ALLOW_DISCHARGE_REGION),
        ('force_charge', Reason.FORCE_CHARGE_ACTIVE),
        ('discharge_not_allowed', Reason.DISCHARGE_NOT_ALLOWED),
    ])
    def test_skip_reasons(self, case, expected_reason):
        logic = self._logic()
        settings = self._settings()
        calc_input = self._pv_input()
        timestamp = MORNING
        if case == 'no_production':
            calc_input = self._pv_input(production=[0] * 8)
        elif case == 'past_hour':
            timestamp = datetime.datetime(2025, 6, 20, 15, 0, 0,
                                          tzinfo=datetime.timezone.utc)
        elif case == 'always_allow_region':
            calc_input = self._pv_input(stored_energy=9500)
        elif case == 'force_charge':
            settings = self._settings(allow_discharge=False,
                                      charge_from_grid=True, charge_rate=3000)
        elif case == 'discharge_not_allowed':
            settings = self._settings(allow_discharge=False)

        records = self._apply_peak_shaving(logic, settings, calc_input, timestamp)

        assert [(r.decision, r.outcome, r.reason) for r in records] == [
            (Decision.PEAK_SHAVING, Outcome.SKIPPED, expected_reason)]
        assert not records[0].decisive

    def test_price_limit_missing_when_price_is_only_component(self):
        logic = self._logic(time_active=False, price_active=True,
                            price_limit=None)

        records = self._apply_peak_shaving(logic, self._settings(), self._pv_input())

        assert [(r.outcome, r.reason) for r in records] == [
            (Outcome.SKIPPED, Reason.PRICE_LIMIT_MISSING)]

    def test_no_limit_needed(self):
        logic = self._logic()
        # plenty of free capacity for the whole PV surplus
        calc_input = self._pv_input(stored_energy=1000,
                                    production=[600, 600])

        records = self._apply_peak_shaving(logic, self._settings(), calc_input)

        assert [(r.outcome, r.reason, r.decisive) for r in records] == [
            (Outcome.NOT_NEEDED, Reason.NO_LIMIT_NEEDED, False)]

    def test_peak_shaving_records_do_not_duplicate_log_lines(self, caplog):
        logic = self._logic()

        with caplog.at_level(logging.DEBUG, logger='batcontrol.logic.default'):
            self._apply_peak_shaving(logic, self._settings(), self._pv_input())

        info_lines = [r.getMessage() for r in caplog.records
                      if r.levelno == logging.INFO]
        assert sum('[PeakShaving]' in m for m in info_lines) == 1

    def test_full_calculation_traces_peak_shaving_after_discharge_rule(self):
        logic = self._logic()
        calc_input = self._pv_input(stored_energy=7000)
        calc_input.prices = {i: 10.0 for i in range(8)}

        logic.calculate(calc_input, MORNING)

        steps = _steps(logic)
        assert steps[0][0] == Decision.DISCHARGE
        assert steps[-1][0] == Decision.PEAK_SHAVING
        trace = logic.get_decision_trace()
        assert trace.decisive_record().decision == Decision.PEAK_SHAVING
        assert logic.get_inverter_control_settings().limit_battery_charge_rate >= 0

    def test_solar_limit_skipped_without_production(self):
        logic = self._logic(solar_cap_active=True, feed_in_limit_w=5000)
        logic.decision_trace = DecisionTrace()

        logic._apply_solar_limit(  # pylint: disable=protected-access
            self._settings(), self._pv_input(production=[0] * 8), MORNING)

        assert _steps(logic) == [
            (Decision.SOLAR_LIMIT, Outcome.SKIPPED, Reason.NO_PV_PRODUCTION)]

    def test_solar_limit_not_traced_when_rule_is_off(self):
        logic = self._logic(solar_cap_active=False)
        logic.decision_trace = DecisionTrace()

        logic._apply_solar_limit(  # pylint: disable=protected-access
            self._settings(), self._pv_input(), MORNING)

        assert _steps(logic) == []

    def test_solar_limit_no_clip_predicted(self):
        logic = self._logic(solar_cap_active=True, feed_in_limit_w=50000)
        logic.decision_trace = DecisionTrace()

        logic._apply_solar_limit(  # pylint: disable=protected-access
            self._settings(), self._pv_input(stored_energy=1000,
                                             production=[600, 600]), MORNING)

        assert _steps(logic) == [
            (Decision.SOLAR_LIMIT, Outcome.NOT_NEEDED, Reason.NO_CLIP_PREDICTED)]

    @pytest.mark.parametrize('previous_limit, expected_decisive', [
        (500, False),    # merge ends up at the limit peak shaving set
        (-1, True),      # solar cap is the only limit
        (5000, True),    # solar cap tightens the earlier limit
    ])
    def test_solar_limit_is_decisive_only_if_it_changed_the_limit(
            self, monkeypatch, previous_limit, expected_decisive):
        logic = self._logic(solar_cap_active=True, feed_in_limit_w=5000)
        logic.decision_trace = DecisionTrace()
        monkeypatch.setattr('batcontrol.logic.default.solar_limit.compute_solar_limit',
                            lambda *args, **kwargs: (0, 3000))
        settings = self._settings(limit_battery_charge_rate=previous_limit)

        logic._apply_solar_limit(  # pylint: disable=protected-access
            settings, self._pv_input(), MORNING)

        record = logic.get_decision_trace().records[-1]
        assert record.decision == Decision.SOLAR_LIMIT
        assert record.outcome == Outcome.LIMIT_SET
        assert record.decisive is expected_decisive
        assert record.inputs['previous_limit_w'] == (
            previous_limit if previous_limit >= 0 else None)
        assert record.inputs['final_limit_w'] == \
            settings.limit_battery_charge_rate
