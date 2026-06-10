## General options

### max_grid_charge_rate
This is the uppler limit to charge the battery from grid. Value is WATT. This value should not be above the limit of your inverter.

Default:
```
max_grid_charge_rate: 5000
```

### max_pv_charge_rate
This limits the used amount of PV to charge the battery. Value is WATT.
With adding `#` in front of the value, this limit is not set and will push all PV into the battery.

Default:
```
#max_pv_charge_rate: 3000  (Disabled)
```

## Resilient Wrapper Options (since 0.7.0)

These options enable graceful handling of temporary inverter outages (e.g., during firmware upgrades or network interruptions).

### enable_resilient_wrapper
Enable or disable the resilient wrapper for graceful outage handling. When enabled, temporary inverter failures are handled gracefully by caching values and applying retry backoff. This helps batcontrol survive brief connection losses without terminating.

Default:
```
enable_resilient_wrapper: true
```

### outage_tolerance_minutes
The maximum duration (in minutes) to tolerate inverter outages before terminating. This allows batcontrol to survive firmware upgrades or network issues up to the specified time window. After this timeout, batcontrol will give up and exit with an error.

Default:
```
outage_tolerance_minutes: 24  # 24 minutes
```

### retry_backoff_seconds
The time to wait (in seconds) before retrying after an inverter failure. This prevents hammering an unavailable inverter during the outage period and allows time for recovery.

Default:
```
retry_backoff_seconds: 60  # 60 seconds
```

## mqtt
This enables the MQTT inverter driver, which allows integration with any battery/inverter system via MQTT topics. This is a generic bridge that works with any external system that can publish battery status and receive control commands over MQTT.

For detailed documentation, see [MQTT Inverter](../integrations/mqtt-inverter.md).

```yaml
inverter:
  type: mqtt
  capacity: 10000              # Battery capacity in Wh (required)
  min_soc: 5                   # Minimum SoC % (default: 5)
  max_soc: 100                 # Maximum SoC % (default: 100)
  max_grid_charge_rate: 5000   # Maximum charge rate in W (required)
  cache_ttl: 120               # Cache TTL for SOC values in seconds (default: 120)
```

**Key Features:**
- Generic MQTT-based integration for any inverter
- Uses batcontrol's shared MQTT connection
- Supports Home Assistant auto-discovery
- Real-time status updates and command control
- No vendor-specific protocols required

## fronius_gen24
This enables the Fronius GEN24 inverter.

```yaml
inverter:
  type: fronius_gen24 #currently only fronius_gen24 supported
  address: 192.168.0.XX # the local IP of your inverter. needs to be reachable from the machine that runs batcontrol
  user: customer #customer or technician lowercase only!!
  password: YOUR-PASSWORD #
  max_grid_charge_rate: 5000 # Watt
  fronius_inverter_id: '1' # Optional: ID of the inverter in Fronius API (default: '1') (ab 0.5.6)
  fronius_controller_id: '0' # Optional: ID of the controller in Fronius API (default: '0') (ab 0.5.6)
```

### Additional Parameters (since 0.5.6)
- **fronius_inverter_id**: Optional parameter to specify the inverter ID in the Fronius API. Default is '1'.
- **fronius_controller_id**: Optional parameter to specify the controller ID in the Fronius API. Default is '0'.

## fronius-modbus
This enables the Fronius Modbus TCP inverter backend. It controls a Fronius GEN24/BYD battery through SunSpec storage-control registers and does not require inverter web-login credentials.

Enable Modbus TCP in the Fronius inverter web UI before using this backend.

```yaml
inverter:
  type: fronius-modbus
  address: 192.168.0.XX       # Local IP/host of your inverter
  port: 502                   # Optional, default: 502
  unit_id: 1                  # Optional, default: 1
  capacity: 10000             # Required: battery capacity in Wh
  max_grid_charge_rate: 5000  # Required: maximum grid charge rate in W
  min_soc: 5                  # Optional, default: 5
  max_soc: 100                # Optional, default: 100
  revert_seconds: 0           # Optional, default: 0
```

### Backup / emergency-power systems
For systems with backup or emergency-power support, batcontrol should run from a UPS/USV-backed power source. If the public grid fails while restrictive Modbus battery flags are active, batcontrol must remain powered so it can react and reset the Modbus flags.

Optional backup-mode safety settings:

```yaml
  backup_mode_safety_enabled: true
  meter_unit_id: 200          # Optional, default: 200
```

With backup-mode safety enabled, restrictive battery-control writes are only sent while the grid is detected as available. If grid status is off-grid, unknown, or unreadable, batcontrol restores allow-discharge mode instead.

### Notes
- Do not run multiple tools that write Fronius battery-control Modbus registers at the same time.
- If you previously changed battery-control registers with another tool, stop that tool and restart the inverter before running batcontrol.


## dummy
This option is for testing purposes only

*** Sample needs to be added ***