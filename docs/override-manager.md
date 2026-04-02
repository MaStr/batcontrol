# Override Manager — Design & API

## Problem

The original `api_overwrite` was a boolean flag that reset after one evaluation cycle
(~3 minutes). Manual overrides via MQTT `mode/set` were essentially meaningless because
the autonomous logic immediately regained control.

## Solution

`OverrideManager` provides **time-bounded overrides** that persist across multiple
evaluation cycles and auto-expire.

## File

`src/batcontrol/override_manager.py`

## API

```python
class OverrideManager:
    def set_override(mode, duration_minutes=None, charge_rate=None, reason="") -> OverrideState
    def clear_override() -> None
    def get_override() -> Optional[OverrideState]   # None if expired or inactive
    def is_active() -> bool
    remaining_minutes: float                         # property, 0 if no override
```

```python
@dataclass
class OverrideState:
    mode: int                        # -1, 0, 8, or 10
    charge_rate: Optional[int]       # W, only relevant for mode -1
    duration_minutes: float
    reason: str
    created_at: float                # time.time()
    expires_at: float                # auto-calculated
    # Properties:
    remaining_seconds: float
    remaining_minutes: float
    is_expired: bool
    def to_dict() -> dict            # JSON-serializable snapshot
```

## Thread Safety

All public methods acquire `threading.Lock` before modifying `_override`.
Read tools in the MCP server and the main evaluation loop can safely call
`get_override()` concurrently.

## Integration in core.py

### Evaluation loop (`run()`)

```python
override = self.override_manager.get_override()
if override is not None:
    # Re-apply the mode to keep inverter in sync
    self._apply_override(override)
    return  # Skip autonomous logic
# ... normal logic continues
```

### MQTT API callbacks

`api_set_mode()` and `api_set_charge_rate()` create overrides using
`_mqtt_override_duration` (configurable via `override_duration/set`).

### MCP tools

`set_mode_override` and `set_charge_rate` pass explicit `duration_minutes`.

## MQTT Topics

### Published (output)

| Topic | Type | Description |
|-------|------|-------------|
| `override_active` | bool | Whether an override is currently active |
| `override_remaining_minutes` | float | Minutes remaining on active override |
| `override_duration` | float | Configured duration for next mode/set call |

### Subscribable (input)

| Topic | Type | Description |
|-------|------|-------------|
| `override_duration/set` | float | Set duration in minutes (1–1440, 0=reset to 30) |
| `clear_override/set` | str | Any payload clears active override (e.g. `"1"` or `"clear"`) |

## HA Auto Discovery

Three entities registered:
- **Override Active** — sensor, shows "active"/"inactive"
- **Override Remaining Minutes** — sensor, unit: min
- **Override Duration** — number (config), 0–1440, step 5, controls next override length

## Behavioral Contract

1. **Override persists** across evaluation cycles until expiry or clear
2. **Auto-expiry**: `get_override()` returns `None` once `time.time() >= expires_at`
3. **Latest wins**: setting a new override replaces the previous one
4. **Clear is safe**: clearing when nothing is active is a no-op
5. **Duration validation**: `set_override()` raises `ValueError` if `duration_minutes <= 0`
6. **Default duration**: 30 minutes (configurable per-manager and per-MQTT via `override_duration/set`)

## Usage Flow: MQTT

```
1. Publish 120 to  house/batcontrol/override_duration/set
2. Publish -1  to  house/batcontrol/mode/set
   → Creates a 120-minute force-charge override
3. Override auto-expires after 120 min, OR:
   Publish "1" to  house/batcontrol/clear_override/set
   → Clears immediately, autonomous logic resumes next cycle
```

## Usage Flow: MCP

```json
// Tool call: set_mode_override
{"mode": 0, "duration_minutes": 60, "reason": "Guest staying overnight, preserve charge"}

// Tool call: clear_mode_override
{}
```
