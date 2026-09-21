"""Tests for the decision journal and its status change endpoint."""
import json
import logging

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
        assert data['trace']['decided_by']['reason'] == 'GRID_RECHARGE_REQUIRED'


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
    def _journal_with_events():
        journal = DecisionJournal()
        events = []
        journal.add_listener(events.append)
        return journal, events

    def test_small_value_change_is_no_event(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 500)
        journal.commit(_trace(), -1, 'optimizer', 600)
        journal.commit(_trace(), -1, 'optimizer', 400)

        assert [e.kind for e in events] == [KIND_MODE]

    def test_value_change_by_25_percent_is_an_event(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 1000)
        journal.commit(_trace(), -1, 'optimizer', 1250)

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE]
        change = events[-1]
        assert (change.previous_mode, change.mode) == (-1, -1)
        assert (change.previous_value, change.value) == (1000, 1250)

    def test_decrease_by_25_percent_is_an_event(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), 8, 'optimizer', 1000)
        journal.commit(_trace(), 8, 'optimizer', 760)
        journal.commit(_trace(), 8, 'optimizer', 750)

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE]
        assert events[-1].value == 750

    def test_reference_is_the_last_event_not_the_last_cycle(self):
        """Small steps add up: 10 % per cycle must not go unnoticed."""
        journal, events = self._journal_with_events()

        for value in (500, 550, 600, 650):
            journal.commit(_trace(), -1, 'optimizer', value)

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE]
        assert (events[-1].previous_value, events[-1].value) == (500, 650)

    def test_reference_moves_to_the_value_of_the_last_event(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 500)
        journal.commit(_trace(), -1, 'optimizer', 700)   # event, new reference
        journal.commit(_trace(), -1, 'optimizer', 800)   # +14 % of 700

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE]

    def test_change_from_and_to_zero_is_an_event(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), 8, 'optimizer', 0)      # charging blocked
        journal.commit(_trace(), 8, 'optimizer', 0)
        journal.commit(_trace(), 8, 'optimizer', 100)
        journal.commit(_trace(), 8, 'optimizer', 0)

        assert [e.kind for e in events] == [KIND_MODE, KIND_VALUE, KIND_VALUE]
        assert [e.value for e in events] == [0, 100, 0]

    def test_modes_without_value_never_produce_value_events(self):
        journal, events = self._journal_with_events()

        for _ in range(3):
            journal.commit(_trace(), 0, 'optimizer')
            journal.commit(_trace(), 10, 'optimizer')

        assert all(e.kind == KIND_MODE for e in events)
        assert len(events) == 6

    def test_mode_change_resets_the_reference(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 500)
        journal.commit(_trace(), 0, 'optimizer')
        journal.commit(_trace(), -1, 'optimizer', 520)   # mode event
        journal.commit(_trace(), -1, 'optimizer', 600)   # +15 % of 520

        assert [e.kind for e in events] == [KIND_MODE] * 3

    def test_mode_event_has_no_previous_value(self):
        journal, events = self._journal_with_events()

        journal.commit(_trace(), -1, 'optimizer', 500)
        journal.commit(_trace(), 8, 'optimizer', 300)

        assert events[-1].kind == KIND_MODE
        assert events[-1].previous_value is None
        assert events[-1].value == 300

    def test_value_change_is_still_stored_in_the_history(self):
        journal, _events = self._journal_with_events()
        traces = [_trace() for _ in range(3)]

        for trace, value in zip(traces, (500, 510, 520)):
            journal.commit(trace, -1, 'optimizer', value)

        assert journal.history() == traces
        assert journal.last_status_change().trace is traces[0]

    def test_custom_factor(self):
        journal = DecisionJournal(value_change_factor=0.5)
        events = []
        journal.add_listener(events.append)

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
