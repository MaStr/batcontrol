# Decision Trace and Journal

Batcontrol records *why* it selected an inverter mode. Every control cycle
produces a **decision trace**: the ordered list of decision steps that were
evaluated, each with its inputs and a stable reason code. The traces are kept in
an in-memory **journal**, and registered listeners are called whenever the
inverter status changes (new mode, or a significant change of the charge rate /
PV limit).

This is the foundation for answering "why did batcontrol do this?" from the
outside, for example from a chat bot or an MCP server.

## Data model

Defined in `src/batcontrol/logic/decision_trace.py`.

| Type | Purpose |
|------|---------|
| `DecisionRecord` | One step: `decision`, `outcome`, `reason`, `inputs`, `decisive` |
| `DecisionTrace` | Steps of one cycle plus timestamp. `decisive_record()` returns the step that determined the result |

A step is *decisive* when it determines the control settings at the time it
runs. A later decisive step supersedes an earlier one (for example a peak
shaving limit after "discharge allowed"). Steps that were evaluated but did not
change anything (`skipped`, `not_needed`) stay in the trace, so the trace also
explains why something did **not** happen.

### Decisions and reason codes

| Decision | Outcome | Reason | Decisive |
|----------|---------|--------|----------|
| `discharge` | `allowed` | `ALWAYS_ALLOW_DISCHARGE_LIMIT`, `USABLE_ENERGY_EXCEEDS_RESERVE` | yes |
| `discharge` | `forbidden` | `RESERVE_REQUIRED` | no |
| `grid_recharge` | `charge` | `GRID_RECHARGE_REQUIRED` | yes |
| `grid_recharge` | `no_charge` | `GRID_CHARGE_LIMIT_REACHED`, `NO_RECHARGE_REQUIRED` | yes |
| `peak_shaving` | `limit_set` | `PV_CHARGE_LIMITED` | yes |
| `peak_shaving` | `not_needed` | `NO_LIMIT_NEEDED` | no |
| `peak_shaving` | `skipped` | `PRICE_LIMIT_MISSING`, `NO_PV_PRODUCTION`, `PAST_FULL_BATTERY_HOUR`, `ALWAYS_ALLOW_DISCHARGE_REGION`, `FORCE_CHARGE_ACTIVE`, `DISCHARGE_NOT_ALLOWED`, `EVCC_CHARGING`, `EVCC_EV_EXPECTS_PV_SURPLUS` | no |
| `solar_limit` | `limit_set` | `CLIP_ABSORPTION_LIMIT` | yes |
| `solar_limit` | `not_needed` | `NO_LIMIT_NEEDED`, `NO_CLIP_PREDICTED` | no |
| `solar_limit` | `skipped` | `NO_PV_PRODUCTION`, `FORCE_CHARGE_ACTIVE`, `DISCHARGE_NOT_ALLOWED` | no |
| `override` | `applied` | `EXTERNAL_DISCHARGE_BLOCK`, `GRID_CHARGE_LOCK`, `FORECAST_ERROR_FALLBACK`, `CALCULATION_FAILED`, `API_REQUEST` | yes |
| `mode` | `allow_discharging`, `limit_battery_charge_rate`, `avoid_discharging`, `force_charge` | reason of the decisive step | - |

The last record of every trace is the `mode` record. Its inputs contain the
numeric `mode`, the `control_source` (`optimizer` or `api`), `decided_by` (the
decisive decision) and, where relevant, `charge_rate` or
`limit_battery_charge_rate`.

Reason codes are part of the interface: consumers may match on them, so they are
not renamed lightly.

## Logging

Each record can render itself as one line (`DecisionRecord.summary()`). The
logic classes log decisive steps at `INFO` and all other steps at `DEBUG`:

```
[Rule] Grid recharge decision: charge (GRID_RECHARGE_REQUIRED), current_price=0.200, min_dynamic_price_difference=0.050, stored_energy=2000.0 Wh, ...
```

Peak shaving and solar limit keep their existing `[PeakShaving]` and
`[SolarLimit]` log lines; their records are only added to the trace.

## Journal and status change listeners

Defined in `src/batcontrol/decision_journal.py`. `Batcontrol.decision_journal`
holds the last 200 traces (in memory only, lost on restart).

```python
journal = batcontrol.decision_journal

journal.latest()               # trace of the most recent decision
journal.last_status_change()   # why the inverter is in its current state
journal.history(10)            # newest 10 traces, oldest first
```

Every mode change, whether it comes from the optimizer, the MQTT API, evcc or
a fallback, ends up in the journal. Listeners are called on a **status change**:

| `kind` | When |
|--------|------|
| `mode` | The inverter mode changed (also once after start, with `previous_mode=None`) |
| `value` | The mode stayed the same, but its value changed by **25 % or more** |

The value of a mode is the charge rate in force charge (`-1`) and the PV limit
in the limit mode (`8`). The modes allow discharging (`10`) and avoid
discharging (`0`) have no value and only produce `mode` events.

The change is measured against the value of the **last event**, not against the
previous cycle. A charge rate that creeps up by 10 % per cycle (500, 550, 600,
650 W) therefore produces an event at 650 W. A change from or to 0 W always
counts. The factor is `DEFAULT_VALUE_CHANGE_FACTOR` (0.25) and can be set via
`DecisionJournal(value_change_factor=...)`.

```python
def on_status_change(event):        # event: StatusChangeEvent
    print(event.kind, event.previous_mode, '->', event.mode, event.control_source)
    print(event.previous_value, '->', event.value)   # previous_value: kind 'value' only
    print(event.trace.summary())
    payload = event.to_dict()       # JSON friendly, e.g. for a chat bot

batcontrol.decision_journal.add_listener(on_status_change)
```

What a listener does is up to the listener. The journal only guarantees:

- The listener gets the complete trace of the decision that caused the event.
- A failing listener is logged and never affects the control loop or other
  listeners.
- Listeners run **synchronously** in the thread that changed the mode
  (scheduler or MQTT thread). Slow work such as network calls must be handed
  over to a queue or a thread by the listener.

Cycles without a status change do not call listeners; they are still stored in
the journal and available via `latest()` and `history()`.

### Built-in listener: Home Assistant "Decision" sensor

If MQTT is enabled, `MqttApi.publish_status_change` is registered as listener.
It publishes the mode with its value and reason as text on `<base>/decision`
(e.g. `Charge from Grid 1250 W - Grid recharge required`) and the trace as JSON
on `<base>/decision/attributes`. Both are retained. Home Assistant discovers
them as the **Decision** sensor, with the JSON as attributes. The sensor
therefore changes on status changes only, not on every evaluation.

## Adding a decision step

1. Add constants to `Decision`, `Outcome` and `Reason` if needed.
2. Add a `DecisionRecord` where the decision is made, with the numbers that
   explain it in `inputs`. Use `trace.add(record, logger)` to log it.
3. Mark it `decisive=True` only if it determines the control settings.
4. Add the unit of new numeric inputs to `_INPUT_FORMATS` in
   `decision_trace.py`, otherwise they are printed unformatted.
5. Keep reason strings ASCII-only.
