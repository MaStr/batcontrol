"""In-memory journal of control cycle decisions with a status change endpoint.

The journal keeps the most recent :class:`DecisionTrace` objects and notifies
registered listeners whenever the inverter status changes. What a listener does
(push a chat message, publish to MQTT, ...) is up to the listener; the journal
only guarantees that it is called with the full trace of the decision.

The status changes when

- the inverter mode changes (``kind == 'mode'``), or
- the value belonging to the mode (charge rate of force charge, PV limit of
  the limit mode) differs from the value of the last event by at least
  ``value_change_factor`` (``kind == 'value'``).

Listeners that show the explanation of the decision (the MQTT "Decision"
sensor) also need its numbers and reason to stay current while the status
does not change. They register with ``refresh=True`` and additionally get a
``kind == 'refresh'`` event when

- the reason of the decisive step changes, or
- the last event is ``refresh_interval`` (15 minutes) old.

Refresh events do not change the reference of the status change detection.
"""
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from .logic.decision_trace import DecisionTrace, plain_value

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRACES = 200
# 0.25: an event is generated when the value changes by 25 % or more
DEFAULT_VALUE_CHANGE_FACTOR = 0.25

# a listener registered with refresh=True gets a refresh event at the latest
# after this time, so values in the explanation are at most this old
DEFAULT_REFRESH_INTERVAL = 15 * 60  # seconds

KIND_MODE = 'mode'
KIND_VALUE = 'value'
KIND_REFRESH = 'refresh'


@dataclass(frozen=True)
class StatusChangeEvent:  # pylint: disable=too-many-instance-attributes
    """The inverter status changed, or (``kind == 'refresh'``) the current
    status is reported again with the trace of the latest decision.

    ``kind`` is ``'mode'``, ``'value'`` or ``'refresh'``. ``previous_mode``
    is None for the first mode set after start and for refresh events.
    ``value`` is the value belonging to the mode (W), None for modes without
    one. ``previous_value`` is only set for ``kind == 'value'``: the value of
    the last event.
    """
    kind: str
    previous_mode: Optional[int]
    mode: int
    control_source: str
    trace: DecisionTrace
    value: Optional[float] = None
    previous_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON friendly representation: the trace (``timestamp``,
        ``decided_by``, ``records``) plus the event data."""
        return {
            **self.trace.to_dict(),
            'kind': self.kind,
            'previous_mode': self.previous_mode,
            'mode': self.mode,
            'control_source': self.control_source,
            'value': plain_value(self.value),
            'previous_value': plain_value(self.previous_value),
        }


StatusChangeListener = Callable[[StatusChangeEvent], None]


class DecisionJournal:  # pylint: disable=too-many-instance-attributes
    """Thread safe ring buffer of decision traces plus listener registry."""

    def __init__(self, max_traces: int = DEFAULT_MAX_TRACES,
                 value_change_factor: float = DEFAULT_VALUE_CHANGE_FACTOR,
                 refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
                 clock: Callable[[], float] = time.monotonic):
        if value_change_factor <= 0:
            raise ValueError('value_change_factor must be greater than 0, '
                             f'got {value_change_factor}')
        self._value_change_factor = value_change_factor
        self._refresh_interval = refresh_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._traces: Deque[DecisionTrace] = deque(maxlen=max_traces)
        # (listener, wants refresh events)
        self._listeners: List[Tuple[StatusChangeListener, bool]] = []
        # Mode and value of the last event are the reference for the next
        # commit. Values are compared against the last event, not the last
        # cycle, so a slow drift in small steps is detected as well.
        self._last_change: Optional[StatusChangeEvent] = None
        # Last event of any kind (including refresh), with the time it was
        # created: the reference for the next refresh.
        self._last_event: Optional[StatusChangeEvent] = None
        self._last_event_time = 0.0

    def add_listener(self, listener: StatusChangeListener,
                     refresh: bool = False) -> None:
        """Register a callback, called with a :class:`StatusChangeEvent` on
        every status change. With ``refresh=True`` it is also called with
        the refresh events (see module docstring).

        Listeners run synchronously in the thread that changed the mode
        (scheduler or API thread). They must return quickly; hand slow work
        (network calls) over to a queue or thread. Exceptions are logged and
        do not affect the control loop. Registering a listener again
        updates its ``refresh`` setting.
        """
        with self._lock:
            self._listeners = [
                entry for entry in self._listeners if entry[0] != listener]
            self._listeners.append((listener, refresh))

    def remove_listener(self, listener: StatusChangeListener) -> None:
        """Unregister a callback. Unknown callbacks are ignored."""
        with self._lock:
            self._listeners = [
                entry for entry in self._listeners if entry[0] != listener]

    def _value_changed(self, value: Optional[float],
                       reference: Optional[float]) -> bool:
        """True if value differs from the reference (the value of the last
        event) by the configured factor. Going from or to 0 always counts."""
        if value is None or reference is None:
            return False
        if reference == 0:
            return value != 0
        return abs(value - reference) / abs(reference) \
            >= self._value_change_factor

    @staticmethod
    def _reason(trace: Optional[DecisionTrace]) -> Optional[str]:
        """Reason code of the decisive step of a trace."""
        decisive = trace.decisive_record() if trace is not None else None
        return decisive.reason if decisive is not None else None

    def _refresh_due(self, trace: DecisionTrace, now: float) -> bool:
        """True if listeners registered with refresh=True need the trace:
        the reason changed, or the last event is too old."""
        if self._last_event is None:
            return False
        if self._reason(trace) != self._reason(self._last_event.trace):
            return True
        return now - self._last_event_time >= self._refresh_interval

    def commit(self, trace: DecisionTrace, mode: int, control_source: str,
               value: Optional[float] = None) -> Optional[StatusChangeEvent]:
        """Store the trace of a finished decision.

        ``value`` is the value belonging to the mode (charge rate, PV limit).
        Returns the event if the status changed or a refresh was due
        (listeners were notified), otherwise None.
        """
        with self._lock:
            self._traces.append(trace)
            now = self._clock()
            last = self._last_change
            if last is None or mode != last.mode:
                kind = KIND_MODE
            elif self._value_changed(value, last.value):
                kind = KIND_VALUE
            elif self._refresh_due(trace, now):
                kind = KIND_REFRESH
            else:
                return None
            event = StatusChangeEvent(
                kind=kind,
                previous_mode=(last.mode if last is not None
                               and kind != KIND_REFRESH else None),
                mode=mode,
                control_source=control_source,
                trace=trace,
                value=value,
                previous_value=last.value if kind == KIND_VALUE else None,
            )
            if kind != KIND_REFRESH:
                self._last_change = event
            self._last_event = event
            self._last_event_time = now
            listeners = [listener for listener, wants_refresh
                         in self._listeners
                         if wants_refresh or kind != KIND_REFRESH]

        for listener in listeners:
            try:
                listener(event)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    'Decision journal listener %r failed', listener)
        return event

    def latest(self) -> Optional[DecisionTrace]:
        """Trace of the most recent decision."""
        with self._lock:
            return self._traces[-1] if self._traces else None

    def last_status_change(self) -> Optional[StatusChangeEvent]:
        """The most recent status change (mode or value), i.e. the answer
        to "why is the inverter in its current state"."""
        with self._lock:
            return self._last_change

    def last_event(self) -> Optional[StatusChangeEvent]:
        """The most recent event including refresh events: the current
        status with the most recent explanation that was published."""
        with self._lock:
            return self._last_event

    def history(self, count: Optional[int] = None) -> List[DecisionTrace]:
        """Stored traces, oldest first. ``count`` limits to the newest ones."""
        with self._lock:
            traces = list(self._traces)
        if count is None:
            return traces
        return traces[-count:] if count > 0 else []
