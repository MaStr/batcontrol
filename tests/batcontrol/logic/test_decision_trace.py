"""Tests for DecisionRecord / DecisionTrace."""
import datetime
import json
import logging

import numpy as np
import pytest

from batcontrol.logic.decision_trace import (
    Decision, DecisionRecord, DecisionTrace, Outcome, Reason, plain_value,
)


def _record(decisive=False, **overrides):
    values = {
        'decision': Decision.GRID_RECHARGE,
        'outcome': Outcome.CHARGE,
        'reason': Reason.GRID_RECHARGE_REQUIRED,
        'inputs': {'current_price': 0.2, 'stored_usable_energy': 1234.56,
                   'remaining_time': 0.5, 'charge_rate': 800},
        'decisive': decisive,
    }
    values.update(overrides)
    return DecisionRecord(**values)


class TestDecisionRecord:
    """Rendering and serialization of a single step."""

    def test_summary_keeps_grid_recharge_line_format(self):
        summary = _record().summary()

        assert summary.startswith('[Rule] Grid recharge decision: charge '
                                  '(GRID_RECHARGE_REQUIRED), ')
        assert 'current_price=0.200' in summary
        assert 'stored_usable_energy=1234.6 Wh' in summary
        assert 'remaining_time=0.50 h' in summary
        assert 'charge_rate=800 W' in summary

    def test_summary_uses_rule_specific_prefix(self):
        summary = _record(decision=Decision.PEAK_SHAVING,
                          outcome=Outcome.SKIPPED,
                          reason=Reason.NO_PV_PRODUCTION,
                          inputs={}).summary()

        assert summary == ('[PeakShaving] Peak shaving decision: skipped '
                           '(NO_PV_PRODUCTION)')

    def test_summary_renders_missing_values_and_unknown_inputs(self):
        summary = _record(inputs={'cheaper_price_slot': None,
                                  'time_active': True}).summary()

        assert 'cheaper_price_slot=n/a' in summary
        assert 'time_active=True' in summary

    def test_summary_is_ascii(self):
        assert _record().summary().isascii()

    def test_to_dict_is_json_serializable_with_numpy_values(self):
        record = _record(inputs={'stored_energy': np.float64(2000.0),
                                 'slots': np.int64(3)})

        data = record.to_dict()

        assert json.loads(json.dumps(data)) == {
            'decision': 'grid_recharge',
            'outcome': 'charge',
            'reason': 'GRID_RECHARGE_REQUIRED',
            'why': 'Grid recharge required',
            'decisive': False,
            'inputs': {'stored_energy': 2000.0, 'slots': 3},
        }


class TestPlainValue:
    """numpy scalars must not reach json.dumps."""

    @pytest.mark.parametrize('value, expected', [
        (np.int64(3), 3),
        (np.float32(0.5), 0.5),
        (np.bool_(True), True),
        (np.float64(2.5), 2.5),
        (7, 7),
        ('text', 'text'),
        (None, None),
    ])
    def test_converts_numpy_scalars(self, value, expected):
        result = plain_value(value)

        assert result == expected
        assert not isinstance(result, np.generic)
        assert json.dumps(result)


class TestDecisionTrace:
    """Collecting steps of one cycle."""

    def test_decisive_record_is_the_last_decisive_step(self):
        trace = DecisionTrace()
        trace.add(_record(decision=Decision.DISCHARGE, outcome=Outcome.ALLOWED,
                          reason=Reason.USABLE_ENERGY_EXCEEDS_RESERVE,
                          decisive=True))
        trace.add(_record(decision=Decision.PEAK_SHAVING,
                          outcome=Outcome.NOT_NEEDED,
                          reason=Reason.NO_LIMIT_NEEDED))
        limit = trace.add(_record(decision=Decision.PEAK_SHAVING,
                                  outcome=Outcome.LIMIT_SET,
                                  reason=Reason.PV_CHARGE_LIMITED,
                                  decisive=True))
        trace.add(_record(decision=Decision.SOLAR_LIMIT,
                          outcome=Outcome.NOT_NEEDED,
                          reason=Reason.NO_CLIP_PREDICTED))

        assert trace.decisive_record() is limit

    def test_decisive_record_is_none_without_decisive_step(self):
        trace = DecisionTrace()
        trace.add(_record())

        assert trace.decisive_record() is None

    def test_add_logs_decisive_at_info_and_others_at_debug(self, caplog):
        logger = logging.getLogger('test.decision_trace')
        trace = DecisionTrace()

        with caplog.at_level(logging.DEBUG, logger='test.decision_trace'):
            trace.add(_record(decisive=True), logger)
            trace.add(_record(outcome=Outcome.SKIPPED), logger)

        levels = [r.levelno for r in caplog.records]
        assert levels == [logging.INFO, logging.DEBUG]

    def test_add_without_logger_does_not_log(self, caplog):
        with caplog.at_level(logging.DEBUG):
            DecisionTrace().add(_record(decisive=True))

        assert caplog.records == []

    def test_step_appends_a_record_built_from_its_parts(self):
        trace = DecisionTrace()

        record = trace.step(Decision.OVERRIDE, Outcome.APPLIED,
                            Reason.API_REQUEST, decisive=True,
                            requested_mode=8)

        assert trace.records == [record]
        assert record == DecisionRecord(
            Decision.OVERRIDE, Outcome.APPLIED, Reason.API_REQUEST,
            {'requested_mode': 8}, True)

    def test_step_is_not_decisive_by_default(self):
        trace = DecisionTrace()

        assert not trace.step(Decision.PEAK_SHAVING, Outcome.SKIPPED,
                              Reason.NO_PV_PRODUCTION).decisive

    def test_extend_appends_records_in_order(self):
        first, second = _record(), _record(outcome=Outcome.NO_CHARGE)
        trace = DecisionTrace()
        trace.add(first)
        other = DecisionTrace()
        other.add(second)

        trace.extend(other)

        assert trace.records == [first, second]

    def test_to_dict_contains_timestamp_and_decided_by(self):
        timestamp = datetime.datetime(2025, 6, 20, 12, 30,
                                      tzinfo=datetime.timezone.utc)
        trace = DecisionTrace(timestamp=timestamp)
        trace.add(_record(decisive=True))

        data = json.loads(json.dumps(trace.to_dict()))

        assert data['timestamp'] == '2025-06-20T12:30:00+00:00'
        assert data['decided_by'] == {
            'decision': 'grid_recharge',
            'outcome': 'charge',
            'reason': 'GRID_RECHARGE_REQUIRED',
        }
        assert len(data['records']) == 1


class TestStatusText:
    """The mode with its value and the decisive step's explanation as one
    string. The explanation comes from the decisive record (which carries
    the numbers), not from the mode record itself (see TestExplanation for
    the explanation() mechanism in isolation)."""

    @staticmethod
    def _trace(outcome, reason, decisive_inputs=None, **mode_inputs):
        trace = DecisionTrace()
        trace.add(DecisionRecord(Decision.GRID_RECHARGE, Outcome.CHARGE,
                                 reason, decisive_inputs or {}, decisive=True))
        trace.add(DecisionRecord(Decision.MODE, outcome, reason, mode_inputs))
        return trace

    def test_force_charge_with_rate(self):
        trace = self._trace('force_charge', Reason.GRID_RECHARGE_REQUIRED,
                            value=1250)

        assert trace.status_text() == \
            'Charge from Grid 1250 W - Grid recharge required'

    def test_explains_the_numbers_behind_a_grid_recharge(self):
        trace = self._trace(
            'force_charge', Reason.GRID_RECHARGE_REQUIRED,
            decisive_inputs={'stored_usable_energy': 900.0,
                             'reserved_energy': 2500.0,
                             'recharge_energy': 1600.0},
            value=2133)

        assert trace.status_text() == (
            'Charge from Grid 2133 W - usable energy (900 Wh) is below the '
            '2500 Wh reserved for upcoming expensive hours, so 1600 Wh is '
            'charged from the grid')

    def test_limit_mode_with_pv_limit(self):
        trace = self._trace('limit_battery_charge_rate',
                            Reason.PV_CHARGE_LIMITED,
                            value=300)

        assert trace.status_text() == \
            'Limit Battery Charge 300 W - PV charge limited'

    def test_zero_limit_is_shown(self):
        trace = self._trace('limit_battery_charge_rate',
                            Reason.PV_CHARGE_LIMITED,
                            value=0)

        assert 'Limit Battery Charge 0 W' in trace.status_text()

    @pytest.mark.parametrize('outcome, label', [
        ('allow_discharging', 'Discharge Allowed'),
        ('avoid_discharging', 'Avoid Discharge'),
    ])
    def test_static_explanation_replaces_the_raw_reason_code(
            self, outcome, label):
        trace = self._trace(outcome, Reason.NO_RECHARGE_REQUIRED)

        assert trace.status_text() == (
            f'{label} - stored energy is sufficient until prices rise '
            'again, so no grid charging is needed')

    def test_falls_back_to_the_reason_code_when_inputs_are_missing(self):
        """A data-driven explanation whose inputs are not on the decisive
        record must degrade to the readable reason code, not raise."""
        trace = self._trace('force_charge', Reason.GRID_RECHARGE_REQUIRED,
                            value=1)

        assert trace.status_text() == 'Charge from Grid 1 W - Grid recharge required'

    def test_static_explanation_ignores_extra_inputs(self):
        """API_REQUEST has no placeholders; a record with extra inputs
        (e.g. requested_mode, present only when a mode request fell back
        to a different one) must still render the plain static text."""
        trace = self._trace('avoid_discharging', Reason.API_REQUEST,
                            decisive_inputs={'requested_mode': 8})

        assert trace.status_text() == (
            'Avoid Discharge - requested via the API or Home Assistant')

    def test_empty_without_mode_record(self):
        trace = DecisionTrace()
        trace.add(_record(decisive=True))

        assert trace.status_text() == ''

    def test_is_ascii(self):
        trace = self._trace('force_charge', Reason.GRID_RECHARGE_REQUIRED,
                            value=1)

        assert trace.status_text().isascii()

    def test_all_reason_codes_render_within_the_ha_state_limit(self):
        """Home Assistant limits the state to 255 characters."""
        reasons = [value for name, value in vars(Reason).items()
                   if name.isupper()]

        for reason in reasons:
            trace = self._trace('limit_battery_charge_rate', reason,
                                value=123456)
            assert len(trace.status_text()) <= 255


class TestExplanation:
    """DecisionRecord.explanation(): the plain language "why" behind a
    reason code, used by status_text() and exposed as to_dict()['why']."""

    def test_data_driven_explanation_uses_the_record_s_inputs(self):
        record = _record(
            reason=Reason.RESERVE_REQUIRED,
            inputs={'stored_usable_energy': 900.0, 'reserved_energy': 2500.0})

        assert record.explanation() == (
            'usable energy (900 Wh) is below the 2500 Wh reserved for '
            'upcoming expensive hours')

    def test_format_spec_turns_a_ratio_into_a_percentage(self):
        record = _record(
            reason=Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT,
            inputs={'stored_energy': 8200.0, 'always_allow_discharge_limit': 0.8})

        assert record.explanation() == (
            'stored energy (8200 Wh) is above the always-allow-discharge '
            'level (80% of capacity)')

    def test_static_explanation_ignores_inputs(self):
        record = _record(reason=Reason.NO_PV_PRODUCTION, inputs={})

        assert record.explanation() == 'there is currently no solar production'

    def test_falls_back_to_reason_code_on_missing_input(self, caplog):
        """A record built before an explanation existed for its reason, or
        one missing an expected key, must not raise -- only degrade."""
        record = _record(reason=Reason.GRID_RECHARGE_REQUIRED, inputs={})

        with caplog.at_level(logging.DEBUG):
            explanation = record.explanation()

        assert explanation == 'Grid recharge required'
        assert 'No explanation' in caplog.text

    def test_unknown_reason_falls_back_to_reason_code(self):
        record = _record(reason='SOME_FUTURE_REASON', inputs={})

        assert record.explanation() == 'Some future reason'

    def test_is_included_in_to_dict(self):
        record = _record(reason=Reason.NO_PV_PRODUCTION, inputs={})

        assert record.to_dict()['why'] == 'there is currently no solar production'

    def test_trace_to_dict_exposes_the_decisive_step_s_why(self):
        trace = DecisionTrace()
        trace.add(_record(reason=Reason.NO_PV_PRODUCTION, inputs={},
                          decisive=True))

        assert trace.to_dict()['why'] == 'there is currently no solar production'

    def test_trace_to_dict_why_is_none_without_a_decisive_step(self):
        trace = DecisionTrace()
        trace.add(_record(decisive=False))

        assert trace.to_dict()['why'] is None
