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
        assert trace.summary().startswith('No decisive step.')

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
    """The mode with its value and reason as one string."""

    @staticmethod
    def _trace(outcome, reason, **inputs):
        trace = DecisionTrace()
        trace.add(DecisionRecord(Decision.GRID_RECHARGE, Outcome.CHARGE,
                                 Reason.GRID_RECHARGE_REQUIRED, decisive=True))
        trace.add(DecisionRecord(Decision.MODE, outcome, reason, inputs))
        return trace

    def test_force_charge_with_rate(self):
        trace = self._trace('force_charge', Reason.GRID_RECHARGE_REQUIRED,
                            charge_rate=1250)

        assert trace.status_text() == \
            'Charge from Grid 1250 W - Grid recharge required'

    def test_limit_mode_with_pv_limit(self):
        trace = self._trace('limit_battery_charge_rate',
                            Reason.PV_CHARGE_LIMITED,
                            limit_battery_charge_rate=300)

        assert trace.status_text() == \
            'Limit Battery Charge 300 W - PV charge limited'

    def test_zero_limit_is_shown(self):
        trace = self._trace('limit_battery_charge_rate',
                            Reason.PV_CHARGE_LIMITED,
                            limit_battery_charge_rate=0)

        assert 'Limit Battery Charge 0 W' in trace.status_text()

    @pytest.mark.parametrize('outcome, label', [
        ('allow_discharging', 'Discharge Allowed'),
        ('avoid_discharging', 'Avoid Discharge'),
    ])
    def test_modes_without_value(self, outcome, label):
        trace = self._trace(outcome, Reason.NO_RECHARGE_REQUIRED)

        assert trace.status_text() == f'{label} - No recharge required'

    @pytest.mark.parametrize('reason, text', [
        (Reason.GRID_RECHARGE_REQUIRED, 'Grid recharge required'),
        (Reason.NO_PV_PRODUCTION, 'No PV production'),
        (Reason.EVCC_EV_EXPECTS_PV_SURPLUS, 'EVCC EV expects PV surplus'),
        (Reason.API_REQUEST, 'API request'),
    ])
    def test_reason_is_readable_and_keeps_acronyms(self, reason, text):
        trace = self._trace('avoid_discharging', reason)

        assert trace.status_text() == f'Avoid Discharge - {text}'

    def test_empty_without_mode_record(self):
        trace = DecisionTrace()
        trace.add(_record(decisive=True))

        assert trace.status_text() == ''

    def test_is_ascii_and_fits_into_a_ha_state(self):
        trace = self._trace('force_charge', 'X' * 400, charge_rate=1)

        text = trace.status_text()

        assert text.isascii()
        assert len(text) <= 255
