# Forecast Metrics

## Overview

Batcontrol derives a set of **forecast metrics** from the production/consumption
forecast arrays and the current battery state. These indicators are published
via [MQTT](../integrations/mqtt-api.md) and are intended to drive downstream
automation decisions such as *"should I run the heat pump now, or save the
battery for tomorrow's solar charge?"*

Batcontrol itself does not act on these values -- they exist so that **external
automations** (Home Assistant, Node-RED, evcc scripts, ...) can make smarter
decisions about flexible loads without re-implementing the forecast simulation.

All values are updated once per evaluation cycle (every 3 minutes by default)
and are based on the same forecast window that the main optimizer uses, so
their horizon is the shortest of the available forecasts (prices, solar
production, consumption).

The metrics are computed by the `ForecastMetrics` class in
`src/batcontrol/forecast_metrics.py`.

## The Metrics

### `solar_active` -- Solar Currently Producing

**MQTT topic:** `{base}/solar_active`

Boolean flag: `true` if solar production is greater than zero in the current
time slot. Useful as a cheap day/night discriminator for automations that
should only react during (or outside of) production hours.

### `solar_surplus_wh` -- Expected Solar Overflow

**MQTT topic:** `{base}/solar_surplus_wh`

Expected energy in Wh that the solar production of the **next (or ongoing)
production window** will generate above what the battery can absorb.

- **`solar_active = true` (slot 0 is producing):** surplus is the net solar
  production of the ongoing window minus the remaining free battery capacity.
- **`solar_active = false` (nighttime or a break before the next window):**
  the overnight discharge is accounted for first -- consumption will drain the
  battery before solar restarts, which creates additional room. `surplus > 0`
  means even after that extra room is created the next solar window will still
  overflow.

A value of `0` means the battery can absorb everything the upcoming solar
window produces. A value `> 0` means some PV will inevitably be exported to
the grid; running flexible loads now is "free" in terms of battery state,
because the energy they consume would have been exported anyway.

### `pv_start_battery_wh` -- Battery Level at the Next Charging Point

**MQTT topic:** `{base}/pv_start_battery_wh`

Battery level in Wh (above MIN_SOC) at the moment when solar production first
**exceeds** household consumption (`net_consumption < 0`). That crossover is
the point where the battery transitions from discharging to charging, which
makes it the most meaningful reference for overnight planning.

- Simulated slot-by-slot from the current moment forward.
- `0` if the battery hits MIN_SOC before solar takes over.
- `0` if no net-charging slot exists in the forecast at all.
- If slot 0 is already a net-charging slot (solar already exceeds
  consumption), the value equals the current stored usable energy.

!!! note "Net-charging, not sunrise"
    `pv_start_battery_wh` depends on `net_consumption < 0`, not just on
    `production > 0`. The battery does not start charging at sunrise -- it
    starts charging when solar output exceeds household consumption. On a
    partly-cloudy morning that crossover can happen hours after sunrise.

### `forecast_min_battery_wh` -- Forecast Minimum Battery Level

**MQTT topic:** `{base}/forecast_min_battery_wh`

The lowest battery level in Wh (above MIN_SOC) reached at **any** point during
the entire forecast horizon, based on a slot-by-slot simulation with proper
floor/ceiling clamping.

- `0` means the battery is expected to hit MIN_SOC at some point in the
  forecast -- the system will be energy-constrained.
- The simulation respects both the floor (MIN_SOC = 0 usable Wh) and the
  ceiling (MAX_SOC = stored usable energy + free capacity), so multi-day
  charge/discharge cycles are tracked correctly. A simple net-sum over the
  slots would overestimate the available energy because it ignores that the
  battery can neither go below MIN_SOC nor above MAX_SOC.
- The horizon covers the full forecast window (same as the optimizer), not
  just the next 24 hours.

## Use Cases

The metrics answer three different questions about the *future* battery state.
Picking the right one for an automation matters:

| Question | Metric |
|----------|--------|
| "Can I run a load right now without losing stored energy?" | `solar_surplus_wh` |
| "How much charge is left when the battery starts refilling?" | `pv_start_battery_wh` |
| "Will the battery run empty at any point in the planning horizon?" | `forecast_min_battery_wh` |

### Use case 1: Heat pump / hot water on PV surplus

*"Is running the heat pump now free in terms of grid cost?"*

If `solar_surplus_wh >= estimated_heat_pump_wh`, the heat pump can run without
net additional grid draw over the forecast horizon -- the energy it consumes
would otherwise have been exported. Because the nighttime variant of the
calculation already accounts for the overnight bridge discharge, this also
works for "pre-heat in the early morning before a sunny day" scenarios.

Example Home Assistant template condition:

```yaml
condition:
  - condition: numeric_state
    entity_id: sensor.batcontrol_solar_surplus
    above: 2000   # expected heat pump consumption in Wh
```

### Use case 2: EV charging beyond evcc's PV mode

evcc reacts to *current* surplus power. `solar_surplus_wh` adds the *forecast*
dimension: if a large surplus is expected for the rest of the production
window, charging can be started earlier (or with more than the minimum
current) without sacrificing battery charge. Conversely, `solar_surplus_wh ==
0` tells the automation that every Wh sent to the car competes directly with
the home battery.

### Use case 3: Overnight flexible loads (dishwasher, washing machine)

*"Can I run the dishwasher tonight from the battery, or will that leave the
house on grid power before sunrise?"*

`pv_start_battery_wh` is the projected battery level at the moment the battery
starts charging again. A comfortable value (e.g. `> 1500 Wh`) means overnight
consumption will not deplete the battery and the load can run from stored
energy. A value near `0` means the battery will be flat before solar takes
over -- the load either waits for the next solar window or knowingly runs on
grid power (which may still be fine in a cheap price slot).

### Use case 4: Shortage guard for multi-day planning

*"Is the system energy-constrained anywhere in the planning horizon?"*

`forecast_min_battery_wh == 0` signals that batcontrol expects the battery to
hit MIN_SOC at some point -- typically before a cloudy day. Automations should
be conservative with flexible loads, and pre-heating/pre-cooling strategies
can shift consumption into cheap or surplus slots instead. If the value is
comfortably above zero there is a buffer across the whole horizon and flexible
loads can run freely.

### Decision matrix

The metrics combine into a simple decision space for flexible load control:

| `solar_surplus_wh` | `forecast_min_battery_wh` | Recommended action |
|--------------------|---------------------------|--------------------|
| `> 0` | `> 0` | Run flexible loads freely -- PV covers them and the battery stays healthy |
| `> 0` | `= 0` | PV surplus exists but the battery will be short later -- run light loads only |
| `= 0` | `> 0` | No surplus, but the battery is OK -- use `pv_start_battery_wh` to judge night loads |
| `= 0` | `= 0` | Constrained -- block flexible loads, preserve the battery |

`pv_start_battery_wh` refines the third row: if it is high, the overnight
discharge is gentle and a moderate flexible load (e.g. one heat pump cycle)
is fine. If it is near zero, defer the load to the next solar window.

## MQTT Topics

| Topic | Unit | Retained | Description |
|-------|------|----------|-------------|
| `{base}/solar_active` | bool | No | `true` if solar is producing in the current slot |
| `{base}/solar_surplus_wh` | Wh | No | Expected PV overflow that cannot be stored in the battery |
| `{base}/pv_start_battery_wh` | Wh | No | Battery level at the next net-charging crossover |
| `{base}/forecast_min_battery_wh` | Wh | No | Minimum battery level over the entire forecast horizon |

All values are published after each evaluation cycle, together with the
inverter control decision.

### Home Assistant Auto-Discovery

The following entities are created automatically when
`auto_discover_enable: true` is configured (see
[MQTT API](../integrations/mqtt-api.md#home-assistant-auto-discovery)):

- **Solar Surplus** -- sensor (energy, Wh)
- **Solar Active** -- binary sensor (diagnostic)
- **PV Start Battery** -- sensor (energy, Wh)
- **Forecast Min Battery** -- sensor (energy, Wh)

!!! info "Migration from `night_surplus_wh`"
    Earlier development versions published a single `night_surplus_wh` topic.
    It was replaced by the two clearer metrics `pv_start_battery_wh` (its
    direct successor) and `forecast_min_battery_wh`. The retained Home
    Assistant discovery entry for the old sensor is cleaned up automatically.

## Implementation Notes

- All values are computed in `src/batcontrol/forecast_metrics.py` by the
  stateless `ForecastMetrics` class.
- Forecast arrays contain energy per interval (Wh per slot); see
  [15-Minute Interval Transformation](../development/15-min-transform.md).
- Slot 0 is time-adjusted: the elapsed fraction of the current interval is
  subtracted, so a slot that is already 80% elapsed only contributes 20% of
  its forecast energy.
- `net_consumption = consumption - production`; negative values mean the
  battery is charging (or energy is fed in), positive values mean the battery
  is discharging (or energy is drawn from the grid).
- `stored_usable_energy` is the energy above MIN_SOC; `free_capacity` is the
  space between the current level and MAX_SOC.
- The slot-by-slot simulation clamps at both ends:
  `battery = max(0, min(stored_usable + free_capacity, battery - net))`.
