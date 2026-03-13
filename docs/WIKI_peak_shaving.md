# Peak Shaving

## Overview

Peak shaving manages PV battery charging rate so the battery fills up gradually, reaching full capacity by a configurable target hour (`allow_full_battery_after`). This prevents the battery from being full too early in the day.

**Problem:** All PV systems produce peak power around midday. Most batteries are full by then, causing excess PV to be fed into the grid at a time when grid prices are lowest - and for newer installations, feed-in may not be compensated at all. Peak shaving spreads battery charging over time so the system absorbs as much solar energy as possible.

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
  mode: combined               # 'time' | 'price' | 'combined'
  allow_full_battery_after: 14   # Hour (0-23) - battery should be full by this hour
  price_limit: 0.05            # Euro/kWh - keep battery empty for slots at or below this price
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | bool | `false` | Enable/disable peak shaving |
| `mode` | string | `combined` | Algorithm mode: `time`, `price`, or `combined` |
| `allow_full_battery_after` | int | `14` | Target hour (0-23) for the battery to be full |
| `price_limit` | float | `null` | Price threshold (Euro/kWh); required for modes `price` and `combined` |

**`mode`** selects which algorithm components are active:
- **`time`** - time-based only: spread free capacity evenly until `allow_full_battery_after`. `price_limit` not required.
- **`price`** - price-based only: reserve capacity for cheap-price slots (in-window surplus overflow handled). Requires `price_limit`.
- **`combined`** (default) - both components; stricter limit wins. Requires `price_limit`.

**`allow_full_battery_after`** controls when the battery is allowed to be 100% full:
- **Before this hour:** PV charge rate may be limited  
- **At/after this hour:** No limit for all modes (target-hour check applies globally)

## How It Works

### Algorithm

Peak shaving uses one or two components depending on `mode`. The stricter (lower non-negative) limit wins when both are active.

**Component 1: Time-Based** (modes `time` and `combined`)

Spreads remaining free capacity evenly until `allow_full_battery_after`:

```
slots_remaining = slots until allow_full_battery_after
pv_surplus = sum of max(production - consumption, 0) for remaining slots

if pv_surplus > free_capacity:
    charge_limit = free_capacity / slots_remaining  (Wh/slot -> W)
```

**Component 2: Price-Based** (modes `price` and `combined`)

Before cheap window - reserves free capacity so cheap-slot PV surplus fills battery completely:

```
cheap_slots = slots where price <= price_limit
target_reserve = min(sum of PV surplus in cheap slots, max_capacity)
additional_allowed = free_capacity - target_reserve

if additional_allowed <= 0:  -> block charging (rate = 0)
else:                         -> spread additional_allowed over slots before window
```

Inside cheap window - if total PV surplus in the window exceeds free capacity, the battery cannot fully absorb everything. Charging is spread evenly over the cheap slots so the battery fills gradually instead of hitting 100% in the first slot:

```
if total_cheap_surplus > free_capacity:
    charge_limit = free_capacity / num_cheap_slots  (Wh/slot -> W)
else:
    no limit (-1)
```

The charge limit is applied using **MODE 8** (`limit_battery_charge_rate`). Peak shaving only applies when discharge is already allowed by the main price-based logic.

### Skip Conditions

Peak shaving is automatically skipped when:

1. **`price_limit` not configured** for mode `price` or `combined` - price component disabled
2. **No PV production** - nighttime, no action needed
3. **Past the target hour** (`allow_full_battery_after`) - applies to all modes; no limit
4. **Battery in always_allow_discharge region** - SOC is already high
5. **Grid charging active (MODE -1)** - force charge takes priority
6. **Discharge not allowed** - battery is being preserved for upcoming high-price hours
7. **EVCC is actively charging** - EV consumes the excess PV
8. **EV connected in PV mode** - EVCC will absorb PV surplus

The price-based component also returns no limit when:
- No cheap slots exist in the forecast
- Inside cheap window and total surplus fits in free capacity (absorb freely)

### EVCC Interaction

When an EV charger is managed by EVCC:

- **EV actively charging** (`charging=true`): Peak shaving is disabled - the EV consumes the excess PV
- **EV connected in PV mode** (`connected=true` AND `mode=pv`): Peak shaving is disabled - EVCC will naturally absorb surplus PV when the threshold is reached
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

- **Peak Shaving Enabled** - switch entity
- **Peak Shaving Allow Full After** - number entity (0-23, step 1)
- **Peak Shaving Charge Limit** - sensor entity (unit: W)

## Known Limitations

1. **Flat charge distribution:** The charge rate limit is uniform across all time slots, but PV production peaks at midday. The battery may not reach exactly 100% by the target hour.

2. **No intra-day adjustment:** If clouds reduce PV significantly, the limit stays as calculated until the next evaluation cycle (every 3 minutes). The system self-corrects because free capacity stays high, which increases the allowed charge rate.

3. **Code duplication:** `NextLogic` is a copy of `DefaultLogic` with peak shaving added. Once stable, the two could be merged or refactored.
