# Forecast Metrics

`ForecastMetrics` is a stateless module that derives three battery indicators
from the production/consumption forecast arrays and the current battery state.
These indicators are published via MQTT and are intended to drive downstream
automation decisions such as "should I run the heat pump now or save the battery
for tomorrow's solar charge?"

All three values are updated once per evaluation cycle (every 3 minutes by
default). They are based on the same forecast window that the main optimizer
uses, so their horizon is `min(max available price hours, max available solar
hours)`.

## The Three Metrics

### `solar_surplus_wh` — Current-Window Solar Overflow

**MQTT topic:** `{base}/solar_surplus_wh`

Expected energy in Wh that the solar production in the **current production
window** will generate above what the battery can absorb.

- **solar_active = true (slot 0 is producing):** surplus is the net solar
  production of the ongoing window minus the remaining free battery capacity.
  `surplus > 0` means that PV power will be exported to the grid even if the
  battery is managed optimally.
- **solar_active = false (nighttime or break before next window):** surplus
  accounts for the overnight discharge first — the battery will self-discharge
  from consumption before solar starts, which creates room. `surplus > 0` means
  even after that extra room is created the next solar window will still
  overflow.

A value of `0` means the battery can absorb everything the upcoming solar
window produces. A value `> 0` means some PV will inevitably be exported; it
is safe to run flexible loads (heat pump, EV charging) from the grid right now
because the solar surplus will offset them.

**Use case:** If `solar_surplus_wh >= estimated_load_wh`, running a flexible
load (heat pump, EV charging via evcc) has no net grid cost over the forecast
horizon. See [Use Cases](#use-cases) for concrete automation examples.

---

### `pv_start_battery_wh` — Battery Level at Next Charging Point

**MQTT topic:** `{base}/pv_start_battery_wh`

Battery level in Wh (above MIN_SOC) at the moment when solar production first
exceeds household consumption (`net_consumption < 0`). This is the point where
the battery transitions from discharging to charging.

- Simulated slot-by-slot from the current moment forward.
- If the battery hits `0` (MIN_SOC) before solar starts, the value is `0`.
- If the battery has no net-charging slot in the forecast at all, the value
  is `0`.
- If slot 0 is already a net-charging slot (solar already exceeds
  consumption), the value equals the current stored usable energy.

**Use case:** "How much charge will the battery have left when solar starts
tomorrow?" A low value (e.g. < 500 Wh) means flexible loads tonight should
be reduced; a high value means overnight loads can run freely. See
[Use Cases](#use-cases) for heat pump and evcc automation examples.

!!! note
    `pv_start_battery_wh` depends on `net_consumption < 0`, not just on
    `production > 0`. The battery does not switch to charging until solar output
    exceeds household consumption — on a partly-cloudy morning the cross-over can
    happen later than sunrise.

---

### `forecast_min_battery_wh` — Forecast Minimum Battery Level

**MQTT topic:** `{base}/forecast_min_battery_wh`

The lowest battery level in Wh (above MIN_SOC) reached at any point during the
entire forecast horizon, based on slot-by-slot simulation with proper
floor/ceiling clamping.

- A value of `0` means the battery is expected to hit MIN_SOC at some point in
  the forecast — a signal that the system will be energy-constrained.
- The simulation respects both the floor (MIN_SOC = 0 usable Wh) and the
  ceiling (MAX_SOC = stored_usable + free_capacity), so multi-day
  charge/discharge cycles are tracked correctly.
- The horizon covers the full forecast window (same as the optimizer), not
  just the next 24 hours.

**Use case:** "Will the battery run out at any point in the planning horizon?"
`forecast_min_battery_wh == 0` means batcontrol may need to grid-charge later —
block or reduce flexible loads. A value comfortably above zero means the battery
has buffer and loads can run. See [Use Cases](#use-cases).

---

## Decision Matrix

The three metrics form a natural 2-D decision space for flexible load control:

| `solar_surplus_wh` | `forecast_min_battery_wh` | Recommended action |
|--------------------|--------------------------|-------------------|
| > 0 | > 0 | Run flexible loads freely — PV will cover them and battery stays healthy |
| > 0 | = 0 | PV surplus exists but battery will be short later — run light loads only |
| = 0 | > 0 | No surplus but battery OK — use `pv_start_battery_wh` to judge night loads |
| = 0 | = 0 | Constrained — block flexible loads, preserve battery |

`pv_start_battery_wh` refines the third row: if it is high, the overnight
discharge is gentle and a moderate flexible load (e.g. heat pump one cycle)
is fine. If it is near zero, defer to the next solar window.

---

## MQTT Topics

| Topic | Unit | Retained | Description |
|-------|------|----------|-------------|
| `{base}/solar_surplus_wh` | Wh | No | PV overflow that cannot be stored in the battery |
| `{base}/solar_active` | bool | No | `true` if solar is producing in slot 0 |
| `{base}/pv_start_battery_wh` | Wh | No | Battery level at next net-charging crossover |
| `{base}/forecast_min_battery_wh` | Wh | No | Minimum battery level over entire forecast horizon |

All values are published after each evaluation cycle (together with the
inverter control decision). See [MQTT API](mqtt-api.md) for the full topic
reference and configuration options.

### Home Assistant Auto-Discovery

The following HA entities are created automatically when
`auto_discover_enable: true` is configured:

- **Solar Surplus** — sensor (energy, Wh)
- **Solar Active** — binary sensor (on/off diagnostic)
- **PV Start Battery** — sensor (energy, Wh)
- **Forecast Min Battery** — sensor (energy, Wh)

---

## Use Cases

### Heat Pump Control

A heat pump is a flexible load: it can pre-heat a buffer tank or run an extra
heating cycle when energy is cheap or free — but running it at the wrong time
can deplete the battery before the next solar window.

The three metrics together answer the key questions for heat pump automation:

**"Can I run a heating cycle right now without net grid cost?"**

Check `solar_surplus_wh >= estimated_cycle_wh`. If yes, the PV will produce
more than the battery can store anyway — running the heat pump consumes what
would otherwise be exported. No additional grid draw over the forecast horizon.

**"Is the battery safe enough for a cycle tonight?"**

Check `pv_start_battery_wh`. A high value (e.g. > 2000 Wh) means the battery
will still have a comfortable charge when solar starts tomorrow; the heat pump
can run. A low value (< 500 Wh) means overnight consumption will nearly deplete
the battery — defer to the next solar window.

**"Should I block flexible loads entirely?"**

Check `forecast_min_battery_wh == 0`. If the battery is expected to hit MIN_SOC
at some point in the forecast, batcontrol may need to grid-charge later;
flexible loads should wait.

A simple Home Assistant automation combining all three:

```yaml
# Allow heat pump if PV will overflow OR battery is healthy and not forecast-constrained
condition:
  - condition: or
    conditions:
      - condition: numeric_state
        entity_id: sensor.batcontrol_solar_surplus_wh
        above: 1500          # surplus covers one heat pump cycle
      - condition: and
        conditions:
          - condition: numeric_state
            entity_id: sensor.batcontrol_pv_start_battery_wh
            above: 1000      # enough battery charge at dawn
          - condition: numeric_state
            entity_id: sensor.batcontrol_forecast_min_battery_wh
            above: 0         # battery not forecast to run empty
```

---

### EV Charging via evcc

[evcc](https://evcc.io) supports a `min_soc`/`target_soc` model as well as
charging from PV surplus. The batcontrol metrics integrate naturally with evcc's
MQTT API to let you charge the car only when it does not compete with the
battery.

**Scenario: charge only from true PV overflow**

`solar_surplus_wh` tells you exactly how much energy the PV will produce above
what the battery can absorb. Pass this value to evcc's `pv_action` or use it
in an automation to set the evcc charging mode:

- `solar_surplus_wh > 0` → switch evcc to **PV** mode (charge from surplus)
- `solar_surplus_wh == 0` and `forecast_min_battery_wh > 0` → switch to **Min+PV**
  (keep a minimum charge rate, fill up with PV where possible)
- `forecast_min_battery_wh == 0` → switch to **Off** or **Min** only (battery
  needs the energy)

**Scenario: opportunistic overnight charge**

Use `pv_start_battery_wh` to decide whether to allow evcc to draw from the
battery overnight. If the battery is forecast to still be above a threshold at
dawn, a slow overnight charge (e.g. 6 A / 1.4 kW) will not noticeably affect
the next day's solar cycle.

---

### General Pattern for Any Flexible Load

| Question | Metric to check | Threshold example |
|---|---|---|
| Is PV overflowing right now? | `solar_surplus_wh` | `> estimated_load_wh` |
| Will battery survive the night? | `pv_start_battery_wh` | `> 500 Wh` |
| Is the battery forecast-constrained? | `forecast_min_battery_wh` | `> 0` |

All three are dimensioned in Wh, so you can directly compare them against the
energy consumption of the load you want to schedule.

---

## Implementation Notes

- All values are computed in `src/batcontrol/forecast_metrics.py` by the
  `ForecastMetrics` class.
- Slot 0 is time-adjusted: the elapsed fraction of the current interval is
  subtracted so that a slot already 80% elapsed only contributes 20% of its
  forecast energy.
- `net_consumption = consumption - production`; negative = battery charging,
  positive = battery discharging / grid draw.
- `stored_usable_energy` is the energy above MIN_SOC. `free_capacity` is the
  space between the current level and MAX_SOC.
- The slot-by-slot simulation clamps at both ends:
  `battery = max(0, min(stored_usable + free_capacity, battery - net))`.
  A simple net-sum over slots would overestimate available energy because it
  ignores that the battery cannot go below 0 or above MAX_SOC.
