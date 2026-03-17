# Pydantic Config Migration

## Status: In Progress

Pydantic validation layer added at config load time. Type coercion and validation
that was scattered across the codebase is now centralized in `config_model.py`.

## What Was Done

### New Files
- `src/batcontrol/config_model.py` — Pydantic v2 models for all config sections
- `tests/batcontrol/test_config_model.py` — Unit tests for every model and coercion
- `tests/batcontrol/test_config_load_integration.py` — Integration test with real YAML

### Modified Files
- `src/batcontrol/setup.py` — Calls `validate_config()` after YAML load
- `src/batcontrol/core.py` — Removed manual `time_resolution_minutes` string-to-int conversion and validation
- `src/batcontrol/dynamictariff/dynamictariff.py` — Removed `float()` casts on `vat`, `markup`, `fees`, `tariff_zone_*`
- `src/batcontrol/forecastconsumption/consumption.py` — Pydantic handles semicolon-string parsing for flat HA addon config; factory retains parsing for nested `homeassistant_api` dict case
- `src/batcontrol/inverter/inverter.py` — Removed manual `max_charge_rate` rename and `max_pv_charge_rate` default; added `cache_ttl` passthrough
- `pyproject.toml` — Added `pydantic>=2.0,<3.0` dependency

### Design Decisions
- All models use `extra='allow'` so unknown config keys are preserved (forward compat)
- `validate_config()` returns a plain `dict` (via `model_dump(exclude_none=True)`) so downstream key-presence checks work unchanged
- Validation happens once in `setup.py:load_config()`, not scattered per-module
- Legacy key rename (`max_charge_rate` -> `max_grid_charge_rate`) handled by `model_validator`

## What Was NOT Done (Remaining Work)

### TLS Config Bug (Both MQTT and EVCC)
**Files:** `src/batcontrol/mqtt_api.py:92-100`, `src/batcontrol/evcc_api.py:115-123`

The TLS code checks `config['tls'] is True` (bool) then subscripts it as a dict
(`config['tls']['ca_certs']`). This crashes with `TypeError` when TLS is enabled.
The fix should use sibling keys (`config['cafile']`, etc.) which are already defined
in the Pydantic models `MqttConfig` and `EvccConfig`.

**Not fixed** because the code is marked "not tested yet" and needs real TLS testing.

### MQTT API Type Coercions
**File:** `src/batcontrol/mqtt_api.py`

Still has manual `int()` casts on `port`, `retry_attempts`, `retry_delay`.
These are now redundant since `MqttConfig` handles coercion, but removal was deferred
to minimize diff size. Safe to remove in a follow-up.

### EVCC API Type Coercions
**File:** `src/batcontrol/evcc_api.py`

Same situation — manual `int(config['port'])` is redundant. Safe to remove.

### HA Addon Config Passthrough
**Repo:** `MaStr/batcontrol_ha_addon`

The HA addon's `run.sh` passes config values from `options.json` into the YAML config.
No changes were made there. The Pydantic models handle string-to-numeric coercion
that the addon introduces, so no addon changes are needed.

## Config Model Reference

```
BatcontrolConfig (top-level)
  ├── timezone: str = 'Europe/Berlin'
  ├── time_resolution_minutes: int = 60  [validated: 15 or 60]
  ├── loglevel: str = 'info'  [validated + lowercased]
  ├── logfile_enabled: bool = True
  ├── log_everything: bool = False
  ├── max_logfile_size: int = 200
  ├── logfile_path: str = 'logs/batcontrol.log'
  ├── solar_forecast_provider: str = 'fcsolarapi'
  │
  ├── battery_control: BatteryControlConfig
  │     ├── min_price_difference: float = 0.05
  │     ├── min_price_difference_rel: float = 0.10
  │     ├── always_allow_discharge_limit: float = 0.90
  │     ├── max_charging_from_grid_limit: float = 0.89
  │     └── min_recharge_amount: float = 100.0
  │
  ├── battery_control_expert: BatteryControlExpertConfig (optional)
  │     ├── charge_rate_multiplier: float = 1.1
  │     ├── soften_price_difference_on_charging: bool = False
  │     ├── soften_price_difference_on_charging_factor: int = 5
  │     ├── round_price_digits: int = 4
  │     └── production_offset_percent: float = 1.0
  │
  ├── inverter: InverterConfig
  │     ├── type: str = 'dummy'  [fronius_gen24, mqtt, dummy]
  │     ├── address, user, password: Optional[str]
  │     ├── max_grid_charge_rate: float = 5000  [alias: max_charge_rate]
  │     ├── max_pv_charge_rate: float = 0
  │     ├── min_pv_charge_rate: float = 0
  │     ├── enable_resilient_wrapper: bool = False
  │     ├── outage_tolerance_minutes: float = 24
  │     ├── retry_backoff_seconds: float = 60
  │     └── capacity, min_soc, max_soc, base_topic, cache_ttl: Optional (MQTT)
  │
  ├── utility: UtilityConfig (required)
  │     ├── type: str  [tibber, awattar_at, awattar_de, evcc, energyforecast, tariff_zones]
  │     ├── vat: Optional[float] = None  [excluded from output when not set]
  │     ├── fees: Optional[float] = None  [excluded from output when not set]
  │     ├── markup: Optional[float] = None  [excluded from output when not set]
  │     └── tariff_zone_1/2/3, zone_1/2/3_hours: Optional
  │
  ├── mqtt: MqttConfig (optional)
  │     ├── enabled: bool = False
  │     ├── broker: str, port: int = 1883
  │     ├── topic: str, username/password: Optional[str]
  │     ├── tls: bool = False
  │     ├── cafile, certfile, keyfile, tls_version: Optional[str]
  │     └── auto_discover_enable: bool, auto_discover_topic: str
  │
  ├── evcc: EvccConfig (optional)
  │     ├── enabled: bool = False
  │     ├── broker: str, port: int = 1883
  │     ├── loadpoint_topic: Union[str, List[str]]
  │     ├── block_battery_while_charging: bool = True
  │     ├── tls: bool = False
  │     └── cafile, certfile, keyfile, tls_version: Optional[str]
  │
  ├── pvinstallations: List[PvInstallationConfig]
  │     ├── name, type: str
  │     ├── lat, lon, declination, azimuth, kWp: Optional[float]
  │     └── url, horizon, apikey, algorithm, item, token: Optional[str]
  │
  └── consumption_forecast: ConsumptionForecastConfig
        ├── type: str = 'csv'
        ├── annual_consumption: Optional[float]
        ├── history_days: Optional[List[int]]  [parses "−7;−14;−21"]
        ├── history_weights: Optional[List[int]]  [parses "1;1;1"]
        └── base_url, apitoken, entity_id, cache_ttl_hours, multiplier: Optional
```

## Key HA Addon Coercions Handled

| Config Path | Raw Type (HA) | Coerced Type | Example |
|---|---|---|---|
| `time_resolution_minutes` | `str` | `int` | `"15"` -> `15` |
| `mqtt.port` | `str` | `int` | `"1883"` -> `1883` |
| `evcc.port` | `str` | `int` | `"1883"` -> `1883` |
| `utility.vat` | `str` | `float` | `"0.19"` -> `0.19` |
| `inverter.max_grid_charge_rate` | `str` | `float` | `"5000"` -> `5000.0` |
| `battery_control.*` | `str` | `float` | `"0.05"` -> `0.05` |
| `consumption_forecast.history_days` | `str` | `List[int]` | `"-7;-14;-21"` -> `[-7,-14,-21]` |

## How to Run Tests

```bash
cd /home/user/batcontrol
python -m pytest tests/batcontrol/test_config_model.py -v
python -m pytest tests/batcontrol/test_config_load_integration.py -v
```
