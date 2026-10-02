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

import numpy as np

logger = logging.getLogger(__name__)


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
    # outcomes of the mode record, one per inverter mode
    ALLOW_DISCHARGING = 'allow_discharging'
    LIMIT_BATTERY_CHARGE_RATE = 'limit_battery_charge_rate'
    AVOID_DISCHARGING = 'avoid_discharging'
    FORCE_CHARGE = 'force_charge'


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
    EXTERNAL_DISCHARGE_UNBLOCK = 'EXTERNAL_DISCHARGE_UNBLOCK'
    GRID_CHARGE_LOCK = 'GRID_CHARGE_LOCK'
    FORECAST_ERROR_FALLBACK = 'FORECAST_ERROR_FALLBACK'
    CALCULATION_FAILED = 'CALCULATION_FAILED'
    API_REQUEST = 'API_REQUEST'
    UNSPECIFIED = 'UNSPECIFIED'


# Labels of the mode outcomes, same wording as the Home Assistant mode select
_MODE_LABELS = {
    Outcome.ALLOW_DISCHARGING: 'Discharge Allowed',
    Outcome.LIMIT_BATTERY_CHARGE_RATE: 'Limit Battery Charge',
    Outcome.AVOID_DISCHARGING: 'Avoid Discharge',
    Outcome.FORCE_CHARGE: 'Charge from Grid',
}
# Words of reason codes that keep their upper case in the status text
_ACRONYMS = ('PV', 'EV', 'EVCC', 'API')

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
    'cheaper_price': _PRICE,
    'min_dynamic_price_difference': _PRICE,
    'always_allow_discharge_limit': '%.2f',
    'stored_energy': _ENERGY,
    'stored_usable_energy': _ENERGY,
    'reserved_energy': _ENERGY,
    'requested_recharge_energy': _ENERGY,
    'required_recharge_energy': _ENERGY,
    'recharge_energy': _ENERGY,
    'high_price_energy_demand': _ENERGY,
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


def plain_value(value: Any) -> Any:
    """Convert numpy scalars to plain python values (JSON friendly)."""
    return value.item() if isinstance(value, np.generic) else value


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


def _reason_text(reason: str) -> str:
    """GRID_RECHARGE_REQUIRED -> Grid recharge required.

    Used as the fallback explanation for a reason code that has no entry
    in ``_REASON_EXPLANATIONS``, or whose explanation could not be built
    from the record's inputs (see :meth:`DecisionRecord.explanation`).
    """
    words = [word if word in _ACRONYMS else word.lower()
             for word in reason.split('_')]
    if words[0] not in _ACRONYMS:
        words[0] = words[0].capitalize()
    return ' '.join(words)


# One-sentence, plain-language explanation per reason code, for a human
# reading the Home Assistant "Decision" sensor or the trace JSON rather than
# the source code. Entries with ``{placeholder}`` fields are filled in by
# ``str.format(**self.inputs)`` from the record's own inputs -- the same
# numbers already captured for the log line, so no extra data is needed.
# ``:.0%`` turns a 0..1 ratio into a percentage, same as elsewhere in the
# format mini-language. A static string (no placeholders) is used as-is.
# See :meth:`DecisionRecord.explanation`.
_REASON_EXPLANATIONS = {
    Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT:
        'stored energy ({stored_energy:.0f} Wh) is above the '
        'always-allow-discharge level ({always_allow_discharge_limit:.0%} '
        'of capacity)',
    Reason.USABLE_ENERGY_EXCEEDS_RESERVE:
        'usable energy ({stored_usable_energy:.0f} Wh) exceeds the '
        '{reserved_energy:.0f} Wh reserved for upcoming expensive hours',
    Reason.RESERVE_REQUIRED:
        'usable energy ({stored_usable_energy:.0f} Wh) is below the '
        '{reserved_energy:.0f} Wh reserved for upcoming expensive hours',
    Reason.GRID_RECHARGE_REQUIRED:
        'usable energy ({stored_usable_energy:.0f} Wh) is below the '
        '{reserved_energy:.0f} Wh reserved for upcoming expensive hours, '
        'so {recharge_energy:.0f} Wh is charged from the grid',
    Reason.GRID_CHARGE_LIMIT_REACHED:
        'stored energy ({stored_energy:.0f} Wh) is already at or above '
        'the {charge_limit_capacity:.0f} Wh grid-charging limit',
    Reason.NO_RECHARGE_REQUIRED:
        'stored energy is sufficient until prices rise again, '
        'so no grid charging is needed',
    Reason.PV_CHARGE_LIMITED:
        'PV charging is capped at {final_limit_w} W ({active_components}) '
        'so the battery does not fill up before {allow_full_battery_after}:00',
    # final_limit_w is only ever None when this rule did not set a limit,
    # i.e. for a different reason (NO_LIMIT_NEEDED) than this one.
    Reason.CLIP_ABSORPTION_LIMIT:
        'the battery charges at {final_limit_w} W to absorb solar surplus '
        'that would otherwise be clipped at the {feed_in_limit_w:.0f} W '
        'feed-in limit',
    Reason.NO_LIMIT_NEEDED: 'no PV-charge limit is needed right now',
    Reason.NO_CLIP_PREDICTED:
        'no clipping is predicted at the {feed_in_limit_w:.0f} W feed-in '
        'limit, so PV charging is not limited',
    Reason.PRICE_LIMIT_MISSING:
        'the price-based peak-shaving limit has no price_limit configured, '
        'so it has no effect',
    Reason.NO_PV_PRODUCTION: 'there is currently no solar production',
    Reason.PAST_FULL_BATTERY_HOUR:
        'it is past the {allow_full_battery_after}:00 target hour, '
        'so peak shaving no longer limits charging',
    Reason.ALWAYS_ALLOW_DISCHARGE_REGION:
        'the battery is already above the always-allow-discharge level, '
        'so this rule is skipped',
    Reason.FORCE_CHARGE_ACTIVE:
        'grid charging (mode -1) is active and takes priority over this rule',
    Reason.DISCHARGE_NOT_ALLOWED:
        'the battery is preserved for high-price hours, so this rule has no effect',
    Reason.EVCC_CHARGING: 'evcc is actively charging the car, so peak shaving is paused',
    Reason.EVCC_EV_EXPECTS_PV_SURPLUS:
        'an EV is connected in solar-surplus mode, so peak shaving is paused',
    Reason.EXTERNAL_DISCHARGE_BLOCK:
        'an external system (such as evcc) requested that the battery not discharge',
    Reason.EXTERNAL_DISCHARGE_UNBLOCK:
        'the external discharge block was lifted; the next evaluation decides what to do',
    Reason.GRID_CHARGE_LOCK:
        'an external request (e.g. a grid operator signal) is blocking '
        'charging from the grid',
    Reason.FORECAST_ERROR_FALLBACK:
        'forecast data could not be refreshed for {seconds_since_error} '
        'seconds, falling back to a safe mode',
    Reason.CALCULATION_FAILED:
        'the control calculation failed; falling back to a safe mode',
    Reason.API_REQUEST: 'requested via the API or Home Assistant',
    Reason.UNSPECIFIED: 'no specific reason was recorded for this change',
}


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

    def explanation(self) -> str:
        """One sentence, plain language explanation of this step, for a
        human reading the HA sensor or the trace JSON rather than the
        source code. Fills the ``{placeholder}`` fields of the template
        for this record's reason code (see ``_REASON_EXPLANATIONS``) from
        its own inputs; falls back to a readable version of the reason
        code itself if there is no template, or if filling it in fails
        (e.g. an expected input is missing -- this must never raise into
        a log line or the MQTT publish)."""
        template = _REASON_EXPLANATIONS.get(self.reason)
        if template is not None:
            try:
                return template.format(**self.inputs)
            except (KeyError, IndexError, ValueError, TypeError):
                logger.debug(
                    'No explanation for reason %s from inputs %s',
                    self.reason, self.inputs, exc_info=True)
        return _reason_text(self.reason)

    def to_dict(self) -> Dict[str, Any]:
        """JSON friendly representation."""
        return {
            'decision': self.decision,
            'outcome': self.outcome,
            'reason': self.reason,
            'why': self.explanation(),
            'decisive': self.decisive,
            'inputs': {key: plain_value(value) for key, value in self.inputs.items()},
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

    def step(self, decision: str, outcome: str, reason: str, *,
             decisive: bool = False, **inputs) -> DecisionRecord:
        """Append a step given by its parts, without logging."""
        return self.add(DecisionRecord(decision, outcome, reason, inputs,
                                       decisive))

    def extend(self, other: 'DecisionTrace') -> None:
        """Append all steps of another trace."""
        self.records.extend(other.records)

    def log_full_trace(self, log: logging.Logger) -> None:
        """Log every step of this trace as one DEBUG block, e.g. with
        ``loglevel: debug``. Independent of the per-step logging in
        ``add()``: some steps (peak shaving/solar limit skips, core.py
        overrides) are added via ``step()`` without a logger and are
        otherwise never logged at all."""
        if not log.isEnabledFor(logging.DEBUG):
            return
        log.debug(
            'Decision trace (%d steps):\n%s',
            len(self.records),
            '\n'.join(record.summary() for record in self.records))

    def decisive_record(self) -> Optional[DecisionRecord]:
        """The last decisive step, i.e. the one that determined the outcome."""
        for record in reversed(self.records):
            if record.decisive:
                return record
        return None

    def status_text(self) -> str:
        """The resulting mode with its value and a plain language
        explanation as one string, e.g. ``Charge from Grid 1250 W - usable
        energy (900 Wh) is below the 2500 Wh reserved for upcoming
        expensive hours, so 1600 Wh is charged from the grid``.
        Empty if the trace has no mode record yet."""
        mode = next((r for r in reversed(self.records)
                     if r.decision == Decision.MODE), None)
        if mode is None:
            return ''
        text = _MODE_LABELS.get(mode.outcome, mode.outcome)
        value = mode.inputs.get('value')
        if value is not None:
            text += f' {int(value)} W'
        # The mode record's own inputs do not carry the numbers behind the
        # decision (those live on the decisive step); use that step's
        # explanation, falling back to the reason code if there is none.
        decisive = self.decisive_record()
        explanation = decisive.explanation() if decisive is not None \
            else _reason_text(mode.reason)
        return f'{text} - {explanation}'

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
            'why': decisive.explanation() if decisive is not None else None,
            'records': [record.to_dict() for record in self.records],
        }
