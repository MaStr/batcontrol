# Peak Shaving

## Why Peak Shaving?

Most PV systems produce peak power around midday. Without any intervention the battery charges as fast as possible and is full well before the afternoon. Once the battery is full, all surplus PV energy is fed into the grid -- often at the lowest grid prices of the day. For newer installations feed-in compensation may be very small or zero, so this exported energy is essentially wasted.

Peak shaving solves this by **limiting the PV-to-battery charge rate** so the battery fills gradually over the course of the day. The goal is to reach full capacity only by a configurable target hour (e.g. 14:00). This way the battery absorbs as much solar energy as possible and grid feed-in during midday peaks is minimised.

## Status

The algorithm is in the status "experimental", which is the reason why it is only available in the logic type `next`. After collecting enough experience with that feature, it will move into `default` eventually.

## Prerequisites

Peak shaving was introduced with 0.8.0 and is only available with the **`next` logic type**. Set this in the `battery_control` section of your configuration:

```yaml
battery_control:
  type: next   # Required -- 'default' does not include peak shaving
```

The `default` logic type does not support peak shaving at all. Enabling peak shaving without switching to `next` has no effect.

## Configuration

Add a `peak_shaving` block at the **top level** of your configuration file (not nested under `battery_control`):

```yaml
peak_shaving:
  enabled: false
  mode: combined               # 'time' | 'price' | 'combined'
  allow_full_battery_after: 14 # Hour (0-23) -- battery should be full by this hour
  price_limit: 0.05            # Euro/kWh -- slots at or below this price are "cheap"
```

### Parameter Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `false` | Master switch for peak shaving |
| `mode` | string | `combined` | Algorithm mode (see below) |
| `allow_full_battery_after` | int | `14` | Target hour (0-23) by which the battery should be full |
| `price_limit` | float | *none* | Price threshold in Euro/kWh. Required for modes `price` and `combined` |

### MQTT Runtime Control

All four parameters can be changed at runtime via MQTT without restarting batcontrol:

| Topic | Accepts | Description |
|-------|---------|-------------|
| `{base}/peak_shaving/enabled/set` | `true` / `false` | Enable or disable peak shaving |
| `{base}/peak_shaving/allow_full_battery_after/set` | int 0-23 | Change the target hour |
| `{base}/peak_shaving/mode/set` | `time` / `price` / `combined` | Change the algorithm mode |
| `{base}/peak_shaving/price_limit/set` | float | Change the price threshold in EUR/kWh; send `-1` to disable the price component |

Runtime changes are temporary and are not written back to the configuration file.

## The `allow_full_battery_after` Target Hour

This parameter controls when the battery is **allowed** to be 100% full:

- **Before this hour:** the PV charge rate may be limited (depending on mode).
- **At or after this hour:** no limit is applied -- the battery charges as fast as possible.

The target hour applies globally to **all three modes**. Set it to the hour by which your PV system typically produces enough to fill the battery. For many Central European systems `14` (2 PM) is a good starting point; adjust based on your panel orientation and local conditions.

## Modes

Peak shaving offers three modes that control which algorithm components are active:

### `time` -- Time-Based Only

Distributes the remaining free battery capacity evenly over the slots between now and `allow_full_battery_after`, using a **counter-linear ramp**. The allowed charge rate starts low and increases as the target hour approaches, which mirrors the typical PV generation curve that rises towards midday.

`price_limit` is **not required** for this mode.

**Formula:**

```
slots_remaining  = n  (slots until allow_full_battery_after)
pv_surplus       = sum of max(production - consumption, 0) per remaining slot

If pv_surplus > free_capacity:
    wh_current_slot = 2 * free_capacity / (n * (n + 1))
    charge_limit    = wh_current_slot / interval_hours
```

**Example** (free capacity = 2000 Wh, 1 h intervals):

| Hours to target | Allowed charge rate |
|-----------------|---------------------|
| 8 | 55 W |
| 4 | 200 W |
| 2 | 666 W |
| 1 | 2000 W (full rate) |

If the expected PV surplus does not exceed the free capacity, no limit is applied -- the battery can absorb everything anyway.

### `price` -- Price-Based Only

Reserves free battery capacity for upcoming **cheap-price** slots where PV is still producing. A slot is "cheap" when its price is at or below `price_limit`.

`price_limit` is **required** for this mode.

Only slots within the **production window** are considered. The production window ends at the first forecast slot where PV production is zero. This prevents reserving capacity for a cheap slot at e.g. 03:00 that would never produce any solar energy.

**Before the cheap window:**
1. Sum the expected PV surplus during cheap slots to get the target reserve.
2. Calculate how much additional charging is allowed: `additional_allowed = free_capacity - target_reserve`.
3. If `additional_allowed <= 0`: block PV charging entirely (rate = 0).
4. Otherwise: spread `additional_allowed` evenly over the slots before the cheap window.

**Inside the cheap window:**
- If total PV surplus during cheap slots exceeds free capacity, spread `free_capacity` evenly over cheap slots so the battery fills gradually.
- If surplus fits in free capacity, no limit is applied.

### `combined` -- Both Active (Default)

Both the time-based and price-based components run in parallel. The **stricter (lower non-negative) limit wins**. This is the most conservative and generally recommended mode.

`price_limit` is **required** for the price component. If `price_limit` is not set, the price component is disabled and `combined` falls back to **time-only** behaviour — batcontrol logs a warning at startup in this case. Set a numeric `price_limit` or change the mode to `time` to silence the warning.

## Charge Limit and Minimum Charge Rate

The calculated charge limit is applied via **Mode 8** (`LIMIT_BATTERY_CHARGE_RATE`). In this mode the inverter caps PV-to-battery charging at the given wattage while still allowing the battery to discharge normally.

A minimum charge rate of **500 W** is enforced: any computed limit between 1 W and 499 W is raised to 500 W to avoid inefficient low-power charging. A limit of exactly **0 W** (block charging completely) is kept as-is and is not raised.

The charge limit is published via MQTT:

| Topic | Type | Retained | Description |
|-------|------|----------|-------------|
| `{base}/peak_shaving/charge_limit` | int | No | Current charge limit in W (-1 = inactive / no limit) |

## When Peak Shaving is Skipped

Peak shaving is automatically bypassed in the following situations:

| Condition | Reason |
|-----------|--------|
| No PV production (nighttime) | Nothing to limit |
| Past `allow_full_battery_after` hour | Target reached, charge freely |
| Battery in `always_allow_discharge` region (high SOC) | Battery is nearly full anyway |
| Force-charge from grid active (Mode -1) | Grid charging takes priority |
| Discharge not allowed | Battery is being preserved for expensive hours -- limiting PV would be counterproductive |
| evcc is actively charging the EV | The EV already consumes excess PV |
| EV connected in PV mode (evcc) | evcc will absorb surplus PV when its threshold is reached |
| `price_limit` not configured | Price component cannot operate; `combined` falls back to time-only, `price` is effectively inactive |

## evcc Interaction

When an EV charger is managed by [evcc](../integrations/evcc-connection.md):

- **EV actively charging** (`charging=true`): peak shaving is disabled because the EV is already consuming excess PV energy.
- **EV connected in PV mode** (`connected=true` AND `mode=pv`): peak shaving is disabled because evcc will naturally absorb surplus PV once its threshold is reached.
- **EV disconnects or mode changes**: peak shaving is automatically re-enabled.

## Home Assistant Auto-Discovery

When MQTT auto-discovery is enabled, the following Home Assistant entities are created automatically:

| Entity | Type | Description |
|--------|------|-------------|
| Peak Shaving Enabled | Switch | Enable/disable peak shaving |
| Peak Shaving Allow Full After | Number (0-23) | Set the target hour |
| Peak Shaving Charge Limit | Sensor (W) | Current calculated charge limit |

## Self-Correction

The charge limit is recalculated every evaluation cycle (typically every 3 minutes). If clouds reduce PV production significantly, the free capacity stays higher at the next cycle and the counter-linear ramp automatically produces a higher allowed rate. This means the system self-corrects without manual intervention, though there is no intra-cycle adjustment.

## Quick-Start Examples

**Simple time-based setup** -- spread charging until 14:00, no price awareness:

```yaml
battery_control:
  type: next

peak_shaving:
  enabled: true
  mode: time
  allow_full_battery_after: 14
```

**Price-aware combined setup** -- reserve capacity for cheap slots below 5 ct/kWh:

```yaml
battery_control:
  type: next

peak_shaving:
  enabled: true
  mode: combined
  allow_full_battery_after: 14
  price_limit: 0.05
```
