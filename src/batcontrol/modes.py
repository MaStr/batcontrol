""" Inverter control modes

The mode values are a stable, externally consumed contract: they appear in the
MQTT API, in the Decision sensor, in the REST API and in the dashboard. They
live in their own module so consumers can import them without pulling in
core.py (which imports every provider factory).

core.py re-exports these names, so `from batcontrol.core import MODE_*` keeps
working.

Two name sets exist on purpose:
  MODE_NAMES  - machine readable outcome codes, shared with the decision trace
  MODE_LABELS - display strings for the REST API and the dashboard
"""
from .logic.decision_trace import Outcome

MODE_ALLOW_DISCHARGING = 10
MODE_LIMIT_BATTERY_CHARGE_RATE = 8  # Limit PV charge, allow discharge
MODE_AVOID_DISCHARGING = 0
MODE_FORCE_CHARGING = -1

MODE_NAMES = {
    MODE_ALLOW_DISCHARGING: Outcome.ALLOW_DISCHARGING,
    MODE_LIMIT_BATTERY_CHARGE_RATE: Outcome.LIMIT_BATTERY_CHARGE_RATE,
    MODE_AVOID_DISCHARGING: Outcome.AVOID_DISCHARGING,
    MODE_FORCE_CHARGING: Outcome.FORCE_CHARGE,
}

MODE_LABELS = {
    MODE_ALLOW_DISCHARGING: 'ALLOW DISCHARGE',
    MODE_LIMIT_BATTERY_CHARGE_RATE: 'LIMIT CHARGE RATE',
    MODE_AVOID_DISCHARGING: 'AVOID DISCHARGE',
    MODE_FORCE_CHARGING: 'FORCE CHARGE',
}



def mode_code(mode) -> str:
    """ Machine readable outcome code for a mode value.

    Unknown values are reported as-is instead of raising, so a new mode in a
    newer batcontrol never breaks a consumer that only renders the value.
    """
    if mode is None:
        return 'unknown'
    return MODE_NAMES.get(mode, f'mode_{mode}')


def mode_label(mode) -> str:
    """ Display label for a mode value, as shown in the dashboard. """
    if mode is None:
        return 'UNKNOWN'
    return MODE_LABELS.get(mode, f'MODE {mode}')
