"""Tests for the decision journal and its status change endpoint."""
import json
import logging

import numpy as np
import pytest

from batcontrol.decision_journal import (
    DecisionJournal, KIND_MODE, KIND_VALUE,
)
from batcontrol.logic.decision_trace import (
    Decision, DecisionRecord, DecisionTrace, Outcome, Reason,
)


def _trace(reason=Reason.GRID_RECHARGE_REQUIRED):
    trace = DecisionTrace()
    trace.add(DecisionRecord(Decision.GRID_RECHARGE, Outcome.CHARGE, reason,
                             decisive=True))
    return trace


class TestStatusChangeListeners:
    """Listeners are called on mode changes, whatever they do."""

    def test_listener_called_on_first_commit_with_previous_none(self):
        journal = DecisionJournal()
        events = []
        journal.add_listener(events.append)
        trace = _trace()

        journal.commit(trace, mode=0, control_source='optimizer')

        assert len(events) == 1
        assert events[0].previous_mode is None
        assert events[0].mode == 0
        assert events[0].control_source == 'optimizer'
        assert events[0].trace is trace

    def test_listener_called_only_when_mode_changes(self):
        journal = DecisionJournal()
        events = []
        journal.add_listener(events.append)

        journal.commit(_trace(), 0, 'optimizer')
        journal.commit(_trace(), 0, 'optimizer')
        journal.commit(_trace(), -1, 'optimizer')
        journal.commit(_trace(), -1, 'optimizer')
        journal.commit(_trace(), 10, 'api')

        assert [(e.previous_mode, e.mode) for e in events] == [
            (None, 0), (0, -1), (-1, 10)]
        assert events[-1].control_source == 'api'

    def test_commit_returns_event_only_on_change(self):
        journal = DecisionJournal()

        assert journal.commit(_trace(), 0, 'optimizer') is not None
        assert journal.commit(_trace(), 0, 'optimizer') is None

    def test_all_listeners_are_called(self):
        journal = DecisionJournal()
        first, second = [], []
        journal.add_listener(first.append)
        journal.add_listener(second.append)

        journal.commit(_trace(), 0, 'optimizer')

        assert len(first) == len(second) == 1

    def test_failing_listener_does_not_break_commit_or_other_listeners(
            self, caplog):
        journal = DecisionJournal()
        received = []

        def failing(_event):
            raise RuntimeError('boom')

        journal.add_listener(failing)
        journal.add_listener(received.append)

        with caplog.at_level(logging.ERROR):
            journal.commit(_trace(), 0, 'optimizer')

        assert len(received) == 1
        assert journal.latest() is not None
        assert 'boom' in caplog.text

    def test_removed_listener_is_not_called(self):
        journal = DecisionJournal()
        events = []
        journal.add_listener(events.append)
        journal.remove_listener(events.append)

        journal.commit(_trace(), 0, 'optimizer')

        assert events == []

    def test_listener_registered_twice_is_called_once(self):
        journal = DecisionJournal()
        events = []
        journal.add_listener(events.append)
        journal.add_listener(events.append)

        journal.commit(_trace(), 0, 'optimizer')

        assert len(events) == 1

    def test_listener_can_query_journal(self):
        """The journal lock is not held while listeners run."""
        journal = DecisionJournal()
        seen = []
        journal.add_listener(lambda _event: seen.append(journal.latest()))
        trace = _trace()

        journal.commit(trace, 0, 'optimizer')

        assert seen == [trace]

    def test_event_to_dict(self):
        journal = DecisionJournal()
        event = journal.commit(_trace(), -1, 'optimizer')

        data = event.to_dict()

        assert data['previous_mode'] is None
        assert data['mode'] == -1
        assert data['decided_by']['reason'] == 'GRID_RECHARGE_REQUIRED'
        assert [r['decision'] for r in data['records']] == ['grid_recharge']


class TestJournalHistory:
    """In-memory ring buffer."""

    def test_empty_journal(self):
        journal = DecisionJournal()

        assert journal.latest() is None
        assert journal.last_status_change() is None
        assert journal.history() == []

    def test_latest_and_last_status_change(self):
        journal = DecisionJournal()
        change = _trace(Reason.GRID_RECHARGE_REQUIRED)
        same_mode = _trace(Reason.NO_RECHARGE_REQUIRED)

        journal.commit(change, 0, 'optimizer')
        journal.commit(same_mode, 0, 'optimizer')

        assert journal.latest() is same_mode
        assert journal.last_status_change().trace is change

    def test_history_is_limited_and_ordered_oldest_first(self):
        journal = DecisionJournal(max_traces=3)
        traces = [_trace() for _ in range(5)]
        for trace in traces:
            journal.commit(trace, 0, 'optimizer')

        assert journal.history() == traces[2:]
        assert journal.history(2) == traces[3:]
        assert journal.history(0) == []


class TestValueChangeEvents:
    """A change of the value belonging to the mode (charge rate, PV limit)
    by 25 % or more is a status change as well."""

    @staticmethod
    def _journal_with_events(**kwargs):
        journal = DecisionJournal(**kwargs)
        events = []
        journal.add_listener(events.append)
        return journal, events

    @pytest.mark.parametrize('commits, kinds', [
        # (mode, value) per cycle -> kinds of the resulting events
        pytest.param([(-1, 500), (-1, 600), (-1, 400)], ['mode'],
                     id='small changes are no event'),
        pytest.param([(-1, 1000), (-1, 1250)], ['mode', 'value'],
                     id='increase by 25 percent'),
        pytest.param([(8, 1000), (8, 760)], ['mode'],
                     id='decrease by 24 percent'),
        pytest.param([(8, 1000), (8, 750)], ['mode', 'value'],
                     id='decrease by 25 percent'),
        pytest.param([(-1, 500), (-1, 550), (-1, 600), (-1, 650)],
                     ['mode', 'value'],
                     id='small steps add up (reference is the last event)'),
        pytest.param([(-1, 500), (-1, 700), (-1, 800)], ['mode', 'value'],
                     id='reference moves to the value of the last event'),
        pytest.param([(8, 0), (8, 0), (8, 100), (8, 0)],
                     ['mode', 'value', 'value'],
                     id='change from and to zero'),
        pytest.param([(0, None), (10, None)] * 3, ['mode'] * 6,
                     id='modes without value only produce mode events'),
        pytest.param([(-1, 500), (0, None), (-1, 520), (-1, 600)],
                     ['mode'] * 3,
                     id='mode change resets the reference'),
    ])
    def test_event_kinds(self, commits, kinds):
        journal, events = self._journal_with_events()

        for mode, value in commits:
            journal.commit(_trace(), mode, 'optimizer', value)

        assert [e.kind for e in events] == kinds

    def test_value_event_carries_previous_value(self):
        journal, events = self._journal_with_events()

        for value in (500, 550, 600, 650):
            journal.commit(_trace(), -1, 'optimizer', value)

        change = events[-1]
        assert (change.previous_mode, change.mode) == (-1, -1)
        assert (change.previous_value, change.value) == (500, 650)

    def test_mode_event_has_no_previous_value(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 500)
        journal.commit(_trace(), 8, 'optimizer', 300)

        assert events[-1].kind == KIND_MODE
        assert events[-1].previous_value is None
        assert events[-1].value == 300

    def test_cycles_without_event_are_stored_in_the_history(self):
        journal, _events = self._journal_with_events()
        traces = [_trace() for _ in range(3)]

        for trace, value in zip(traces, (500, 510, 520)):
            journal.commit(trace, -1, 'optimizer', value)

        assert journal.history() == traces
        assert journal.last_status_change().trace is traces[0]

    def test_custom_factor(self):
        journal, events = self._journal_with_events(value_change_factor=0.5)

        journal.commit(_trace(), -1, 'optimizer', 1000)
        journal.commit(_trace(), -1, 'optimizer', 1400)
        journal.commit(_trace(), -1, 'optimizer', 1500)

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE]

    @pytest.mark.parametrize('factor', [0, -0.25])
    def test_invalid_factor_is_rejected(self, factor):
        with pytest.raises(ValueError):
            DecisionJournal(value_change_factor=factor)

    def test_value_event_to_dict(self):
        journal, events = self._journal_with_events()
        journal.commit(_trace(), -1, 'optimizer', 1000)
        journal.commit(_trace(), -1, 'optimizer', 2000)

        data = json.loads(json.dumps(events[-1].to_dict()))

        assert data['kind'] == 'value'
        assert data['value'] == 2000
        assert data['previous_value'] == 1000
        assert data['mode'] == -1
        assert data['control_source'] == 'optimizer'

    def test_to_dict_converts_numpy_values(self):
        journal, events = self._journal_with_events()
        journal.commit(_trace(), -1, 'optimizer', np.int64(1000))

        data = events[0].to_dict()

        assert type(data['value']) is int  # pylint: disable=unidiomatic-typecheck
        assert json.dumps(data)
