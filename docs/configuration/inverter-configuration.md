## General options

### max_grid_charge_rate
This is the upper limit to charge the battery from grid. Value is WATT. This value should not be above the limit of your inverter.

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
Enable or disable the resilient wrapper for graceful outage handling. When enabled, a temporary inverter failure makes batcontrol skip the current control cycle and retry on the next scheduled run, instead of terminating. No decisions are made on stale data - the inverter is simply read again next cycle. This helps batcontrol survive brief connection losses (e.g. firmware upgrades) without exiting.

Errors before the first successful control command still fail fast, so configuration mistakes are caught at startup.

Why not just let batcontrol crash and rely on the container restart policy? Without the wrapper, every inverter outage terminates the process, and `restart: unless-stopped` brings it straight back up. During a multi-minute outage this turns into a tight restart loop, and **each restart re-fetches the price and solar forecasts from their providers**. Repeated cold starts can therefore run into provider rate limits (e.g. Awattar/Tibber for prices, Forecast.Solar for solar), which can leave batcontrol without fresh data even after the inverter recovers. Keeping the process alive and skipping cycles avoids hammering both the inverter and the data providers.

Default:
```
enable_resilient_wrapper: true
```

### outage_tolerance_minutes
The maximum duration (in minutes) to tolerate inverter outages before terminating. While the inverter is unreachable, each control cycle is skipped. If communication is not restored within this window, batcontrol gives up and exits with an error.

Default:
```
outage_tolerance_minutes: 24  # 24 minutes
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

### Requirements

- The inverter web interface must be reachable over HTTP from the machine that runs batcontrol (local network).
- A web interface login is required. Both the **customer** and the **technician** logins work — use the login name in lowercase (`customer` or `technician`) together with the matching password.
- The Fronius **Solar.API** does *not* need to be enabled manually: batcontrol activates it automatically at startup, since it is required to read the battery state of charge.

### Changes batcontrol makes to the inverter configuration

At startup, batcontrol:

- **Enables the Solar.API** (`SolarAPIv1Enabled`), which is required to read SoC and power values.
- **Saves a backup** of the current battery settings (min/max SoC, energy-management mode/power, grid-charging flag) to `config/battery_config.json` and of the time-of-use schedule to `config/timeofuse_config.json`. Both files are only written if they do not already exist — after an unclean stop the existing files are preserved.
- **Enables charging from grid** (`HYB_EVU_CHARGEFROMGRID`), so batcontrol can charge the battery during cheap price windows.

During operation, batcontrol controls the battery by writing battery settings (min/max SoC in manual mode, energy-management mode and power) and time-of-use schedules.

On a clean shutdown, batcontrol restores the original settings. The two backup files behave differently:

- **`config/timeofuse_config.json`** is read from disk and used for the time-of-use restore. If the file was preserved after a crash, the original time-of-use schedule is correctly restored on the next clean shutdown.
- **`config/battery_config.json`** is written as a reference, but the battery restore uses the settings fetched live from the inverter at startup — the file is not read back. If batcontrol crashed and the inverter still holds batcontrol-modified settings, the live fetch captures those modified settings, so battery settings are not automatically restored to the original pre-crash state. The file can be inspected manually to see what the original settings were.

Both files are deleted after a successful restore. The Solar.API stays enabled — disable it manually in the inverter web UI if no other software needs it (note: the Fronius Wattpilot wallbox requires the Solar.API).

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