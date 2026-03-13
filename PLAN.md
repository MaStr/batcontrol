# Peak Shaving Feature - Implementation Plan

## Overview

Add peak shaving to batcontrol: manage PV battery charging rate so the battery fills up gradually, reaching full capacity by a target hour (`allow_full_battery_after`). This prevents the battery from being full too early in the day.

**Problem:** All PV systems in the country produce peak power around midday. Most batteries are full by then, causing excess PV to be fed into the grid at a time when grid prices are lowest and - for newer installations - feed-in may not be compensated at all. Peak shaving spreads battery charging over time so the system absorbs as much solar energy as possible into the battery, rather than feeding it into the grid during peak hours.

**EVCC interaction:**
- When an EV is actively charging (`charging=true`), peak shaving is disabled - the EV consumes the excess PV.
- When an EV is connected and the loadpoint mode is `pv`, peak shaving is also disabled - EVCC+EV will naturally absorb excessive PV energy once the surplus threshold is reached.
- If the EV disconnects or the mode changes away from `pv`, peak shaving is re-enabled.

Uses the existing **MODE 8 (limit_battery_charge_rate)** to throttle PV charging.

**Logic architecture:** Peak shaving is implemented as a **new independent logic class** (`NextLogic`), selectable via `type: next` in the config. The existing `DefaultLogic` remains untouched. This allows users to opt into the new behavior while keeping the stable default path.

---

## 1. Configuration

### 1.1 Top-Level `peak_shaving` Section

```yaml
peak_shaving:
  enabled: false
  mode: combined               # 'time' | 'price' | 'combined'
  allow_full_battery_after: 14   # Hour (0-23) - battery should be full by this hour
  price_limit: 0.05              # Euro/kWh - keep free capacity for slots at or below this price
```

**`mode`** - selects which algorithm components are active:
- **`time`** - time-based only: spread free capacity evenly until `allow_full_battery_after`. `price_limit` is not required and is ignored.
- **`price`** - price-based only: reserve capacity for cheap-price PV slots. Requires `price_limit`.
- **`combined`** (default) - both components active; stricter limit wins. Requires `price_limit`.

**`allow_full_battery_after`** - Target hour for the battery to be full:
- **Before this hour:** PV charge rate is limited to spread charging evenly. The battery fills gradually instead of reaching 100% early and overflowing PV to grid.
- **At/after this hour:** No PV charge limit. Battery is allowed to be 100% full. PV overflow to grid is acceptable (e.g., EV arrives home and the charger absorbs excess).
- **During EV charging or EV connected in PV mode:** Peak shaving disabled entirely.

**`price_limit`** (optional for `mode: time`, required for `price`/`combined`) - Keep free battery capacity reserved for upcoming cheap-price time slots:
- When set, the algorithm identifies upcoming slots where `price <= price_limit` and reserves enough free capacity to absorb the full PV surplus during those cheap hours.
- **During a cheap slot** and surplus > free capacity: charging is spread evenly over the remaining cheap slots.
- **During a cheap slot** and surplus <= free capacity: no limit - absorb freely.
- Accepts any numeric value including -1 (which disables cheap-slot detection since no price <= -1); `None` disables the price component.
- When both `price_limit` and `allow_full_battery_after` are configured (mode `combined`), the stricter limit wins.

### 1.2 Logic Type Selection

```yaml
# In the top-level config or battery_control section:
type: next   # Use 'next' to enable peak shaving logic (default: 'default')
```

The `type: next` logic includes all existing `DefaultLogic` behavior plus peak shaving as a post-processing step.

---

## 2. EVCC Integration - Loadpoint Mode & Connected State

### 2.1 Approach

Peak shaving is disabled when **any** of the following EVCC conditions are true:
1. **`charging = true`** - EV is actively charging (already tracked)
2. **`connected = true` AND `mode = pv`** - EV is plugged in and waiting for PV surplus

The EVCC check is handled in `core.py`, **not** in the logic layer. EVCC is an external integration concern, same pattern as `discharge_blocked`.

### 2.2 New EVCC Topics - Derived from `loadpoint_topic`

The `mode` and `connected` topics are derived from the existing `loadpoint_topic` config by stripping `/charging` and appending the relevant suffix:

```
evcc/loadpoints/1/charging   -> evcc/loadpoints/1/mode
                             -> evcc/loadpoints/1/connected
```

Topics not ending in `/charging`: log warning, skip mode/connected subscription.

### 2.3 Changes to `evcc_api.py`

**New state:**
```python
self.evcc_loadpoint_mode = {}       # topic_root -> mode string ("pv", "now", "minpv", "off")
self.evcc_loadpoint_connected = {}  # topic_root -> bool
self.list_topics_mode = []          # derived mode topics
self.list_topics_connected = []     # derived connected topics
```

**In `__init__`:** For each loadpoint topic ending in `/charging`:
```python
root = topic[:-len('/charging')]
mode_topic = root + '/mode'
connected_topic = root + '/connected'
self.list_topics_mode.append(mode_topic)
self.list_topics_connected.append(connected_topic)
self.evcc_loadpoint_mode[root] = None
self.evcc_loadpoint_connected[root] = False
self.client.message_callback_add(mode_topic, self._handle_message)
self.client.message_callback_add(connected_topic, self._handle_message)
```

**In `on_connect`:** Subscribe to mode and connected topics.

**In `_handle_message`:** Route to new handlers based on topic matching.

**New handlers:**
```python
def handle_mode_message(self, message):
    """Handle incoming loadpoint mode messages."""
    root = message.topic[:-len('/mode')]
    mode = message.payload.decode('utf-8').strip().lower()
    old_mode = self.evcc_loadpoint_mode.get(root)
    if old_mode != mode:
        logger.info('Loadpoint %s mode changed: %s -> %s', root, old_mode, mode)
        self.evcc_loadpoint_mode[root] = mode

def handle_connected_message(self, message):
    """Handle incoming loadpoint connected messages."""
    root = message.topic[:-len('/connected')]
    connected = re.match(b'true', message.payload, re.IGNORECASE) is not None
    old_connected = self.evcc_loadpoint_connected.get(root, False)
    if old_connected != connected:
        logger.info('Loadpoint %s connected: %s', root, connected)
        self.evcc_loadpoint_connected[root] = connected
```

**New public property:**
```python
@property
def evcc_ev_expects_pv_surplus(self) -> bool:
    """True if any loadpoint has an EV connected in PV mode."""
    for root in self.evcc_loadpoint_connected:
        if self.evcc_loadpoint_connected.get(root, False) and \
           self.evcc_loadpoint_mode.get(root) == 'pv':
            return True
    return False
```

**`shutdown`:** Unsubscribe from mode and connected topics.

**EVCC offline reset:** When EVCC goes offline (status message received), mode and connected state are reset to prevent stale values:
```python
for root in list(self.evcc_loadpoint_mode.keys()):
    self.evcc_loadpoint_mode[root] = None
    self.evcc_loadpoint_connected[root] = False
```

### 2.4 Backward Compatibility

- Topics not ending in `/charging`: warning logged, no mode/connected sub, existing behavior unchanged
- `evcc_ev_expects_pv_surplus` returns `False` when no data received
- Existing `evcc_is_charging` behavior is completely unchanged

---

## 3. New Logic Class - `NextLogic`

### 3.1 Architecture

`NextLogic` is an **independent** `LogicInterface` implementation in `src/batcontrol/logic/next.py`. It contains all the logic from `DefaultLogic` plus peak shaving as a post-processing step.

The implementation approach:
- Copy `DefaultLogic` to create `NextLogic`
- Add peak shaving methods (`_apply_peak_shaving`, `_calculate_peak_shaving_charge_limit`)
- Add post-processing call in `calculate_inverter_mode()`

This keeps `DefaultLogic` completely untouched and allows the `next` logic to evolve independently.

### 3.2 Core Algorithm

The algorithm has two components that both compute a PV charge rate limit in W. The stricter (lower non-negative) limit wins.

#### Component 1: Price-Based (Primary)

The primary driver. The idea: before cheap-price slots arrive, keep the battery partially empty so those slots' PV surplus fills the battery completely rather than spilling to the grid.

```
cheap_slots = upcoming slots where price <= price_limit
target_reserve_wh = min(sum of PV surplus in cheap slots, max_capacity)
additional_charging_allowed_wh = free_capacity - target_reserve_wh

if additional_charging_allowed <= 0:
    block PV charging (rate = 0)
else:
    spread additional_charging_allowed over slots_before_cheap_window
    charge_rate = additional_charging_allowed / slots_before_cheap / interval_hours
```

When `price_limit` is not configured: this component returns -1 (no limit), effectively **disabling peak shaving entirely** since both components must be configured for any limit to apply.

When the current slot is cheap (`prices[0] <= price_limit`): no limit - absorb as much PV as possible.

#### Component 2: Time-Based (Secondary)

Spreads remaining battery free capacity evenly until `allow_full_battery_after`. Only triggers if the expected PV surplus would fill the battery before the target hour:

```
slots_remaining = slots from now until allow_full_battery_after
free_capacity = battery free capacity in Wh
pv_surplus = sum of max(production - consumption, 0) for remaining slots (Wh)

if pv_surplus > free_capacity:
    charge_limit = free_capacity / slots_remaining  (Wh per slot -> W)
```

#### Combining Both Limits

Both limits are computed independently. The final limit is `min(price_limit_w, time_limit_w)` where only non-negative values are considered. If only one component produces a limit, that limit is used.

**Note:** When `price_limit` is not set, the price-based component returns -1 (no limit) and the time-based component is also bypassed - peak shaving is fully disabled. This design ensures peak shaving only activates when `price_limit` is explicitly configured, giving operators control over when the feature is active.

### 3.3 Algorithm Implementation

#### Time-Based: `_calculate_peak_shaving_charge_limit()`

```python
def _calculate_peak_shaving_charge_limit(self, calc_input, calc_timestamp):
    """Calculate PV charge rate limit to fill battery by target hour.

    Returns: int - charge rate limit in W, or -1 if no limit needed
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

    # Calculate PV surplus per slot (only count positive surplus - when PV > consumption)
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
    charge_rate_w = wh_per_slot / interval_hours  # Convert Wh/slot -> W

    return int(charge_rate_w)
```

#### Price-Based: `_calculate_peak_shaving_charge_limit_price_based()`

```python
def _calculate_peak_shaving_charge_limit_price_based(self, calc_input):
    """Reserve free capacity for upcoming cheap-price PV slots.

    When inside cheap window (first_cheap_slot == 0):
      If surplus > free capacity: spread free_capacity over cheap slots.
      If surplus <= free capacity: return -1 (no limit needed).

    When before cheap window:
      Reserve capacity so the window can be absorbed fully.
      additional_allowed = free_capacity - target_reserve_wh.
      Spread additional_allowed evenly over slots before the window.
      If additional_allowed <= 0: block charging (return 0).

    Returns: int - charge rate limit in W, or -1 if no limit needed.
    """
    price_limit = self.calculation_parameters.peak_shaving_price_limit
    ...
    cheap_slots = [i for i, p in enumerate(prices) if p is not None and p <= price_limit]
    if not cheap_slots:
        return -1

    first_cheap_slot = cheap_slots[0]

    if first_cheap_slot == 0:  # Inside cheap window
        total_surplus = sum_pv_surplus(cheap_slots)
        if total_surplus <= calc_input.free_capacity:
            return -1  # Battery can absorb everything
        # Spread free capacity evenly over cheap slots
        return int(calc_input.free_capacity / len(cheap_slots) / interval_hours)

    # Before cheap window
    total_surplus = sum_pv_surplus(cheap_slots)
    if total_surplus <= 0:
        return -1
    target_reserve = min(total_surplus, max_capacity)
    additional_allowed = calc_input.free_capacity - target_reserve
    if additional_allowed <= 0:
        return 0  # Block charging
    return int(additional_allowed / first_cheap_slot / interval_hours)
```

### 3.4 Always-Allow-Discharge Region Skips Peak Shaving

When `stored_energy >= max_capacity * always_allow_discharge_limit`, the battery is in the "always allow discharge" region. In this region, peak shaving is **not applied** - the system is already at high SOC and the normal discharge logic takes over. This also avoids toggling issues when SOC fluctuates near 100%.

### 3.5 Force Charge (MODE -1) Takes Priority Over Peak Shaving

If the price-based logic decides to force charge from grid (`charge_from_grid=True`), this overrides peak shaving. The force charge decision means energy is cheap enough to justify grid charging - peak shaving should not interfere.

When this occurs, a warning is logged:
```
[PeakShaving] Skipped: force_charge (MODE -1) active, grid charging takes priority
```

In practice, force charge should rarely trigger during peak shaving hours because:
- Peak shaving hours have high PV production
- Prices are typically low during peak PV (no incentive to grid-charge)
- There should be enough PV to fill the battery by the target hour

### 3.6 Peak Shaving Post-Processing in `NextLogic`

In `calculate_inverter_mode()`, after the existing DefaultLogic calculation returns `inverter_control_settings`:

```python
# Apply peak shaving as post-processing step
if self.calculation_parameters.peak_shaving_enabled:
    inverter_control_settings = self._apply_peak_shaving(
        inverter_control_settings, calc_input, calc_timestamp)

return inverter_control_settings
```

**`_apply_peak_shaving()`:**

Peak shaving uses MODE 8 (`limit_battery_charge_rate` with `allow_discharge=True`). It is only applied when the main logic already allows discharge - meaning no upcoming high-price slots require preserving battery energy.

The method dispatches to the appropriate sub-algorithms based on `peak_shaving_mode`.  The **target-hour check** (`allow_full_battery_after`) applies to all modes and is checked early so no computation occurs past that hour.

```python
def _apply_peak_shaving(self, settings, calc_input, calc_timestamp):
    """Limit PV charge rate based on the configured peak shaving mode.

    Mode behaviour (peak_shaving_mode):
      'time'     - spread remaining capacity until allow_full_battery_after
      'price'    - reserve capacity for upcoming cheap-price PV slots;
                   inside cheap window, spread if surplus > free capacity
      'combined' - both limits active, stricter one wins

    Skipped when:
    - 'price'/'combined' mode and price_limit is not configured
    - No PV production right now (nighttime)
    - Past allow_full_battery_after hour (all modes)
    - Battery in always_allow_discharge region (high SOC)
    - Force-charge from grid active (MODE -1)
    - Discharge not allowed (battery preserved for high-price hours)

    Note: EVCC checks (charging, connected+pv mode) are handled in core.py.
    """
    mode = self.calculation_parameters.peak_shaving_mode
    price_limit = self.calculation_parameters.peak_shaving_price_limit

    # Price component needs price_limit configured
    if mode in ('price', 'combined') and price_limit is None:
        return settings

    # No production right now: skip
    if calc_input.production[0] <= 0:
        return settings

    # Past target hour: skip (applies to all modes)
    if calc_timestamp.hour >= self.calculation_parameters.peak_shaving_allow_full_after:
        return settings

    # In always_allow_discharge region: skip
    if self.common.is_discharge_always_allowed_capacity(calc_input.stored_energy):
        return settings

    # Force charge takes priority
    if settings.charge_from_grid:
        return settings

    # Battery preserved for high-price hours
    if not settings.allow_discharge:
        return settings

    # Compute limits according to mode; price-based handles both before-cheap
    # and in-cheap-window-overflow cases
    price_limit_w = -1
    time_limit_w = -1

    if mode in ('price', 'combined'):
        price_limit_w = self._calculate_peak_shaving_charge_limit_price_based(calc_input)
    if mode in ('time', 'combined'):
        time_limit_w = self._calculate_peak_shaving_charge_limit(calc_input, calc_timestamp)

    candidates = [v for v in (price_limit_w, time_limit_w) if v >= 0]
    if not candidates:
        return settings

    charge_limit = min(candidates)
    if settings.limit_battery_charge_rate < 0:
        settings.limit_battery_charge_rate = charge_limit
    else:
        settings.limit_battery_charge_rate = min(
            settings.limit_battery_charge_rate, charge_limit)

    return settings
```

### 3.7 Data Flow - Extended `CalculationParameters`

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
    # 'time': target hour only | 'price': cheap-slot reservation | 'combined': both
    peak_shaving_mode: str = 'combined'
    peak_shaving_price_limit: Optional[float] = None  # Euro/kWh; any numeric or None

    def __post_init__(self):
        if not 0 <= self.peak_shaving_allow_full_after <= 23:
            raise ValueError(...)
        valid_modes = ('time', 'price', 'combined')
        if self.peak_shaving_mode not in valid_modes:
            raise ValueError(...)
        if (self.peak_shaving_price_limit is not None
                and not isinstance(self.peak_shaving_price_limit, (int, float))):
            raise ValueError(...)  # must be numeric or None
```

**No changes needed to `CalculationInput`** - the existing fields (`production`, `consumption`, `free_capacity`, `stored_energy`) provide all data the algorithm needs.

---

## 4. Logic Factory - `logic.py`

The factory gains a new type `next`:

```python
@staticmethod
def create_logic(config: dict, timezone) -> LogicInterface:
    battery_control = config.get('battery_control', {})
    request_type = battery_control.get('type', 'default').lower()
    interval_minutes = config.get('time_resolution_minutes', 60)

    if request_type == 'default':
        logic = DefaultLogic(timezone, interval_minutes=interval_minutes)
    elif request_type == 'next':
        logic = NextLogic(timezone, interval_minutes=interval_minutes)
    else:
        raise RuntimeError(f'[Logic] Unknown logic type {request_type}')

    # Apply expert tuning attributes (shared between default and next)
    if config.get('battery_control_expert', None) is not None:
        battery_control_expert = config.get('battery_control_expert', {})
        attribute_list = [
            'soften_price_difference_on_charging',
            'soften_price_difference_on_charging_factor',
            'round_price_digits',
            'charge_rate_multiplier',
        ]
        for attribute in attribute_list:
            if attribute in battery_control_expert:
                setattr(logic, attribute, battery_control_expert[attribute])
    return logic
```

The `NextLogic` supports the same expert tuning attributes as `DefaultLogic`.

---

## 5. Core Integration - `core.py`

### 5.1 Init

No new instance vars needed. Peak shaving config is read from `self.config` each run cycle and passed via `CalculationParameters`.

### 5.2 Run Loop

Extend `CalculationParameters` construction:

```python
peak_shaving_config = self.config.get('peak_shaving', {})

calc_parameters = CalculationParameters(
    self.max_charging_from_grid_limit,
    self.min_price_difference,
    self.min_price_difference_rel,
    self.get_max_capacity(),
    peak_shaving_enabled=peak_shaving_config.get('enabled', False),
    peak_shaving_allow_full_after=peak_shaving_config.get('allow_full_battery_after', 14),
    peak_shaving_mode=peak_shaving_config.get('mode', 'combined'),
    peak_shaving_price_limit=peak_shaving_config.get('price_limit', None),
)
```

### 5.3 EVCC Peak Shaving Guard

The EVCC check is handled in `core.py`, keeping EVCC concerns out of the logic layer. This follows the same pattern as `discharge_blocked` (line 536-541 in current code).

After `logic.calculate()` returns and before mode dispatch, peak shaving is overridden if EVCC conditions require it:

```python
# EVCC disables peak shaving (handled in core, not logic)
if self.evcc_api is not None:
    evcc_disable_peak_shaving = (
        self.evcc_api.evcc_is_charging or
        self.evcc_api.evcc_ev_expects_pv_surplus
    )
    if evcc_disable_peak_shaving and inverter_settings.limit_battery_charge_rate >= 0:
        if self.evcc_api.evcc_is_charging:
            logger.debug('[PeakShaving] Disabled: EVCC is actively charging')
        else:
            logger.debug('[PeakShaving] Disabled: EV connected in PV mode')
        inverter_settings.limit_battery_charge_rate = -1
```

**Note:** This clears any `limit_battery_charge_rate` set by the logic, not just peak shaving. In the current codebase this is safe because MODE 8 with `allow_discharge=True` is only set by peak shaving. If this changes in the future, a more targeted approach (e.g., a flag on the settings) would be needed.

### 5.4 Mode Selection

In the mode selection block (after `logic.calculate()`), `limit_battery_charge_rate >= 0` already triggers MODE 8 via `self.limit_battery_charge_rate()`. No changes needed - peak shaving sets the limit in `InverterControlSettings` and the existing dispatch handles it.

---

## 6. MQTT API - Publish Peak Shaving State

Publish topics:
- `{base}/peak_shaving/enabled` - boolean (`true`/`false`, plain text, retained)
- `{base}/peak_shaving/allow_full_battery_after` - integer hour 0-23 (plain text, retained)
- `{base}/peak_shaving/charge_limit` - current calculated charge limit in W (plain text, not retained, -1 if inactive)

Settable topics:
- `{base}/peak_shaving/enabled/set` - accepts `true`/`false`
- `{base}/peak_shaving/allow_full_battery_after/set` - accepts integer 0-23

Home Assistant discovery:
- `peak_shaving/enabled` -> switch entity
- `peak_shaving/allow_full_battery_after` -> number entity (min: 0, max: 23, step: 1)
- `peak_shaving/charge_limit` -> sensor entity (unit: W)

QoS: default (0) for all topics (consistent with existing MQTT API).

`charge_limit` is only published when peak shaving is enabled, to avoid unnecessary MQTT traffic.

---

## 7. Tests

### 7.1 Logic Tests (`tests/batcontrol/logic/test_peak_shaving.py`)

**Algorithm tests (`_calculate_peak_shaving_charge_limit`):**
- High PV surplus, small free capacity -> low charge limit
- Low PV surplus, large free capacity -> no limit (-1)
- PV surplus exactly matches free capacity -> no limit (-1)
- Battery full (`free_capacity = 0`) -> charge limit = 0
- Past target hour -> no limit (-1)
- 1 slot remaining -> rate for that single slot
- Consumption reduces effective PV - e.g., 3kW PV, 2kW consumption = 1kW surplus

**Decision tests (`_apply_peak_shaving`):**
- `peak_shaving_enabled = False` -> no change to settings
- `price_limit = None` -> peak shaving disabled entirely
- Current production = 0 (nighttime) -> peak shaving skipped
- Currently in cheap slot (`prices[0] <= price_limit`) -> no charge limit applied
- `charge_from_grid = True` -> peak shaving skipped, warning logged
- `allow_discharge = False` (battery preserved for high-price hours) -> peak shaving skipped
- Battery in always_allow_discharge region -> peak shaving skipped
- Before target hour, limit calculated -> `limit_battery_charge_rate` set
- After target hour -> no change
- Existing tighter limit from other logic -> kept (more restrictive wins)
- Peak shaving limit tighter than existing -> peak shaving limit applied

**Price-based algorithm tests (`_calculate_peak_shaving_charge_limit_price_based`):**
- No cheap slots -> -1
- Currently in cheap slot -> -1
- PV surplus in cheap slots <= 0 -> -1
- Cheap-slot surplus exceeds free capacity -> block (0)
- Partial reserve spreads over remaining slots -> rate calculation
- Free capacity well above reserve -> charge rate returned
- Consumption reduces cheap-slot surplus
- Both price-based and time-based configured -> stricter limit wins

**`CalculationParameters` tests:**
- `peak_shaving_price_limit` defaults to `None`
- Explicit float value stored correctly
- Zero allowed (free price slots)
- Negative value raises `ValueError`

### 7.2 EVCC Tests (`tests/batcontrol/test_evcc_mode.py`)

- Topic derivation: `evcc/loadpoints/1/charging` -> mode: `evcc/loadpoints/1/mode`, connected: `evcc/loadpoints/1/connected`
- Non-standard topic (not ending in `/charging`) -> warning, no mode/connected sub
- `handle_mode_message` parses mode string correctly
- `handle_connected_message` parses boolean correctly
- `evcc_ev_expects_pv_surplus`: connected=true + mode=pv -> True
- `evcc_ev_expects_pv_surplus`: connected=true + mode=now -> False
- `evcc_ev_expects_pv_surplus`: connected=false + mode=pv -> False
- `evcc_ev_expects_pv_surplus`: no data received -> False
- Multi-loadpoint: one connected+pv is enough to return True
- Mode change from pv to now -> `evcc_ev_expects_pv_surplus` changes to False

### 7.3 Core EVCC Guard Tests (`tests/batcontrol/test_core.py`)

- EVCC actively charging + charge limit active -> limit cleared to -1
- EV connected in PV mode + charge limit active -> limit cleared to -1
- EVCC not charging and no PV mode -> charge limit preserved
- No charge limit active (-1) + EVCC charging -> no change (stays -1)

### 7.4 Config Tests

- `type: next` -> creates `NextLogic` instance
- `type: default` -> creates `DefaultLogic` instance (unchanged)
- With `peak_shaving` section -> `CalculationParameters` fields set correctly
- Without `peak_shaving` section -> `peak_shaving_enabled = False` (default)

---

## 8. Implementation Order

1. **Config** - Add `peak_shaving` section to dummy config, add `type: next` option
2. **Data model** - Extend `CalculationParameters` with peak shaving fields
3. **EVCC** - Add mode and connected topic subscriptions, `evcc_ev_expects_pv_surplus` property
4. **NextLogic** - New file `next.py`: copy DefaultLogic, add `_calculate_peak_shaving_charge_limit()`, `_apply_peak_shaving()`
5. **Logic factory** - Add `type: next` -> `NextLogic` in `logic.py`
6. **Core** - Wire peak shaving config into `CalculationParameters`, EVCC peak shaving guard
7. **MQTT** - Publish topics + settable topics + HA discovery
8. **Tests**
9. **Documentation** - Write `docs/peak_shaving.md` covering feature overview, configuration, EVCC interaction, algorithm explanation, and known limitations

---

## 9. Files Modified

| File | Change |
|------|--------|
| `config/batcontrol_config_dummy.yaml` | Add `peak_shaving` section |
| `src/batcontrol/logic/logic_interface.py` | Add peak shaving fields to `CalculationParameters` |
| `src/batcontrol/logic/next.py` | **New** - `NextLogic` class with peak shaving |
| `src/batcontrol/logic/logic.py` | Add `type: next` -> `NextLogic` |
| `src/batcontrol/evcc_api.py` | Add mode + connected topic subscriptions, `evcc_ev_expects_pv_surplus` |
| `src/batcontrol/core.py` | Wire peak shaving config into `CalculationParameters`, EVCC peak shaving guard |
| `src/batcontrol/mqtt_api.py` | Peak shaving MQTT topics + HA discovery |
| `tests/batcontrol/logic/test_peak_shaving.py` | New - algorithm + decision tests |
| `tests/batcontrol/test_evcc_mode.py` | New - mode/connected topic tests |
| `tests/batcontrol/test_core.py` | Add EVCC peak shaving guard tests |
| `docs/WIKI_peak_shaving.md` | New - feature documentation |

**Not modified:** `default.py` (untouched - peak shaving is in `next.py`)

---

## 10. Resolved Design Decisions

1. **New independent logic class:** Peak shaving lives in `NextLogic` (`type: next`), not as a modification to `DefaultLogic`. This keeps the stable default path untouched and allows the next logic to evolve independently. `NextLogic` is a full copy of `DefaultLogic` with peak shaving added.

2. **EVCC integration:** Handled in `core.py` (not logic layer). Peak shaving is disabled when `evcc_is_charging` OR (`connected=true` AND `mode=pv`). The mode and connected topics are derived from the existing `loadpoint_topic` config by stripping `/charging`. No `chargePower` subscription - it was reported as unreliable.

3. **Grid charging interaction (MODE -1 vs MODE 8):** `force_charge` takes priority. If price logic triggers grid charging during peak shaving hours, a warning is logged but grid charging proceeds.

4. **Interface consistency:** Peak shaving config is passed via `CalculationParameters` (extended with new fields), following the existing pattern.

5. **High SOC handling:** When battery is in `always_allow_discharge` region, peak shaving is skipped entirely. This avoids toggling at near-full SOC.

6. **Algorithm uses net PV surplus:** The charge limit calculation uses `production - consumption` (positive only), not raw production. This prevents over-throttling when household consumption absorbs most of the PV.

7. **Price-based algorithm is the primary driver:** Peak shaving is **disabled** when `price_limit` is `None`. This makes the operator opt in explicitly by setting a price threshold. The price-based algorithm identifies upcoming cheap-price slots and reserves enough free capacity to absorb their full PV surplus. This is the economically motivated core of peak shaving: buy cheap energy via full PV absorption, not by throttling charging arbitrarily.

8. **Currently-in-cheap-slot skip:** When the current slot is cheap (`prices[0] <= price_limit`), no charge limit is applied - the battery should absorb as much PV as possible during this window. This is checked in `_apply_peak_shaving` before calling either sub-algorithm.

9. **Two-limit combination (stricter wins):** The price-based and time-based components are independent. When both are configured, the final limit is `min(price_limit_w, time_limit_w)` over non-negative values. This ensures neither algorithm can inadvertently allow more charging than the other intends.

## 11. Known Limitations (v1)

1. **Flat charge distribution:** The charge rate limit is uniform across all slots, but PV production peaks midday. The battery may not reach exactly 100% by the target hour. Acceptable for v1.

2. **No intra-day target adjustment:** If clouds reduce PV significantly, the limit stays as calculated until the next evaluation cycle (every 3 minutes). The system self-corrects because free capacity stays high, which increases the allowed charge rate.

3. **Code duplication:** `NextLogic` is a copy of `DefaultLogic`. Changes to the default logic need to be mirrored manually. Once peak shaving is stable, the two could be merged (next becomes the new default) or refactored to use composition.
