""" Read-only service layer

Every REST route and every MCP tool reads through this module. That keeps one
source of truth for field names, units and rounding, and it keeps business
logic out of both transports.

Two rules hold for everything in here:

1. **Read-only.** Nothing in this module changes batcontrol state.
2. **No inverter traffic.** The service reads the values the control loop has
   already cached (``last_SOC``, ``last_max_capacity``, ...), never the
   ``get_*`` accessors on the core, because those fetch from the inverter on a
   cache miss and would mutate the loop's cache flags from a web thread. An
   HTTP request must never cause Modbus or HTTP traffic towards the inverter.
"""
import logging
import os
import time
from typing import Any, Dict, Optional

from .. import __pkginfo__
from ..modes import mode_code, mode_label
from .snapshot import ENERGY_DIGITS, PRICE_DIGITS, ForecastSnapshot

logger = logging.getLogger(__name__)

# A cycle older than this many evaluation intervals counts as stale: the
# control loop has missed several runs and the data should not be presented as
# current.
STALE_AFTER_INTERVALS = 3

# Default forecast window, in hours. Expressed in hours rather than slots
# because a slot is 15 or 60 minutes depending on the configuration: a fixed
# slot count would mean 48 hours at one resolution and 12 at the other.
DEFAULT_WINDOW_HOURS = 24

# Upper bound for a forecast window or offset: one week at 15-minute
# resolution. The real horizon is at most about two days, so this is a
# validation guard for both transports, not a usable window.
WINDOW_MAX = 672
# The journal keeps 200 traces by default; no transport may ask for more.
HISTORY_MAX = 200


class NoDataAvailable(Exception):
    """ Raised while the first control cycle has not completed yet. """
    code = 'no_data_yet'


class ForecastExpired(NoDataAvailable):
    """ Raised when every slot of the last published forecast has passed. """
    code = 'forecast_expired'


class NotFound(Exception):
    """ Raised when a requested resource does not exist (yet). """


def _dt_ms(moment) -> Optional[int]:
    """ Convert a datetime to epoch milliseconds.

    The decision trace carries a datetime (serialised as ISO 8601 for the
    MQTT contract). The API states timestamps in epoch milliseconds
    throughout, so the conversion happens here rather than leaving two time
    formats in one response for consumers to sort out.
    """
    if moment is None:
        return None
    try:
        return int(moment.timestamp() * 1000)
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def _ms(seconds: Optional[float]) -> Optional[int]:
    """ Convert epoch seconds to epoch milliseconds. """
    if seconds is None or seconds <= 0:
        return None
    return int(seconds * 1000)


def _round(value, digits: int = ENERGY_DIGITS) -> Optional[float]:
    """ Round a value, mapping None and non-numeric input to None. """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits)


def _cached(value, digits: int = ENERGY_DIGITS) -> Optional[float]:
    """ Round a cached core value, honouring batcontrol's -1 sentinel.

    The core initialises its cached capacities and the SoC to -1 and only
    replaces them once the inverter has been read. Reporting that as a
    negative energy would be a lie, so it becomes None (unknown).
    """
    rounded = _round(value, digits)
    if rounded is None or rounded == -1:
        return None
    return rounded


class BatcontrolService:
    """ Transport independent read access to a live Batcontrol instance. """

    def __init__(self, batcontrol):
        self._bc = batcontrol
        # Reference for "stale" while no cycle has completed yet: without it
        # a loop that fails on every cycle from startup would report online
        # forever.
        self._started = time.time()

    # -- snapshot ---------------------------------------------------------

    @property
    def snapshot(self) -> Optional[ForecastSnapshot]:
        """ The forecast snapshot of the last completed cycle, if any. """
        return getattr(self._bc, 'forecast_snapshot', None)

    def require_snapshot(self) -> ForecastSnapshot:
        """ Snapshot or NoDataAvailable, for endpoints that need forecasts. """
        snapshot = self.snapshot
        if snapshot is None or not snapshot.has_data():
            raise NoDataAvailable(
                'No completed control cycle yet. The first forecast is'
                ' available after the first evaluation.'
            )
        return snapshot

    @staticmethod
    def _origin(snapshot: ForecastSnapshot) -> int:
        """ Snapshot index of the current slot; raises once all are past. """
        origin = snapshot.current_index(time.time())
        if origin >= len(snapshot.prices):
            raise ForecastExpired(
                'The last forecast has expired: no control cycle has'
                ' completed for the whole forecast horizon.'
            )
        return origin

    def _is_stale(self) -> bool:
        """ True when the data must not be presented as current.

        Measured against the older of the last cycle and the last forecast
        snapshot: a cycle that aborts on a forecast error still advances
        last_run_time, but publishes no snapshot. Until a snapshot exists the
        service start time is the reference - also when cycles run but their
        forecasts fail from startup on.
        """
        last_run = getattr(self._bc, 'last_run_time', 0) or 0
        snapshot = self.snapshot
        if last_run <= 0 or snapshot is None or not snapshot.has_data():
            reference = self._started
        else:
            reference = min(last_run, snapshot.run_time)
        limit = self._evaluation_interval() * STALE_AFTER_INTERVALS
        return time.time() - reference > limit

    # -- meta -------------------------------------------------------------

    def version(self) -> Dict[str, Any]:
        """ Version and runtime identification. """
        return {
            'version': __pkginfo__.__version__,
            'git_sha': os.environ.get('BATCONTROL_GIT_SHA') or None,
            'runtime_env': os.environ.get('BATCONTROL_RUNTIME_ENV') or 'source',
            'interval_minutes': self._bc.time_resolution,
            'timezone': str(self._bc.timezone),
        }

    def health(self) -> Dict[str, Any]:
        """ Liveness information. Intentionally answers before the first cycle.

        This is the endpoint a container health check uses, so it must never
        fail just because no forecast exists yet.
        """
        last_run = getattr(self._bc, 'last_run_time', 0) or 0
        age = None if last_run <= 0 else max(0.0, time.time() - last_run)
        snapshot = self.snapshot
        return {
            'status': 'ok',
            'version': __pkginfo__.__version__,
            'has_data': snapshot is not None and snapshot.has_data(),
            'last_evaluation': _ms(last_run),
            'cycle_age_seconds': None if age is None else round(age, 1),
            'evaluation_interval_seconds': self._evaluation_interval(),
            'stale': self._is_stale(),
        }

    def window_slots(self, hours: float = DEFAULT_WINDOW_HOURS) -> int:
        """ Number of forecast slots covering `hours` at this resolution.

        24 h is 24 slots at 60-minute and 96 slots at 15-minute resolution.
        """
        interval = int(self._bc.time_resolution) or 60
        return max(1, int(hours * 60 / interval))

    def _evaluation_interval(self) -> int:
        return int(getattr(self._bc, 'evaluation_interval_seconds', 180))

    # -- status and battery ----------------------------------------------

    def status(self) -> Dict[str, Any]:
        """ Current control state: what batcontrol is doing right now. """
        last_run = getattr(self._bc, 'last_run_time', 0) or 0
        interval = self._evaluation_interval()
        mode = self._bc.last_mode
        return {
            'mode': mode,
            'mode_code': mode_code(mode),
            'mode_label': mode_label(mode),
            'control_source': self._bc.last_control_source,
            'charge_rate': _round(self._bc.last_charge_rate, 0),
            'limit_battery_charge_rate': getattr(
                self._bc, '_limit_battery_charge_rate', -1),
            'api_override_active': bool(self._bc.api_overwrite),
            'discharge_blocked': bool(self._bc.discharge_blocked),
            'grid_charge_locked': bool(
                getattr(self._bc, '_grid_charge_locked', False)),
            'last_evaluation': _ms(last_run),
            'next_evaluation': _ms(last_run + interval) if last_run else None,
            'evaluation_interval_seconds': interval,
            'online': not self._is_stale(),
            # When the mode last actually changed - not the last refresh, so
            # a UI can show "since HH:MM" rather than "a moment ago" on every
            # cycle. None until the first change after a restart.
            'mode_changed_at': _dt_ms(self._mode_changed_at()),
        }

    def _mode_changed_at(self):
        """ Timestamp of the last mode change, if one is recorded. """
        change = self._bc.decision_journal.last_mode_change()
        if change is None or change.trace is None:
            return None
        return change.trace.timestamp

    def battery(self) -> Dict[str, Any]:
        """ Battery state and the limits the dashboard's SOC grid needs. """
        soc = self._bc.last_SOC
        return {
            'soc': None if soc is None or soc < 0 else round(float(soc), 2),
            'max_energy_capacity': _cached(self._bc.last_max_capacity),
            'stored_energy_capacity': _cached(self._bc.last_stored_energy),
            'stored_usable_energy_capacity': _cached(
                self._bc.last_stored_usable_energy),
            'reserved_energy_capacity': _cached(self._bc.last_reserved_energy),
            'free_capacity': _cached(self._bc.last_free_capacity),
            'always_allow_discharge_limit_percent': _round(
                self._bc.general_logic.get_always_allow_discharge_limit() * 100, 2),
            'max_charging_from_grid_limit_percent': _round(
                self._bc.max_charging_from_grid_limit * 100, 2),
            # Cached by the core per cycle, -1 until the first read.
            'soc_limits': {
                'min_soc': _cached(self._bc.last_min_soc, 2),
                'max_soc': _cached(self._bc.last_max_soc, 2),
            },
        }

    # -- current ----------------------------------------------------------

    def current(self) -> Dict[str, Any]:
        """ Everything the dashboard header and PRICE block need at once.

        Deliberately complete and independent of which decision path ran:
        the current price is taken from the price forecast's current slot,
        not from a decision input that some paths never populate.
        """
        snapshot = self.require_snapshot()
        origin = self._origin(snapshot)
        battery = self.battery()
        return {
            'version': __pkginfo__.__version__,
            'price': {
                'current': _round(snapshot.prices[origin], PRICE_DIGITS),
                'current_ts': snapshot.slot_start_ms(origin),
                'unit': 'EUR/kWh',
                'interval_minutes': snapshot.interval_minutes,
                # A single scalar the logic computes after the snapshot is
                # published; it does not need the snapshot's cycle guarantee.
                'min_dynamic_price_difference': _round(
                    self._bc.last_min_dynamic_price_difference, PRICE_DIGITS),
            },
            'soc': battery['soc'],
            'soc_limits': battery['soc_limits'],
        }

    # -- forecasts --------------------------------------------------------

    # Series name -> (snapshot attribute, unit, digits, include_power)
    SERIES = {
        'price': ('prices', 'EUR/kWh', PRICE_DIGITS, False),
        'consumption': ('consumption', 'Wh', ENERGY_DIGITS, True),
        'production': ('production', 'Wh', ENERGY_DIGITS, True),
        'net_consumption': ('net_consumption', 'Wh', ENERGY_DIGITS, True),
    }

    def forecast(self, name: str, *, limit: Optional[int] = None,
                 offset: int = 0) -> Dict[str, Any]:
        """ One forecast series, optionally windowed.

        Raises NotFound for an unknown series name.
        """
        spec = self.SERIES.get(name)
        if spec is None:
            raise NotFound(
                f'Unknown forecast series {name!r}. Available:'
                f' {", ".join(sorted(self.SERIES))}.'
            )
        snapshot = self.require_snapshot()
        return self._series(snapshot, self._origin(snapshot), spec,
                            limit=limit, offset=offset)

    def forecast_all(self, *, limit: Optional[int] = None,
                     offset: int = 0) -> Dict[str, Any]:
        """ All four series in one response, so the dashboard polls once. """
        # One snapshot read for all four series: a cycle publishing between
        # two reads would otherwise mix two cycles in one response.
        snapshot = self.require_snapshot()
        origin = self._origin(snapshot)
        series = {
            name: self._series(snapshot, origin, spec, limit=limit,
                               offset=offset)
            for name, spec in self.SERIES.items()
        }
        price = series['price']
        return {
            'run_time': _ms(snapshot.run_time),
            'interval_minutes': snapshot.interval_minutes,
            'total_count': price['total_count'],
            'returned_count': price['returned_count'],
            'offset': price['offset'],
            'has_more': price['has_more'],
            'next_offset': price['next_offset'],
            **{name: payload['slots'] for name, payload in series.items()},
        }

    @staticmethod
    def _series(snapshot: ForecastSnapshot, origin: int, spec, *,
                limit: Optional[int] = None,
                offset: int = 0) -> Dict[str, Any]:
        """ Render a series, windowed by offset/limit from the current slot.

        Slot 0 is the slot containing now (`origin` in the snapshot), so a
        snapshot that outlived its first slot never presents a past price as
        current. The window carries its own metadata (total_count, has_more,
        next_offset) so a consumer that cannot hold the full horizon - an LLM
        in particular - can page through it instead of being handed several
        hundred slots.
        """
        attribute, unit, digits, include_power = spec
        values = getattr(snapshot, attribute)
        total = max(0, len(values) - origin)
        offset = max(0, int(offset))
        start = origin + offset
        stop = None if limit is None else start + max(0, int(limit))
        window = snapshot.slots(values, start=start, stop=stop, origin=origin,
                                digits=digits, include_power=include_power)
        next_offset = offset + len(window)
        return {
            'run_time': _ms(snapshot.run_time),
            'interval_minutes': snapshot.interval_minutes,
            'unit': unit,
            'total_count': total,
            'returned_count': len(window),
            'offset': offset,
            'has_more': next_offset < total,
            'next_offset': next_offset if next_offset < total else None,
            'slots': window,
        }

    # -- decisions --------------------------------------------------------

    def decision(self) -> Dict[str, Any]:
        """ The latest decision trace: what batcontrol decided and why. """
        journal = self._bc.decision_journal
        trace = journal.latest()
        if trace is None:
            raise NotFound('No decision recorded yet.')
        event = journal.last_event()
        # Two different ages on purpose, and they must not be confused:
        # `trace` is the latest evaluation, refreshed every control cycle,
        # while `event` is the last status *change* and only moves on a mode
        # or value change (or the journal's 15-minute refresh). A consumer
        # that renders the records from `event` shows a "why" that can be a
        # quarter of an hour older than the mode it sits next to.
        payload = {
            'decided_at': _dt_ms(trace.timestamp),
            'trace': trace.to_dict(),
            'status_text': trace.status_text(),
            'explanation': trace.explanation(),
        }
        decisive = trace.decisive_record()
        payload['decisive'] = None if decisive is None else decisive.to_dict()
        payload['event'] = None if event is None else event.to_dict()
        payload['event_at'] = (
            None if event is None or event.trace is None
            else _dt_ms(event.trace.timestamp))
        return payload

    def decision_history(self, limit: int = 20) -> Dict[str, Any]:
        """ The last traces from the in-memory journal, oldest first. """
        journal = self._bc.decision_journal
        traces = journal.history(limit)
        return {
            'count': len(traces),
            'limit': limit,
            'traces': [trace.to_dict() for trace in traces],
        }

    # -- configuration ----------------------------------------------------

    def configuration(self) -> Dict[str, Any]:
        """ The runtime parameters that explain batcontrol's behaviour.

        Built from an explicit list of core attributes, never from the
        configuration dictionary. The config holds inverter passwords, tariff
        API tokens and MQTT credentials, so there is no code path here that
        could reach them: adding a secret to the config cannot leak it through
        this endpoint, because nothing in here reads the config at all.
        """
        peak_shaving = self._bc.peak_shaving_config
        logic_type = 'default'
        batconfig = getattr(self._bc, 'batconfig', None)
        if isinstance(batconfig, dict):
            logic_type = str(batconfig.get('type', 'default'))
        return {
            'interval_minutes': self._bc.time_resolution,
            'timezone': str(self._bc.timezone),
            'evaluation_interval_seconds': self._evaluation_interval(),
            'battery_control': {
                'type': logic_type,
                'min_price_difference': _round(
                    self._bc.min_price_difference, PRICE_DIGITS),
                'min_price_difference_rel': _round(
                    self._bc.min_price_difference_rel, 4),
                'always_allow_discharge_limit': _round(
                    self._bc.general_logic.get_always_allow_discharge_limit(), 4),
                'max_charging_from_grid_limit': _round(
                    self._bc.max_charging_from_grid_limit, 4),
                'min_grid_charge_soc': _round(self._bc.min_grid_charge_soc, 4),
                'preserve_min_grid_charge_soc': bool(
                    self._bc.preserve_min_grid_charge_soc),
                'production_offset_percent': _round(
                    self._bc.production_offset_percent, 4),
            },
            'peak_shaving': {
                'enabled': bool(peak_shaving.enabled),
                'time_active': bool(peak_shaving.time_active),
                'price_active': bool(peak_shaving.price_active),
                'solar_cap_active': bool(peak_shaving.solar_cap_active),
                'allow_full_battery_after': peak_shaving.allow_full_battery_after,
                'price_limit': _round(peak_shaving.price_limit, PRICE_DIGITS),
                'feed_in_limit_w': _round(peak_shaving.feed_in_limit_w, 0),
            },
        }
