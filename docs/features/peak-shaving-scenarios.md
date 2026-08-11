# Peak Shaving Scenarios

This page walks one example day through every combination of the three peak
shaving rules, so you can see how batcontrol would actually behave before
enabling anything. See [Peak Shaving](peak-shaving.md) for the parameters and
the algorithms themselves.

Every chart is generated from the shipped implementation --
`scripts/generate_peak_shaving_csv.py` runs the real
`_apply_peak_shaving` and `_apply_solar_limit` steps over the example day and
writes the result as CSV. The charts cannot drift away from the code.

## The example day

| Property | Value |
|----------|-------|
| Resolution | 15 minutes (96 slots) |
| Battery | 10 kWh, 20 % at midnight |
| PV | clear day, peak 5000 W at 13:00 |
| House load | 400 W constant |
| Feed-in limit | 4000 W (`feed_in_limit_w`), headroom 1.0 |
| Cheap price window | 11:00 - 14:00 at 0.03 EUR/kWh (`price_limit` 0.05) |
| Other prices | 0.28 EUR/kWh, evening peak 0.42 EUR/kWh |
| Target hour | 14:00 (`allow_full_battery_after`) |

Because PV peaks at 5000 W against a 400 W load, the surplus reaches 4600 W --
600 W above the feed-in limit. Everything above the limit is curtailed unless
the battery absorbs it.

!!! note "What the simulation does and does not cover"
    The charts isolate the peak shaving post-processing: the upstream
    discharge decision is stipulated as "discharge allowed, no grid
    charging". In a real run the evening price peak of 0.42 EUR/kWh can make
    batcontrol withhold the battery for expensive hours, which skips the time
    and price rules entirely. See
    [When Peak Shaving is Skipped](peak-shaving.md#when-peak-shaving-is-skipped).

## Reading the charts

| Series | Meaning |
|--------|---------|
| PV surplus (W) | production minus house load |
| Applied charge limit (W) | what batcontrol sets via Mode 8; gaps mean "no limit" |
| Battery charge (W) | what the battery actually absorbs |
| Curtailed (W) | surplus above the feed-in limit that nobody took -- lost energy |
| SoC (%) | battery state of charge, right axis |
| SoC without peak shaving (%) | the baseline run, for comparison |

## Baseline: no peak shaving

The battery charges as fast as the PV allows and is full at 11:45 -- well
before the production peak. From then on everything above the feed-in limit is
curtailed.

<div class="ps-chart" data-scenario="baseline"></div>

## Single rules

### Time based only

`time_active: true`

The counter-linear ramp spreads the remaining free capacity over the slots
until `allow_full_battery_after`. The limit starts at the 500 W minimum charge
rate and rises steeply as 14:00 approaches, because the per-slot allocation is
`2 * free_capacity / (n * (n + 1))` with `n` shrinking. The battery reaches
full exactly around the target hour instead of at lunchtime.

<div class="ps-chart" data-scenario="time"></div>

### Price based only

`price_active: true`, `price_limit: 0.05`

Until 11:00 the rule returns **0** -- a hard block. The PV surplus expected
during the cheap window exceeds the free capacity, so no charging at all is
allowed beforehand and the whole battery is kept for the cheap hours. At 11:00
the cheap window opens and the reserved capacity is spread evenly over the
remaining cheap slots.

Note the difference to the time rule: the price rule distributes **flat**
(`free_capacity / number of cheap slots`), not as a rising ramp. It is
recomputed every cycle, so the value still drifts upward as cheap slots are
used up and free capacity shrinks.

<div class="ps-chart" data-scenario="price"></div>

### Solar cap only

`solar_cap_active: true`, `feed_in_limit_w: 4000`

Two phases are visible. Before the clip window the rule only reserves capacity
(`free_capacity - predicted clip energy`, spread over the time until clipping
starts) -- the battery charges nearly unrestricted. From 11:45 the surplus
exceeds the feed-in limit and the rule switches to a **floor**: a minimum
charge rate that tracks the excess over 4000 W, peaking at 600 W at 13:00.

<div class="ps-chart" data-scenario="solar"></div>

!!! warning "This rule alone curtails more than the time rule here"
    Look at the applied limit between 11:45 and 12:15: the floor asks for
    54 / 243 / 396 W, but the limit sits at 500 W. The
    [minimum charge rate](peak-shaving.md#interaction-with-the-minimum-charge-rate)
    overrides the floor, the battery charges about 202 Wh more than intended
    and is full at 13:30 -- right before the peak. The red curtailment band
    after 13:30 is exactly that energy coming back.

    Combined with the time rule the battery still has room when clipping
    starts and the day ends at zero curtailment. Tracked in
    [issue #409](https://github.com/MaStr/batcontrol/issues/409).

## Combinations

When several rules are active the caps compete and the strictest wins, but the
solar floor overrides all of them:

```
final_limit = max(solar_floor, min(all_caps))
```

### Time + Price

<div class="ps-chart" data-scenario="time_price"></div>

The price block dominates the morning, then the time ramp takes over inside the
cheap window because it is the stricter of the two.

### Time + Solar cap

<div class="ps-chart" data-scenario="time_solar"></div>

The time ramp holds the battery back through the morning, so the full free
capacity is still available when clipping starts. This combination reaches zero
curtailment.

### Price + Solar cap

<div class="ps-chart" data-scenario="price_solar"></div>

### All three rules

<div class="ps-chart" data-scenario="all"></div>

From 12:30 both the time and the price rule switch off -- the remaining surplus
no longer exceeds the free capacity, so neither needs to cap anything. The
solar floor keeps working past the 14:00 target hour, which is why the battery
only reaches 100 % in the late afternoon.

## Comparison

<div class="ps-summary"></div>

The trade-off is visible in the last two columns: peak shaving does not charge
*more* energy into the battery, it charges the *same* energy *later*, which
keeps room free for the hours when energy would otherwise be lost.

## Changing the example day

The charts are driven by two committed input files:

| File | Content |
|------|---------|
| `scripts/data/peak_shaving_example_day.csv` | the time series: `time`, `pv_w`, `consumption_w`, `price` |
| `scripts/data/peak_shaving_example_day.yaml` | battery, rule parameters and the list of configurations |

Edit those to change the scenario, then regenerate:

```bash
python scripts/generate_peak_shaving_csv.py
```

The generated datasets under `docs/assets/data/peak_shaving/` are **build
output and not committed** -- the documentation build produces them before
`mkdocs build`, so the published charts always match the current
implementation. Run the command once before `mkdocs serve` to preview the
charts locally, otherwise they will report missing data.

Adding a configuration to the YAML creates a new dataset automatically; embed
it in a page with `<div class="ps-chart" data-scenario="NAME"></div>`.
