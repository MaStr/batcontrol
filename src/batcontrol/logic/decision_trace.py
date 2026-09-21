"""Structured records of battery control decisions.

A control cycle walks through several decision steps (discharge rule, grid
recharge, peak shaving, overrides, ...). Each step is captured as a
:class:`DecisionRecord`; the steps of one cycle form a :class:`DecisionTrace`.

The trace is the single source of truth for "why did batcontrol do this".
The log line is only one rendering of a record (:meth:`DecisionRecord.summary`),
other consumers (journal, MQTT, chat bots, MCP) work on the structured data
(:meth:`DecisionTrace.to_dict`).

This module must not import other logic modules, so that
``logic_interface`` can use it without an import cycle.
"""
# Decision, Outcome and Reason are plain namespaces for string constants.
# pylint: disable=too-few-public-methods
import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class Decision:
    """Identifiers of the decision steps a control cycle can go through."""
    DISCHARGE = 'discharge'
    GRID_RECHARGE = 'grid_recharge'
    PEAK_SHAVING = 'peak_shaving'
    SOLAR_LIMIT = 'solar_limit'
    OVERRIDE = 'override'
    MODE = 'mode'


class Outcome:
    """Result of a decision step."""
    ALLOWED = 'allowed'
    FORBIDDEN = 'forbidden'
    CHARGE = 'charge'
    NO_CHARGE = 'no_charge'
    LIMIT_SET = 'limit_set'
    NOT_NEEDED = 'not_needed'
    SKIPPED = 'skipped'
    APPLIED = 'applied'


class Reason:
    """Stable, machine readable reason codes (ASCII, upper case)."""
    # discharge
    ALWAYS_ALLOW_DISCHARGE_LIMIT = 'ALWAYS_ALLOW_DISCHARGE_LIMIT'
    USABLE_ENERGY_EXCEEDS_RESERVE = 'USABLE_ENERGY_EXCEEDS_RESERVE'
    RESERVE_REQUIRED = 'RESERVE_REQUIRED'
    # grid recharge
    GRID_RECHARGE_REQUIRED = 'GRID_RECHARGE_REQUIRED'
    GRID_CHARGE_LIMIT_REACHED = 'GRID_CHARGE_LIMIT_REACHED'
    NO_RECHARGE_REQUIRED = 'NO_RECHARGE_REQUIRED'
    # peak shaving / solar limit
    PV_CHARGE_LIMITED = 'PV_CHARGE_LIMITED'
    CLIP_ABSORPTION_LIMIT = 'CLIP_ABSORPTION_LIMIT'
    NO_LIMIT_NEEDED = 'NO_LIMIT_NEEDED'
    NO_CLIP_PREDICTED = 'NO_CLIP_PREDICTED'
    PRICE_LIMIT_MISSING = 'PRICE_LIMIT_MISSING'
    NO_PV_PRODUCTION = 'NO_PV_PRODUCTION'
    PAST_FULL_BATTERY_HOUR = 'PAST_FULL_BATTERY_HOUR'
    ALWAYS_ALLOW_DISCHARGE_REGION = 'ALWAYS_ALLOW_DISCHARGE_REGION'
    FORCE_CHARGE_ACTIVE = 'FORCE_CHARGE_ACTIVE'
    DISCHARGE_NOT_ALLOWED = 'DISCHARGE_NOT_ALLOWED'
    # overrides outside the logic
    EVCC_CHARGING = 'EVCC_CHARGING'
    EVCC_EV_EXPECTS_PV_SURPLUS = 'EVCC_EV_EXPECTS_PV_SURPLUS'
    EXTERNAL_DISCHARGE_BLOCK = 'EXTERNAL_DISCHARGE_BLOCK'
    GRID_CHARGE_LOCK = 'GRID_CHARGE_LOCK'
    FORECAST_ERROR_FALLBACK = 'FORECAST_ERROR_FALLBACK'
    CALCULATION_FAILED = 'CALCULATION_FAILED'
    API_REQUEST = 'API_REQUEST'
    UNSPECIFIED = 'UNSPECIFIED'


# Labels of the mode outcomes, same wording as the Home Assistant mode select
_MODE_LABELS = {
    'allow_discharging': 'Discharge Allowed',
    'limit_battery_charge_rate': 'Limit Battery Charge',
    'avoid_discharging': 'Avoid Discharge',
    'force_charge': 'Charge from Grid',
}
# Words of reason codes that keep their upper case in the status text
_ACRONYMS = ('PV', 'EV', 'EVCC', 'API')
# Home Assistant limits the state of an entity to 255 characters
_MAX_STATUS_TEXT_LENGTH = 255

_PREFIXES = {
    Decision.PEAK_SHAVING: '[PeakShaving]',
    Decision.SOLAR_LIMIT: '[SolarLimit]',
}
_DEFAULT_PREFIX = '[Rule]'

_TITLES = {
    Decision.DISCHARGE: 'Discharge decision',
    Decision.GRID_RECHARGE: 'Grid recharge decision',
    Decision.PEAK_SHAVING: 'Peak shaving decision',
    Decision.SOLAR_LIMIT: 'Solar limit decision',
    Decision.OVERRIDE: 'Override',
    Decision.MODE: 'Mode decision',
}

# printf style format (including the unit) used to render known inputs
_PRICE = '%.3f'
_ENERGY = '%0.1f Wh'
_POWER = '%d W'
_INPUT_FORMATS = {
    'current_price': _PRICE,
    'future_price': _PRICE,
    'min_dynamic_price_difference': _PRICE,
    'always_allow_discharge_limit': '%.2f',
    'stored_energy': _ENERGY,
    'stored_usable_energy': _ENERGY,
    'reserved_energy': _ENERGY,
    'requested_recharge_energy': _ENERGY,
    'required_recharge_energy': _ENERGY,
    'recharge_energy': _ENERGY,
    'available_grid_charge_capacity': _ENERGY,
    'charge_limit_capacity': _ENERGY,
    'remaining_time': '%0.2f h',
    'charge_rate': _POWER,
    'limit_battery_charge_rate': _POWER,
    'price_limit_w': _POWER,
    'time_limit_w': _POWER,
    'floor_w': _POWER,
    'cap_w': _POWER,
    'final_limit_w': _POWER,
    'previous_limit_w': _POWER,
    'feed_in_limit_w': _POWER,
}


def _plain(value: Any) -> Any:
    """Convert numpy scalars to plain python values (JSON friendly)."""
    if hasattr(value, 'item') and not isinstance(value, (list, tuple, dict)):
        try:
            return value.item()
        except (AttributeError, ValueError):
            return value
    return value


def _format_input(key: str, value: Any) -> str:
    """Render one input as ``key=value`` for the summary line."""
    fmt = _INPUT_FORMATS.get(key)
    if value is None:
        rendered = 'n/a'
    elif fmt is not None and isinstance(value, (int, float)) \
            and not isinstance(value, bool):
        rendered = fmt % value
    else:
        rendered = str(value)
    return f'{key}={rendered}'


@dataclass(frozen=True)
class DecisionRecord:
    """One decision step of a control cycle.

    ``decisive`` marks steps that determine the resulting control settings
    at the time they run. A later decisive step supersedes an earlier one,
    see :meth:`DecisionTrace.decisive_record`.
    """
    decision: str
    outcome: str
    reason: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    decisive: bool = False

    def summary(self) -> str:
        """Compact one-line rendering, used for logging."""
        prefix = _PREFIXES.get(self.decision, _DEFAULT_PREFIX)
        title = _TITLES.get(self.decision, self.decision)
        text = f'{prefix} {title}: {self.outcome} ({self.reason})'
        if self.inputs:
            text += ', ' + ', '.join(
                _format_input(key, value) for key, value in self.inputs.items())
        return text

    def to_dict(self) -> Dict[str, Any]:
        """JSON friendly representation."""
        return {
            'decision': self.decision,
            'outcome': self.outcome,
            'reason': self.reason,
            'decisive': self.decisive,
            'inputs': {key: _plain(value) for key, value in self.inputs.items()},
        }


@dataclass
class DecisionTrace:
    """Ordered decision steps of one control cycle."""
    timestamp: Optional[datetime.datetime] = None
    records: List[DecisionRecord] = field(default_factory=list)

    def add(self, record: DecisionRecord,
            log: Optional[logging.Logger] = None) -> DecisionRecord:
        """Append a step. With ``log`` the summary is logged as well:
        INFO for decisive steps, DEBUG for all others."""
        self.records.append(record)
        if log is not None:
            log.log(logging.INFO if record.decisive else logging.DEBUG,
                    '%s', record.summary())
        return record

    def extend(self, other: 'DecisionTrace') -> None:
        """Append all steps of another trace."""
        self.records.extend(other.records)

    def decisive_record(self) -> Optional[DecisionRecord]:
        """The last decisive step, i.e. the one that determined the outcome."""
        for record in reversed(self.records):
            if record.decisive:
                return record
        return None

    def summary(self) -> str:
        """Short text: which step decided and which steps were evaluated."""
        decisive = self.decisive_record()
        steps = ', '.join(f'{r.decision}={r.outcome}' for r in self.records)
        if decisive is None:
            return f'No decisive step. Steps: {steps}'
        return (f'Decided by {decisive.decision} ({decisive.reason}). '
                f'Steps: {steps}')

    def status_text(self) -> str:
        """The resulting mode with its value and the reason as one string,
        e.g. ``Charge from Grid 1250 W - Grid recharge required``.
        Empty if the trace has no mode record yet."""
        mode = next((r for r in reversed(self.records)
                     if r.decision == Decision.MODE), None)
        if mode is None:
            return ''
        text = _MODE_LABELS.get(mode.outcome, mode.outcome)
        value = mode.inputs.get('charge_rate',
                                mode.inputs.get('limit_battery_charge_rate'))
        if value is not None:
            text += f' {int(value)} W'
        text += ' - ' + self._reason_text(mode.reason)
        return text[:_MAX_STATUS_TEXT_LENGTH]

    @staticmethod
    def _reason_text(reason: str) -> str:
        """GRID_RECHARGE_REQUIRED -> Grid recharge required"""
        words = [word if word in _ACRONYMS else word.lower()
                 for word in reason.split('_')]
        if words[0] not in _ACRONYMS:
            words[0] = words[0].capitalize()
        return ' '.join(words)

    def to_dict(self) -> Dict[str, Any]:
        """JSON friendly representation."""
        decisive = self.decisive_record()
        return {
            'timestamp': (self.timestamp.isoformat()
                          if self.timestamp is not None else None),
            'decided_by': (
                {'decision': decisive.decision,
                 'outcome': decisive.outcome,
                 'reason': decisive.reason}
                if decisive is not None else None),
            'records': [record.to_dict() for record in self.records],
        }
