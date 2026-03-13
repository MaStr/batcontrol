# Peak Shaving Feature — Implementation Plan

## Overview

Add peak shaving to batcontrol: manage PV battery charging rate so the battery fills up gradually, reaching full capacity by a target hour (`allow_full_battery_after`). This prevents the battery from being full too early in the day.

**Problem:** All PV systems in the country produce peak power around midday. Most batteries are full by then, causing excess PV to be fed into the grid at a time when grid prices are lowest and — for newer installations — feed-in may not be compensated at all. Peak shaving spreads battery charging over time so the system absorbs as much solar energy as possible into the battery, rather than feeding it into the grid during peak hours.

**EVCC interaction:** When an EV is actively charging (`charging=true`), peak shaving is disabled — the EV consumes the excess PV. When an EV is connected in EVCC "pv" mode (waiting for surplus), EVCC+EV will naturally absorb excessive PV energy once the surplus threshold is reached.

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

## 2. EVCC Integration — Use Existing Charging State

### 2.1 Approach

Peak shaving uses the **existing** `evcc_is_charging` boolean from `evcc_api.py` to decide whether to apply PV charge limiting. No new EVCC subscriptions or topics are needed.

- `evcc_is_charging = True` → peak shaving disabled (EV is consuming energy)
- `evcc_is_charging = False` → peak shaving may apply

**Note:** We intentionally do **not** subscribe to or rely on `chargePower`. The existing `charging` topic is sufficient for the peak shaving decision.

### 2.2 No Changes to `evcc_api.py`

The existing EVCC integration already provides:
- `self.evcc_is_charging` — whether any loadpoint is actively charging
- Discharge blocking during EV charging
- Battery halt SOC management

Peak shaving only needs the `evcc_is_charging` boolean, which is already available in `core.py` via `self.evcc_api.evcc_is_charging`.

---

## 3. Logic Changes — Peak Shaving via PV Charge Rate Limiting

### 3.1 Core Algorithm

The algorithm spreads battery charging over time so the battery reaches full at the target hour:

```
slots_remaining = slots from now until allow_full_battery_after
free_capacity = battery free capacity in Wh
expected_pv_surplus = sum of (production - consumption) for those slots, only positive values (Wh)
```

If expected **PV surplus** (production minus consumption) exceeds free capacity, PV would fill the battery too early. We calculate the **maximum PV charge rate** that fills the battery evenly:

```
ideal_charge_rate_wh = free_capacity / slots_remaining  # Wh per slot
ideal_charge_rate_w = ideal_charge_rate_wh * (60 / interval_minutes)  # Convert to W
```

Set `limit_battery_charge_rate = ideal_charge_rate_w` → MODE 8.

If expected PV surplus is less than free capacity, no limit needed (battery won't fill early).

**Note:** The charge limit is distributed evenly across slots. This is a simplification — PV production peaks midday while the limit is flat. This means the limit may have no effect in low-PV morning slots and may clip excess in high-PV midday slots. The battery may not reach exactly 100% by the target hour. This is acceptable for v1; a PV-weighted distribution could be added later.

### 3.2 Algorithm Implementation

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
        hour=self.calculation_parameters.peak_shaving_allow_full_after,
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

    # Calculate PV surplus per slot (only count positive surplus — when PV > consumption)
    pv_surplus = calc_input.production[:slots_remaining] - calc_input.consumption[:slots_remaining]
    pv_surplus = np.clip(pv_surplus, 0, None)  # Only positive surplus counts

    # Sum expected PV surplus energy (Wh) over remaining slots
    interval_hours = self.interval_minutes / 60.0
    expected_surplus_wh = float(np.sum(pv_surplus)) * interval_hours

    free_capacity = calc_input.free_capacity

    if expected_surplus_wh <= free_capacity:
        return -1  # PV surplus won't fill battery early, no limit needed

    if free_capacity <= 0:
        return 0  # Battery is full, block PV charging

    # Spread charging evenly across remaining slots
    wh_per_slot = free_capacity / slots_remaining
    charge_rate_w = wh_per_slot / interval_hours  # Convert Wh/slot → W

    return int(charge_rate_w)
```

### 3.3 EVCC Charging Disables Peak Shaving

When EVCC reports `charging=true`, peak shaving is disabled. All energy goes to EV.

### 3.4 Always-Allow-Discharge Region Skips Peak Shaving

When `stored_energy >= max_capacity * always_allow_discharge_limit`, the battery is in the "always allow discharge" region. In this region, peak shaving is **not applied** — the system is already at high SOC and the normal discharge logic takes over. This also avoids toggling issues when SOC fluctuates near 100%.

### 3.5 Force Charge (MODE -1) Takes Priority Over Peak Shaving

If the price-based logic decides to force charge from grid (`charge_from_grid=True`), this overrides peak shaving. The force charge decision means energy is cheap enough to justify grid charging — peak shaving should not interfere.

When this occurs, a warning is logged:
```
[PeakShaving] Skipped: force_charge (MODE -1) active, grid charging takes priority
```

In practice, force charge should rarely trigger during peak shaving hours because:
- Peak shaving hours have high PV production
- Prices are typically low during peak PV (no incentive to grid-charge)
- There should be enough PV to fill the battery by the target hour

### 3.6 Implementation in `default.py`

**Post-processing step** in `calculate_inverter_mode()`, after existing logic returns `inverter_control_settings`:

```python
# Apply peak shaving as post-processing step
if self.calculation_parameters.peak_shaving_enabled:
    inverter_control_settings = self._apply_peak_shaving(
        inverter_control_settings, calc_input, calc_timestamp)
```

**`_apply_peak_shaving()`:**

```python
def _apply_peak_shaving(self, settings, calc_input, calc_timestamp):
    """Limit PV charge rate to spread battery charging until target hour.

    Skipped when:
    - Past the target hour (allow_full_battery_after)
    - Battery is in always_allow_discharge region (high SOC)
    - EVCC is actively charging an EV
    - Force charge from grid is active (MODE -1)
    """
    # After target hour: no limit
    if calc_timestamp.hour >= self.calculation_parameters.peak_shaving_allow_full_after:
        return settings

    # In always_allow_discharge region: skip peak shaving
    if self.common.is_discharge_always_allowed_capacity(calc_input.stored_energy):
        logger.debug('[PeakShaving] Skipped: battery in always_allow_discharge region')
        return settings

    # EVCC charging: skip peak shaving
    if self.calculation_parameters.evcc_is_charging:
        logger.debug('[PeakShaving] Skipped: EVCC is charging')
        return settings

    # Force charge takes priority over peak shaving
    if settings.charge_from_grid:
        logger.warning('[PeakShaving] Skipped: force_charge (MODE -1) active, '
                       'grid charging takes priority')
        return settings

    charge_limit = self._calculate_peak_shaving_charge_limit(
        calc_input, calc_timestamp)

    if charge_limit >= 0:
        # Apply PV charge rate limit
        if settings.limit_battery_charge_rate < 0:
            # No existing limit — apply peak shaving limit
            settings.limit_battery_charge_rate = charge_limit
        else:
            # Keep the more restrictive limit
            settings.limit_battery_charge_rate = min(
                settings.limit_battery_charge_rate, charge_limit)

        logger.info('[PeakShaving] PV charge limit: %d W (battery full by %d:00)',
                    settings.limit_battery_charge_rate,
                    self.calculation_parameters.peak_shaving_allow_full_after)

    return settings
```

### 3.7 Data Flow — Extended `CalculationParameters`

Peak shaving configuration is passed via the existing `CalculationParameters` dataclass (consistent with existing interface pattern):

```python
@dataclass
class CalculationParameters:
    """ Calculations from Battery control configuration """
    max_charging_from_grid_limit: float
    min_price_difference: float
    min_price_difference_rel: float
    max_capacity: float  # Maximum capacity of the battery in Wh (excludes MAX_SOC)
    # Peak shaving parameters
    peak_shaving_enabled: bool = False
    peak_shaving_allow_full_after: int = 14  # Hour (0-23)
    evcc_is_charging: bool = False  # Whether any EVCC loadpoint is actively charging
```

In `core.py`, the `CalculationParameters` constructor is extended:

```python
evcc_is_charging = False
if self.evcc_api is not None:
    evcc_is_charging = self.evcc_api.evcc_is_charging

peak_shaving_config = self.config.get('peak_shaving', {})

calc_parameters = CalculationParameters(
    self.max_charging_from_grid_limit,
    self.min_price_difference,
    self.min_price_difference_rel,
    self.get_max_capacity(),
    peak_shaving_enabled=peak_shaving_config.get('enabled', False),
    peak_shaving_allow_full_after=peak_shaving_config.get('allow_full_battery_after', 14),
    evcc_is_charging=evcc_is_charging,
)
```

**No changes needed to `CalculationInput`** — the existing fields (`production`, `consumption`, `free_capacity`, `stored_energy`) provide all data the algorithm needs.

**No changes needed to `logic.py` factory** — configuration flows through `CalculationParameters` via the existing `set_calculation_parameters()` method.

---

## 4. Core Integration — `core.py`

### 4.1 Init

No new instance vars needed. Peak shaving config is read from `self.config` each run cycle and passed via `CalculationParameters`.

### 4.2 Run Loop

Extend `CalculationParameters` construction (see Section 3.7). No other changes to the run loop.

### 4.3 Mode Selection

In the mode selection block (after `logic.calculate()`), `limit_battery_charge_rate >= 0` already triggers MODE 8 via `self.limit_battery_charge_rate()`. No changes needed — peak shaving sets the limit in `InverterControlSettings` and the existing dispatch handles it.

---

## 5. MQTT API — Publish Peak Shaving State

Publish topics:
- `{base}/peak_shaving/enabled` — boolean (`true`/`false`, plain text, retained)
- `{base}/peak_shaving/allow_full_battery_after` — integer hour 0-23 (plain text, retained)
- `{base}/peak_shaving/charge_limit` — current calculated charge limit in W (plain text, not retained, -1 if inactive)

Settable topics:
- `{base}/peak_shaving/enabled/set` — accepts `true`/`false`
- `{base}/peak_shaving/allow_full_battery_after/set` — accepts integer 0-23

Home Assistant discovery:
- `peak_shaving/enabled` → switch entity
- `peak_shaving/allow_full_battery_after` → number entity (min: 0, max: 23, step: 1)
- `peak_shaving/charge_limit` → sensor entity (unit: W)

QoS: 1 for all topics (consistent with existing MQTT API).

---

## 6. Tests

### 6.1 Logic Tests (`tests/batcontrol/logic/test_peak_shaving.py`)

**Algorithm tests (`_calculate_peak_shaving_charge_limit`):**
- High PV surplus, small free capacity → low charge limit
- Low PV surplus, large free capacity → no limit (-1)
- PV surplus exactly matches free capacity → no limit (-1)
- Battery full (`free_capacity = 0`) → charge limit = 0
- Past target hour → no limit (-1)
- 1 slot remaining → rate for that single slot
- Consumption reduces effective PV — e.g., 3kW PV, 2kW consumption = 1kW surplus

**Decision tests (`_apply_peak_shaving`):**
- `peak_shaving_enabled = False` → no change to settings
- `evcc_is_charging = True` → peak shaving skipped
- `charge_from_grid = True` → peak shaving skipped, warning logged
- Battery in always_allow_discharge region → peak shaving skipped
- Before target hour, limit calculated → `limit_battery_charge_rate` set
- After target hour → no change
- Existing tighter limit from other logic → kept (more restrictive wins)
- Peak shaving limit tighter than existing → peak shaving limit applied

### 6.2 Config Tests

- With `peak_shaving` section → `CalculationParameters` fields set correctly
- Without `peak_shaving` section → `peak_shaving_enabled = False` (default)
- Invalid `allow_full_battery_after` values (edge cases: 0, 23)

---

## 7. Implementation Order

1. **Config** — Add `peak_shaving` section to dummy config
2. **Data model** — Extend `CalculationParameters` with peak shaving fields
3. **Logic** — `_calculate_peak_shaving_charge_limit()`, `_apply_peak_shaving()` in `default.py`
4. **Core** — Wire EVCC state + peak shaving config into `CalculationParameters`
5. **MQTT** — Publish topics + settable topics + HA discovery
6. **Tests**

---

## 8. Files Modified

| File | Change |
|------|--------|
| `config/batcontrol_config_dummy.yaml` | Add `peak_shaving` section |
| `src/batcontrol/logic/logic_interface.py` | Add peak shaving fields + `evcc_is_charging` to `CalculationParameters` |
| `src/batcontrol/logic/default.py` | `_calculate_peak_shaving_charge_limit()`, `_apply_peak_shaving()` |
| `src/batcontrol/core.py` | Wire EVCC state + peak shaving config into `CalculationParameters` |
| `src/batcontrol/mqtt_api.py` | Peak shaving MQTT topics + HA discovery |
| `tests/batcontrol/logic/test_peak_shaving.py` | New — algorithm + decision tests |

**Not modified:** `evcc_api.py` (no changes needed), `logic.py` factory (config flows through `CalculationParameters`)

---

## 9. Resolved Design Decisions

1. **EVCC integration:** Use existing `evcc_is_charging` boolean only. No `chargePower` subscription — it was reported as unreliable and is not needed for the on/off peak shaving decision.

2. **Grid charging interaction (MODE -1 vs MODE 8):** `force_charge` takes priority. If price logic triggers grid charging during peak shaving hours, a warning is logged but grid charging proceeds. This should rarely occur in practice because PV-heavy hours have low prices.

3. **Interface consistency:** Peak shaving config is passed via `CalculationParameters` (extended with new fields), following the existing pattern. No separate `set_peak_shaving_config()` method.

4. **High SOC handling:** When battery is in `always_allow_discharge` region, peak shaving is skipped entirely. This avoids toggling at near-full SOC and is consistent with the system's existing high-SOC behavior.

5. **Algorithm uses net PV surplus:** The charge limit calculation uses `production - consumption` (positive only), not raw production. This prevents over-throttling when household consumption absorbs most of the PV.

## 10. Known Limitations (v1)

1. **Flat charge distribution:** The charge rate limit is uniform across all slots, but PV production peaks midday. The battery may not reach exactly 100% by the target hour. Acceptable for v1.

2. **No intra-day target adjustment:** If clouds reduce PV significantly, the limit stays as calculated until the next evaluation cycle (every 3 minutes). The system self-corrects because free capacity stays high, which increases the allowed charge rate.
