# Peak Shaving Feature — Implementation Plan

## Overview

Add peak shaving to batcontrol: manage PV battery charging rate so the battery fills up gradually, reaching full capacity by a target hour (`allow_full_battery_after`). This prevents the battery from being full too early (losing midday PV to grid overflow) and maximizes PV self-consumption. Peak shaving is automatically disabled when EVCC reports active EV charging.

Uses the existing **MODE 8 (limit_battery_charge_rate)** to throttle PV charging.

---

## 1. Configuration — Top-Level `peak_shaving` Section

```yaml
peak_shaving:
  enabled: false
  allow_full_battery_after: 14   # Hour (0-23) — battery should be full by this hour
```

**`allow_full_battery_after`** — Target hour for the battery to be full:
- **Before this hour:** PV charge rate is limited to spread charging evenly. The battery fills gradually instead of reaching 100% early and overflowing PV to grid.
- **At/after this hour:** No PV charge limit. Battery is allowed to be 100% full. PV overflow to grid is acceptable (e.g., EV arrives home and the charger absorbs excess).
- **During EV charging (EVCC `charging=true`):** Peak shaving disabled entirely. All energy flows to the car.

---

## 2. EVCC Integration — Derive Root Topic & Subscribe to Power

### 2.1 Topic Derivation

Users configure loadpoint topics like:
```yaml
loadpoint_topic:
  - evcc/loadpoints/1/charging
```

Derive the root by stripping `/charging`:
- `evcc/loadpoints/1/charging` → root = `evcc/loadpoints/1`

Subscribe to: `{root}/chargePower` — current charging power in W

### 2.2 Changes to `evcc_api.py`

**New state:**
```python
self.evcc_loadpoint_power = {}  # root_topic → charge power (W)
self.list_topics_charge_power = []  # derived chargePower topics
```

**In `__init__`:** For each loadpoint topic ending in `/charging`:
```python
root = topic[:-len('/charging')]
power_topic = root + '/chargePower'
self.list_topics_charge_power.append(power_topic)
self.evcc_loadpoint_power[root] = 0.0
self.client.message_callback_add(power_topic, self._handle_message)
```

Topics not ending in `/charging`: log warning, skip power subscription.

**In `on_connect`:** Subscribe to chargePower topics.

**In `_handle_message`:** Route to `handle_charge_power_message`.

**New handler:**
```python
def handle_charge_power_message(self, message):
    try:
        power = float(message.payload)
        root = message.topic[:-len('/chargePower')]
        self.evcc_loadpoint_power[root] = power
    except (ValueError, TypeError):
        logger.error('Could not parse chargePower: %s', message.payload)
```

**New public method:**
```python
def get_total_charge_power(self) -> float:
    return sum(self.evcc_loadpoint_power.values())
```

**`shutdown`:** Unsubscribe from chargePower topics.

### 2.3 Backward Compatibility

- Non-`/charging` topics: warning logged, no power sub, existing behavior unchanged
- `get_total_charge_power()` returns 0.0 when no data received

---

## 3. Logic Changes — Peak Shaving via PV Charge Rate Limiting

### 3.1 Core Algorithm

The simulation spreads battery charging over time so the battery reaches full at the target hour:

```
slots_remaining = slots from now until allow_full_battery_after
free_capacity = battery free capacity in Wh
expected_pv = sum of production forecast for those slots (Wh)
```

If expected PV production exceeds free capacity, PV would fill the battery too early. We calculate the **maximum PV charge rate** that fills the battery evenly:

```
ideal_charge_rate_wh = free_capacity / slots_remaining  # Wh per slot
ideal_charge_rate_w = ideal_charge_rate_wh * (60 / interval_minutes)  # Convert to W
```

Set `limit_battery_charge_rate = ideal_charge_rate_w` → MODE 8.

If expected PV is less than free capacity, no limit needed (battery won't fill early).

### 3.2 Sequential Simulation

Following the default logic's pattern of iterating through future slots:

```python
def _calculate_peak_shaving_charge_limit(self, calc_input, calc_timestamp):
    """Calculate PV charge rate limit to fill battery by target hour.

    Returns: int — charge rate limit in W, or -1 if no limit needed
    """
    slot_start = calc_timestamp.replace(
        minute=(calc_timestamp.minute // self.interval_minutes) * self.interval_minutes,
        second=0, microsecond=0
    )
    target_time = calc_timestamp.replace(
        hour=self.peak_shaving_allow_full_after,
        minute=0, second=0, microsecond=0
    )

    if target_time <= slot_start:
        return -1  # Past target hour, no limit

    slots_remaining = int(
        (target_time - slot_start).total_seconds() / (self.interval_minutes * 60)
    )
    slots_remaining = min(slots_remaining, len(calc_input.production))

    if slots_remaining <= 0:
        return -1

    # Sum expected PV production (Wh) over remaining slots
    interval_hours = self.interval_minutes / 60.0
    expected_pv_wh = float(np.sum(
        calc_input.production[:slots_remaining]
    )) * interval_hours

    free_capacity = calc_input.free_capacity

    if free_capacity <= 0:
        return 0  # Battery is full, block PV charging

    if expected_pv_wh <= free_capacity:
        return -1  # PV won't fill battery early, no limit needed

    # Spread charging evenly across remaining slots
    wh_per_slot = free_capacity / slots_remaining
    charge_rate_w = wh_per_slot / interval_hours  # Convert Wh/slot → W

    return int(charge_rate_w)
```

### 3.3 EVCC Charging Disables Peak Shaving

When EVCC reports `charging=true`, peak shaving is disabled. All energy goes to EV.

### 3.4 Implementation in `default.py`

**Post-processing step** in `calculate_inverter_mode()`, after existing logic:

```python
if self.peak_shaving_enabled and not calc_input.evcc_is_charging:
    inverter_control_settings = self._apply_peak_shaving(
        inverter_control_settings, calc_input, calc_timestamp)
```

**`_apply_peak_shaving()`:**

```python
def _apply_peak_shaving(self, settings, calc_input, calc_timestamp):
    """Limit PV charge rate to fill battery by target hour."""
    current_hour = calc_timestamp.hour

    # After target hour: no limit, battery may be full
    if current_hour >= self.peak_shaving_allow_full_after:
        return settings

    charge_limit = self._calculate_peak_shaving_charge_limit(
        calc_input, calc_timestamp)

    if charge_limit >= 0:
        # Apply PV charge rate limit
        # If existing logic already set a tighter limit, keep the tighter one
        if settings.limit_battery_charge_rate < 0:
            # No existing limit — apply peak shaving limit
            settings.limit_battery_charge_rate = charge_limit
        else:
            # Keep the more restrictive limit
            settings.limit_battery_charge_rate = min(
                settings.limit_battery_charge_rate, charge_limit)

        logger.info('[PeakShaving] PV charge limit: %d W (battery full by %d:00)',
                    settings.limit_battery_charge_rate,
                    self.peak_shaving_allow_full_after)

    return settings
```

### 3.5 Data Flow

**New fields on `CalculationInput`:**
```python
@dataclass
class CalculationInput:
    # ... existing fields ...
    ev_charge_power: float = 0.0    # W — real-time total EV charge power
    evcc_is_charging: bool = False  # Whether any loadpoint is charging
```

In `core.py.run()`:
```python
ev_charge_power = 0.0
evcc_is_charging = False
if self.evcc_api is not None:
    ev_charge_power = self.evcc_api.get_total_charge_power()
    evcc_is_charging = self.evcc_api.evcc_is_charging
```

### 3.6 Config in Logic

In `logic.py` factory:
```python
peak_shaving_config = config.get('peak_shaving', {})
logic.set_peak_shaving_config(peak_shaving_config)
```

In `default.py`:
```python
def set_peak_shaving_config(self, config: dict):
    self.peak_shaving_enabled = config.get('enabled', False)
    self.peak_shaving_allow_full_after = config.get('allow_full_battery_after', 14)
```

---

## 4. Core Integration — `core.py`

### 4.1 Init

No new instance vars. Config in `self.config` is passed to logic factory.

### 4.2 Run Loop

Before `CalculationInput`:
```python
ev_charge_power = 0.0
evcc_is_charging = False
if self.evcc_api is not None:
    ev_charge_power = self.evcc_api.get_total_charge_power()
    evcc_is_charging = self.evcc_api.evcc_is_charging
```

Add to `CalculationInput` constructor.

### 4.3 Mode Selection

In the mode selection block (after logic.calculate), `limit_battery_charge_rate >= 0` already triggers MODE 8 via `self.limit_battery_charge_rate()`. No changes needed — peak shaving sets the limit in InverterControlSettings and the existing dispatch handles it.

---

## 5. MQTT API — Publish Peak Shaving State

Publish topics:
- `/peak_shaving/enabled` — boolean
- `/peak_shaving/allow_full_battery_after` — hour (0-23)
- `/peak_shaving/ev_charge_power` — W (real-time)

Settable topics:
- `peak_shaving/enabled/set`
- `peak_shaving/allow_full_battery_after/set`

Home Assistant discovery for all.

---

## 6. Tests

### 6.1 Logic Tests (`tests/batcontrol/logic/test_peak_shaving.py`)

**Simulation tests:**
- Battery nearly full, lots of PV expected → low charge limit
- Battery mostly empty, moderate PV → no limit (PV won't fill battery)
- Battery already full → charge limit = 0
- Past target hour → no limit (-1)
- 1 slot remaining → calculated rate for that slot

**Decision tests:**
- Peak shaving disabled → no change
- EVCC charging=true → peak shaving disabled
- Before target hour, limit calculated → MODE 8 with limit
- After target hour → no change to existing settings
- Existing tighter limit from logic → kept (more restrictive wins)

### 6.2 EVCC Tests (`tests/batcontrol/test_evcc_power.py`)

- Topic derivation: `evcc/loadpoints/1/charging` → root `evcc/loadpoints/1`
- Non-standard topic → warning, no power sub
- `get_total_charge_power()` multi-loadpoint sum
- `get_total_charge_power()` returns 0.0 initially
- chargePower parsing (valid/invalid)

### 6.3 Config Tests

- With `peak_shaving` → loads correctly
- Without `peak_shaving` → disabled by default

---

## 7. Implementation Order

1. **Config** — Add `peak_shaving` to dummy config
2. **EVCC** — Topic derivation, chargePower sub, `get_total_charge_power()`
3. **Data model** — Add fields to `CalculationInput`
4. **Logic** — `set_peak_shaving_config()`, `_calculate_peak_shaving_charge_limit()`, `_apply_peak_shaving()`
5. **Logic factory** — Pass peak_shaving config
6. **Core** — Wire EV data into CalculationInput
7. **MQTT** — Publish topics + discovery
8. **Tests**

---

## 8. Files Modified

| File | Change |
|------|--------|
| `config/batcontrol_config_dummy.yaml` | Add `peak_shaving` section |
| `src/batcontrol/evcc_api.py` | Topic derivation, chargePower sub, `get_total_charge_power()` |
| `src/batcontrol/logic/logic_interface.py` | Add `ev_charge_power`, `evcc_is_charging` to `CalculationInput` |
| `src/batcontrol/logic/default.py` | Peak shaving simulation + PV charge rate limiting |
| `src/batcontrol/logic/logic.py` | Pass peak_shaving config |
| `src/batcontrol/core.py` | Wire EV data into CalculationInput |
| `src/batcontrol/mqtt_api.py` | Peak shaving MQTT topics + HA discovery |
| `tests/batcontrol/logic/test_peak_shaving.py` | New |
| `tests/batcontrol/test_evcc_power.py` | New |

---

## 9. Open Questions

1. **EVCC chargePower topic** — Is `{loadpoint_root}/chargePower` the correct EVCC MQTT topic name?
2. **Interaction with grid charging** — If price logic wants to grid-charge (MODE -1) but peak shaving wants to limit PV charge (MODE 8), which wins? Current plan: peak shaving only affects PV charge rate, doesn't block grid charging decisions.
