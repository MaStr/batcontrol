"""Decision traces produced by DefaultLogic and NextLogic.

The rules shared by both logic types run against both classes; the peak
shaving / solar limit steps only exist in NextLogic.
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

    def test_no_recharge_required_keeps_battery(self, logic_cls):
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
             Reason.NO_RECHARGE_REQUIRED),
        ]
        assert logic.get_decision_trace().decisive_record().decision == \
            Decision.GRID_RECHARGE

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
    """Peak shaving and solar limit steps of NextLogic."""

    @staticmethod
    def _logic(**peak_shaving_kwargs):
        peak_shaving_kwargs.setdefault('enabled', True)
        peak_shaving_kwargs.setdefault('allow_full_battery_after', 14)
        return _logic(NextLogic, always_allow_discharge_limit=0.90,
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

        with caplog.at_level(logging.DEBUG, logger='batcontrol.logic.next'):
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
