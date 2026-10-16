# Dashboard fixtures

Frozen API responses for `npm run dev:fixtures`, so the dashboard can be developed and reviewed
without a running batcontrol - and, more importantly, in states a live system will not produce
on request.

All of them were captured from a real instance and then mutated, so their shape is the API's.
**Only the fields the scenario is about are changed**: the decision trace in `battery-low.json`,
for example, still describes the captured decision and will not match the mutated battery. That
is deliberate - these exercise the rendering, not the control logic.

| Scenario | What it is for |
|---|---|
| `default` | The ordinary case. Also the baseline the others are derived from. |
| `battery-full` | SoC exactly 100 % with max_soc 100: every cell lit, none blinking, no free capacity. |
| `battery-low` | SoC 3 % in FORCE CHARGE: the amber mode, a charge rate in the header, lock dots below MIN_SOC. |
| `reserved-over-capacity` | A reservation larger than the battery - it has to be capped at `always_allow_discharge` before the hatching is drawn. |
| `no-data` | Before the first control cycle: 503 on `current` and the forecast, 404 on the decision. |
| `offline` | A control loop that has missed several evaluations. |
| `unauthorized` | 401 everywhere, for the token prompt. |

The derived scenarios store only their differences: `"$extends": "default"` plus the changed
fields, deep-merged by `fixtures/load.js` (objects merge, arrays and scalars replace). A contract
change therefore only has to be made in `default.json`.

Timestamps are rebased onto the current time per request, so relative displays stay meaningful
however old the file is. Forecast slots are shifted by whole slots, so slot boundaries stay on
the interval - otherwise an hourly forecast renders as "MIN 24h @ 13:04" and sends you looking
for a bug that is not there.

## Updating them

```bash
python -m batcontrol --config <a config with api.enabled: true>   # dummy inverter is fine
# then capture the envelopes from /api/v1/{status,battery,current,version,forecast/price,decision}
```

`src/lib/api.test.js` runs `normalize()` against these files, so a change to the API contract
shows up as a failing test rather than as a dashboard that quietly renders nothing.
