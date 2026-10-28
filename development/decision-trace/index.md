# Decision Trace and Journal

Batcontrol records *why* it selected an inverter mode. Every control cycle produces a **decision trace**: the ordered list of decision steps that were evaluated, each with its inputs and a stable reason code. The traces are kept in an in-memory **journal**, and registered listeners are called whenever the inverter status changes (new mode, or a significant change of the charge rate / PV limit). Listeners that show the explanation (the "Decision" sensor) can also ask for refreshes, so its numbers and reason stay current.

This is the foundation for answering "why did batcontrol do this?" from the outside, for example from a chat bot or an MCP server. For what the Home Assistant "Decision" sensor itself looks like and how to read it, see [Understanding the Decision Sensor](https://mastr.github.io/batcontrol/features/decision-sensor/index.md).

## Data model

Defined in `src/batcontrol/logic/decision_trace.py`.

| Type             | Purpose                                                                                            |
| ---------------- | -------------------------------------------------------------------------------------------------- |
| `DecisionRecord` | One step: `decision`, `outcome`, `reason`, `inputs`, `decisive`, `explanation()`                   |
| `DecisionTrace`  | Steps of one cycle plus timestamp. `decisive_record()` returns the step that determined the result |

A step is *decisive* when it determines the control settings at the time it runs. A later decisive step supersedes an earlier one (for example a peak shaving limit after "discharge allowed"). Steps that were evaluated but did not change anything (`skipped`, `not_needed`) stay in the trace, so the trace also explains why something did **not** happen.

### Decisions and reason codes

| Decision        | Outcome                                                                               | Reason                                                                                                                                                                                                                                   | Decisive                                            |
| --------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `discharge`     | `allowed`                                                                             | `ALWAYS_ALLOW_DISCHARGE_LIMIT`, `USABLE_ENERGY_EXCEEDS_RESERVE`                                                                                                                                                                          | yes                                                 |
| `discharge`     | `forbidden`                                                                           | `RESERVE_REQUIRED`                                                                                                                                                                                                                       | no                                                  |
| `grid_recharge` | `charge`                                                                              | `GRID_RECHARGE_REQUIRED`                                                                                                                                                                                                                 | yes                                                 |
| `grid_recharge` | `no_charge`                                                                           | `GRID_CHARGE_LIMIT_REACHED`, `NO_HIGH_PRICE_SLOTS`, `HIGH_PRICE_DEMAND_COVERED_BY_PRODUCTION`, `NO_RECHARGE_REQUIRED`, `RECHARGE_BELOW_MINIMUM`                                                                                          | yes                                                 |
| `peak_shaving`  | `limit_set`                                                                           | `PV_CHARGE_LIMITED`                                                                                                                                                                                                                      | yes                                                 |
| `peak_shaving`  | `not_needed`                                                                          | `NO_LIMIT_NEEDED`                                                                                                                                                                                                                        | no                                                  |
| `peak_shaving`  | `skipped`                                                                             | `PRICE_LIMIT_MISSING`, `NO_PV_PRODUCTION`, `PAST_FULL_BATTERY_HOUR`, `ALWAYS_ALLOW_DISCHARGE_REGION`, `FORCE_CHARGE_ACTIVE`, `DISCHARGE_NOT_ALLOWED`, `EVCC_CHARGING`, `EVCC_EV_EXPECTS_PV_SURPLUS`                                      | no                                                  |
| `solar_limit`   | `limit_set`                                                                           | `CLIP_ABSORPTION_LIMIT`                                                                                                                                                                                                                  | only if it changed the limit set by an earlier rule |
| `solar_limit`   | `not_needed`                                                                          | `NO_LIMIT_NEEDED`, `NO_CLIP_PREDICTED`                                                                                                                                                                                                   | no                                                  |
| `solar_limit`   | `skipped`                                                                             | `NO_PV_PRODUCTION`, `FORCE_CHARGE_ACTIVE`, `DISCHARGE_NOT_ALLOWED`                                                                                                                                                                       | no                                                  |
| `override`      | `applied`                                                                             | `EXTERNAL_DISCHARGE_BLOCK`, `EXTERNAL_DISCHARGE_UNBLOCK`, `GRID_CHARGE_LOCK`, `FORECAST_ERROR_FALLBACK`, `CALCULATION_FAILED`, `API_REQUEST` (input `requested_mode` shows the mode the API asked for, which can differ if it fell back) | yes                                                 |
| `override`      | `applied`                                                                             | `PV_CHARGE_RATE_CLAMPED`, `GRID_CHARGE_RATE_CLAMPED`                                                                                                                                                                                     | no (note, see below)                                |
| `mode`          | `allow_discharging`, `limit_battery_charge_rate`, `avoid_discharging`, `force_charge` | reason of the decisive step                                                                                                                                                                                                              | -                                                   |

`grid_recharge`/`no_charge` has five reasons for the same outcome, told apart purely from `high_price_slots`/`high_price_energy_demand`/ `recharge_energy_before_minimum` (relative slot indices again, with `interval_minutes`) -- no separate flag is needed:

- `GRID_CHARGE_LIMIT_REACHED`: SoC is already above the grid-charging limit, so the calculation never ran and these stay at their defaults.
- `NO_HIGH_PRICE_SLOTS`: it ran, but no slot in the window is priced high enough to reserve or recharge for (`high_price_slots` is empty).
- `HIGH_PRICE_DEMAND_COVERED_BY_PRODUCTION`: high-price slots exist, but forecast solar production is expected to cover their demand (`high_price_slots` non-empty, `high_price_energy_demand` is 0).
- `NO_RECHARGE_REQUIRED`: demand remains after production, but stored usable battery energy already covers it (`recharge_energy_before_minimum` \<= 0).
- `RECHARGE_BELOW_MINIMUM`: a real demand remained, but the resulting amount is below the minimum charge amount, so nothing is charged for it (`recharge_energy_before_minimum` > 0).

This step is only reached when the `discharge` step forbade discharging, so it is the decisive step of the Avoid Discharge mode. It therefore also carries `stored_usable_energy` and `reserved_energy` from that step, and its explanation says both why the battery is held and why it is not recharged from the grid, e.g. `battery is held for upcoming more expensive hours (usable 900 Wh, reserve 2500 Wh); no grid charging: solar is forecast to cover the demand of the hours where it would pay off`. The two are not a contradiction: the reserve counts every upcoming hour that is more expensive than now, grid charging only pays off for hours that are more expensive by at least the (dynamic) minimum price difference.

`core.py` applies two hardware/config-level clamps *after* the logic has already decided a value: `force_charge()` caps the grid recharge rate to `max_grid_charge_rate`, and `limit_battery_charge_rate()` caps the PV limit to `max_pv_charge_rate`/`min_pv_charge_rate`. When a clamp actually changes the value, it is recorded as a non-decisive `override`/`applied` step (`GRID_CHARGE_RATE_CLAMPED` / `PV_CHARGE_RATE_CLAMPED`, inputs `requested_w` and `applied_w`) between the step that decided the pre-clamp value and the `mode` record. Such a step is a **note**: it adjusts the value, but the reason for the mode stays with the decisive step, and the note is appended to its explanation in brackets (`DecisionTrace.explanation()`, reasons in `NOTE_REASONS`), e.g. `... so 1600 Wh is charged from the grid (charge rate capped from 3200 W to 1000 W by the configured maximum grid charge rate)`. So the "why" matches what was actually applied to the inverter without hiding why the mode was chosen. A clamp that does not change anything (the decided value was already within limits) adds no step. This only applies to mode changes made from within a control cycle (`Batcontrol.run()`); a direct API call (`api_set_charge_rate`/`api_set_limit_battery_charge_rate`) has no decision scope open at that point, so a clamp there is not yet recorded.

The last record of every trace is the `mode` record. Its reason is borrowed from the decisive step, so its inputs start as a copy of that step's own inputs (needed for its `explanation()`/`why` to render correctly), with `mode`, the `control_source` (`optimizer` or `api`), `decided_by` (the decisive decision) and the `value` belonging to the mode in W (charge rate of force charge, PV limit of the limit mode, `null` for the other modes) applied on top.

Reason codes are part of the interface: consumers may match on them, so they are not renamed lightly.

### Plain-language explanations

`DecisionRecord.explanation()` renders one sentence for a reason code, meant for a human rather than the source code -- it is what ends up in the "Decision" sensor's state (`DecisionTrace.status_text()`) and as `why` in `to_dict()` (on the record; on the trace it is `DecisionTrace.explanation()`, the decisive step's sentence plus the notes). The mapping lives in `_REASON_EXPLANATIONS` (`decision_trace.py`): most entries are a plain string with `{input_key}` placeholders, filled in via `str.format(**record.inputs)` from the record's own inputs -- the same numbers already captured for the log line, so adding an explanation never needs a new input. A reason with no placeholders is used as-is. An entry can also be a function of the inputs, for a sentence that depends on the values rather than only showing them (`PV_CHARGE_LIMITED` names the purpose of each active peak shaving limit). If filling in the placeholders fails (a key is missing, e.g. because a record was built before an explanation existed for its reason), `explanation()` falls back to a readable version of the reason code itself and never raises.

Guidelines for the sentence of a decisive reason: it answers "why is the inverter in this mode" (not why something else did not happen), and it does not repeat the value of the mode, which precedes it in the status text (`Limit Battery Charge 1200 W - PV charging is limited so ...`).

## Logging

Each record can render itself as one line (`DecisionRecord.summary()`). The logic classes log decisive steps at `INFO` and all other steps at `DEBUG`:

```
[Rule] Grid recharge decision: charge (GRID_RECHARGE_REQUIRED), current_price=0.200, min_dynamic_price_difference=0.050, stored_energy=2000.0 Wh, ...
```

Peak shaving and solar limit keep their existing `[PeakShaving]` and `[SolarLimit]` log lines; their records are only added to the trace.

Some steps are added via `trace.step(...)` without a logger (peak shaving / solar limit skips, core.py's overrides) and are never logged individually. `core.py` logs the **complete** trace of every control cycle as one `DEBUG`-level block once it is final (`DecisionTrace.log_full_trace()`), so `loglevel: debug` always shows every step, including those. This duplicates the per-step lines above; that's the point of a DEBUG dump.

```
DEBUG Decision trace (3 steps):
[Rule] Discharge decision: forbidden (RESERVE_REQUIRED), ...
[Rule] Grid recharge decision: charge (GRID_RECHARGE_REQUIRED), ...
[Rule] Mode decision: force_charge (GRID_RECHARGE_REQUIRED), mode=-1, control_source=optimizer, decided_by=grid_recharge, value=2133
```

## Journal and status change listeners

Defined in `src/batcontrol/decision_journal.py`. `Batcontrol.decision_journal` holds the last 200 traces (in memory only, lost on restart).

```
journal = batcontrol.decision_journal

journal.latest()               # trace of the most recent decision
journal.last_status_change()   # why the inverter is in its current state
journal.last_event()           # same, or a newer refresh of it (see below)
journal.history(10)            # newest 10 traces, oldest first
```

Every mode change, whether it comes from the optimizer, the MQTT API, evcc or a fallback, ends up in the journal. Listeners are called on a **status change**:

| `kind`  | When                                                                         |
| ------- | ---------------------------------------------------------------------------- |
| `mode`  | The inverter mode changed (also once after start, with `previous_mode=None`) |
| `value` | The mode stayed the same, but its value changed by **25 % or more**          |

The value of a mode is the charge rate in force charge (`-1`) and the PV limit in the limit mode (`8`). The modes allow discharging (`10`) and avoid discharging (`0`) have no value and only produce `mode` events.

The change is measured against the value of the **last event**, not against the previous cycle. A charge rate that creeps up by 10 % per cycle (500, 550, 600, 650 W) therefore produces an event at 650 W. A change from or to 0 W always counts. The factor is `DEFAULT_VALUE_CHANGE_FACTOR` (0.25) and can be set via `DecisionJournal(value_change_factor=...)`.

```
def on_status_change(event):        # event: StatusChangeEvent
    print(event.kind, event.previous_mode, '->', event.mode, event.control_source)
    print(event.previous_value, '->', event.value)   # previous_value: kind 'value' only
    print(event.trace.status_text())
    payload = event.to_dict()       # JSON friendly, e.g. for a chat bot

batcontrol.decision_journal.add_listener(on_status_change)
```

What a listener does is up to the listener. The journal only guarantees:

- The listener gets the complete trace of the decision that caused the event.
- A failing listener is logged and never affects the control loop or other listeners.
- Listeners run **synchronously** in the thread that changed the mode (scheduler or MQTT thread). Slow work such as network calls must be handed over to a queue or a thread by the listener.

Cycles without a status change do not call listeners; they are still stored in the journal and available via `latest()` and `history()`.

### Refresh events

A status change keeps its trace, so the numbers in its explanation get old while the mode stays the same (for example the usable energy of `Discharge Allowed` over several hours), and a new reason with the same mode would not show up at all. A listener that shows the explanation registers with `add_listener(listener, refresh=True)` and additionally gets events of `kind == 'refresh'` with the trace of the latest cycle when

- the reason of the decisive step changed, or
- the last event (of any kind) is `DEFAULT_REFRESH_INTERVAL` (15 minutes) old.

The check runs in `commit()`, i.e. once per control cycle (every 3 minutes), so no timer is needed. Refresh events have `previous_mode=None` and `previous_value=None`, and they do not move the reference of the status change detection: `last_status_change()` and the 25 % comparison stay at the last `mode`/`value` event. `last_event()` returns the newest event including refreshes. Listeners registered without `refresh=True` never see refresh events.

### Built-in listener: Home Assistant "Decision" sensor

If MQTT is enabled, `MqttApi.publish_status_change` is registered as listener with `refresh=True`. It publishes the mode with its value and reason as text on `<base>/decision` (e.g. `Charge from Grid 1250 W - Grid recharge required`) and the trace as JSON on `<base>/decision/attributes`. Both are retained. Home Assistant discovers them as the **Decision** sensor, with the JSON as attributes. The content changes on status changes and refresh events, not on every evaluation, so it is at most 15 minutes old. The last event (`last_event()`) is published again in every evaluation, like the mode, so an event that happened while the broker was unreachable reaches the sensor afterwards. The attributes are published first, and the text only if that publish's return code reports success, so the text topic never advances ahead of the attributes topic. The return code of the text publish is checked too; if it fails, the mismatch (attributes ahead of text) cannot be undone at that point, so it is only logged as a warning.

## Adding a decision step

1. Add constants to `Decision`, `Outcome` and `Reason` if needed.
1. Add a `DecisionRecord` where the decision is made, with the numbers that explain it in `inputs`. Use `trace.add(record, logger)` to log it.
1. Mark it `decisive=True` only if it determines the control settings.
1. Add the unit of new numeric inputs to `_INPUT_FORMATS` in `decision_trace.py`, otherwise they are printed unformatted.
1. Add a plain-language entry for a new reason to `_REASON_EXPLANATIONS`, with `{input_key}` placeholders for the numbers that explain it (see "Plain-language explanations" above). Without one, the Decision sensor and `why` fall back to the raw reason code.
1. Keep reason strings ASCII-only.
1. If an input is a relative slot index or a list of them (0 = the current interval, not a clock time), add `interval_minutes` to the same record's inputs so a consumer can turn a slot into an actual time span. See `higher_price_slots`/`cheaper_price_slot` on the discharge rule and `high_price_slots`/`recharge_window_end` on the grid recharge decision.
