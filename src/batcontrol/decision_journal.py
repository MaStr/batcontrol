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
"""
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional

from .logic.decision_trace import DecisionTrace

logger = logging.getLogger(__name__)

DEFAULT_MAX_TRACES = 200
# 0.25: an event is generated when the value changes by 25 % or more
DEFAULT_VALUE_CHANGE_FACTOR = 0.25

KIND_MODE = 'mode'
KIND_VALUE = 'value'


@dataclass(frozen=True)
class StatusChangeEvent:  # pylint: disable=too-many-instance-attributes
    """The inverter status changed.

    ``kind`` is ``'mode'`` or ``'value'``. ``previous_mode`` is None for the
    first mode set after start. ``value`` is the value belonging to the mode
    (W), None for modes without one. ``previous_value`` is only set for
    ``kind == 'value'``: the value of the last event.
    """
    kind: str
    previous_mode: Optional[int]
    mode: int
    control_source: str
    trace: DecisionTrace
    value: Optional[float] = None
    previous_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON friendly representation."""
        return {
            'kind': self.kind,
            'previous_mode': self.previous_mode,
            'mode': self.mode,
            'control_source': self.control_source,
            'value': self.value,
            'previous_value': self.previous_value,
            'trace': self.trace.to_dict(),
        }


StatusChangeListener = Callable[[StatusChangeEvent], None]


class DecisionJournal:
    """Thread safe ring buffer of decision traces plus listener registry."""

    def __init__(self, max_traces: int = DEFAULT_MAX_TRACES,
                 value_change_factor: float = DEFAULT_VALUE_CHANGE_FACTOR):
        if value_change_factor <= 0:
            raise ValueError('value_change_factor must be greater than 0, '
                             f'got {value_change_factor}')
        self._value_change_factor = value_change_factor
        self._lock = threading.Lock()
        self._traces: Deque[DecisionTrace] = deque(maxlen=max_traces)
        self._listeners: List[StatusChangeListener] = []
        self._current_mode: Optional[int] = None
        # value at the last event; later values are compared against it, so
        # a slow drift in small steps is detected as well
        self._reference_value: Optional[float] = None
        self._last_change: Optional[StatusChangeEvent] = None

    def add_listener(self, listener: StatusChangeListener) -> None:
        """Register a callback, called with a :class:`StatusChangeEvent` on
        every status change.

        Listeners run synchronously in the thread that changed the mode
        (scheduler or API thread). They must return quickly; hand slow work
        (network calls) over to a queue or thread. Exceptions are logged and
        do not affect the control loop.
        """
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: StatusChangeListener) -> None:
        """Unregister a callback. Unknown callbacks are ignored."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _value_changed(self, value: Optional[float]) -> bool:
        """True if value differs from the last event's value by the
        configured factor. Going from or to 0 always counts."""
        reference = self._reference_value
        if value is None or reference is None:
            return False
        if reference == 0:
            return value != 0
        return abs(value - reference) / abs(reference) \
            >= self._value_change_factor

    def commit(self, trace: DecisionTrace, mode: int, control_source: str,
               value: Optional[float] = None) -> Optional[StatusChangeEvent]:
        """Store the trace of a finished decision.

        ``value`` is the value belonging to the mode (charge rate, PV limit).
        Returns the event if the status changed (listeners were notified),
        otherwise None.
        """
        with self._lock:
            self._traces.append(trace)
            if mode != self._current_mode:
                kind = KIND_MODE
            elif self._value_changed(value):
                kind = KIND_VALUE
            else:
                return None
            event = StatusChangeEvent(
                kind=kind,
                previous_mode=self._current_mode,
                mode=mode,
                control_source=control_source,
                trace=trace,
                value=value,
                previous_value=(self._reference_value
                                if kind == KIND_VALUE else None),
            )
            self._current_mode = mode
            self._reference_value = value
            self._last_change = event
            listeners = list(self._listeners)

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
        """The most recent status change, i.e. the answer to "why is the
        inverter in its current state"."""
        with self._lock:
            return self._last_change

    def history(self, count: Optional[int] = None) -> List[DecisionTrace]:
        """Stored traces, oldest first. ``count`` limits to the newest ones."""
        with self._lock:
            traces = list(self._traces)
        if count is None:
            return traces
        return traces[-count:] if count > 0 else []
