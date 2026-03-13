# Peak Shaving

## Overview

Peak shaving manages PV battery charging rate so the battery fills up gradually, reaching full capacity by a configurable target hour (`allow_full_battery_after`). This prevents the battery from being full too early in the day.

**Problem:** All PV systems produce peak power around midday. Most batteries are full by then, causing excess PV to be fed into the grid at a time when grid prices are lowest — and for newer installations, feed-in may not be compensated at all. Peak shaving spreads battery charging over time so the system absorbs as much solar energy as possible.

## Configuration

### Enable Peak Shaving

Peak shaving requires two configuration changes:

1. Set the logic type to `next` in the `battery_control` section:

```yaml
battery_control:
  type: next   # Use 'next' to enable peak shaving logic (default: 'default')
```

2. Configure the `peak_shaving` section:

```yaml
peak_shaving:
  enabled: false
  allow_full_battery_after: 14   # Hour (0-23) — battery should be full by this hour
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `false` | Enable/disable peak shaving |
| `allow_full_battery_after` | int | `14` | Target hour (0-23) for the battery to be full |

**`allow_full_battery_after`** controls when the battery is allowed to be 100% full:
- **Before this hour:** PV charge rate is limited to spread charging evenly
- **At/after this hour:** No PV charge limit, battery is allowed to reach full charge

## How It Works

### Algorithm

The algorithm calculates the expected PV surplus (production minus consumption) for all time slots until the target hour. If the expected surplus would fill the battery before the target hour, it calculates a charge rate limit:

```
slots_remaining = slots from now until allow_full_battery_after
free_capacity = battery free capacity in Wh
pv_surplus = sum of max(production - consumption, 0) for remaining slots

if pv_surplus > free_capacity:
    charge_limit = free_capacity / slots_remaining  (Wh per slot, converted to W)
```

The charge limit is applied using **MODE 8** (`limit_battery_charge_rate`), which limits PV charging while still allowing battery discharge.

### Skip Conditions

Peak shaving is automatically skipped when:

1. **No PV production** — nighttime, no action needed
2. **Past the target hour** — battery is allowed to be full
3. **Battery in always_allow_discharge region** — SOC is already high
4. **Grid charging active (MODE -1)** — force charge takes priority
5. **EVCC is actively charging** — EV consumes the excess PV
6. **EV connected in PV mode** — EVCC will absorb PV surplus

### EVCC Interaction

When an EV charger is managed by EVCC:

- **EV actively charging** (`charging=true`): Peak shaving is disabled — the EV consumes the excess PV
- **EV connected in PV mode** (`connected=true` AND `mode=pv`): Peak shaving is disabled — EVCC will naturally absorb surplus PV when the threshold is reached
- **EV disconnects or mode changes**: Peak shaving is re-enabled

The EVCC integration derives `mode` and `connected` topics automatically from the configured `loadpoint_topic` by replacing `/charging` with `/mode` and `/connected`.

## MQTT API

### Published Topics

| Topic | Type | Retained | Description |
|-------|------|----------|-------------|
| `{base}/peak_shaving/enabled` | bool | Yes | Peak shaving enabled status |
| `{base}/peak_shaving/allow_full_battery_after` | int | Yes | Target hour (0-23) |
| `{base}/peak_shaving/charge_limit` | int | No | Current charge limit in W (-1 if inactive) |

### Settable Topics

| Topic | Accepts | Description |
|-------|---------|-------------|
| `{base}/peak_shaving/enabled/set` | `true`/`false` | Enable/disable peak shaving |
| `{base}/peak_shaving/allow_full_battery_after/set` | int 0-23 | Set target hour |

### Home Assistant Auto-Discovery

The following HA entities are automatically created:

- **Peak Shaving Enabled** — switch entity
- **Peak Shaving Allow Full After** — number entity (0-23, step 1)
- **Peak Shaving Charge Limit** — sensor entity (unit: W)

## Known Limitations

1. **Flat charge distribution:** The charge rate limit is uniform across all time slots, but PV production peaks at midday. The battery may not reach exactly 100% by the target hour.

2. **No intra-day adjustment:** If clouds reduce PV significantly, the limit stays as calculated until the next evaluation cycle (every 3 minutes). The system self-corrects because free capacity stays high, which increases the allowed charge rate.

3. **Code duplication:** `NextLogic` is a copy of `DefaultLogic` with peak shaving added. Once stable, the two could be merged or refactored.
