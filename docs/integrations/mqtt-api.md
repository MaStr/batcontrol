# MQTT API Configuration

Batcontrol provides an MQTT API that allows you to monitor and integrate your battery control system with other home automation platforms like Home Assistant. The MQTT interface publishes battery status, pricing information, and control states to configurable topics.

## Basic Configuration

```yaml
mqtt:
  enabled: true
  logger: false
  broker: localhost
  port: 1883
  topic: house/batcontrol
  username: user
  password: password
```

### Basic Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable or disable the MQTT API |
| `logger` | boolean | `false` | Enable MQTT logging for debugging |
| `broker` | string | `localhost` | MQTT broker hostname or IP address |
| `port` | integer | `1883` | MQTT broker port (1883 for unencrypted, 8883 for TLS) |
| `topic` | string | `house/batcontrol` | Base topic for all batcontrol MQTT messages |
| `username` | string | `user` | MQTT broker username (if authentication required) |
| `password` | string | `password` | MQTT broker password (if authentication required) |

## Advanced Configuration

### Connection Reliability

```yaml
mqtt:
  retry_attempts: 5    # Number of connection retry attempts
  retry_delay: 10      # Delay in seconds between retry attempts
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `retry_attempts` | integer | `5` | Number of times to retry connection on failure |
| `retry_delay` | integer | `10` | Seconds to wait between retry attempts |

### TLS/SSL Configuration

> ⚠️ **Note**: TLS/SSL support is currently **untested and known to be non-functional**: the implementation expects the certificate options nested below `tls`, while the enable check expects a boolean — these requirements contradict each other. Track progress or report your use case in the project issues before relying on TLS.

## Home Assistant Auto-Discovery

Batcontrol supports Home Assistant's MQTT auto-discovery feature, which automatically creates entities in Home Assistant without manual configuration.

```yaml
mqtt:
  auto_discover_enable: true
  auto_discover_topic: homeassistant
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auto_discover_enable` | boolean | `true` | Enable Home Assistant auto-discovery |
| `auto_discover_topic` | string | `homeassistant` | Base topic for auto-discovery messages |

When enabled, batcontrol will publish device and entity configuration messages to topics like:
- `homeassistant/sensor/batcontrol/battery_soc/config`
- `homeassistant/sensor/batcontrol/current_price/config`
- `homeassistant/binary_sensor/batcontrol/charging_active/config`

## Published Topics

Batcontrol publishes data to the following topic structure (assuming base topic `house/batcontrol`):

### System Status
- `house/batcontrol/status` - System status (`online`/`offline`)
- `house/batcontrol/last_evaluation` - Timestamp of last evaluation (Unix timestamp)
- `house/batcontrol/evaluation_intervall` - Evaluation interval in seconds

### Control & Mode
- `house/batcontrol/mode` - Current operational mode:
  - `-1` = Charge from Grid
  - `0` = Avoid Discharge
  - `8` = Limit Battery Charge Rate ([peak shaving](../features/peak-shaving.md))
  - `10` = Discharge Allowed
- `house/batcontrol/charge_rate` - Current charge rate in W
- `house/batcontrol/limit_battery_charge_rate` - Dynamic battery charge rate limit in W
- `house/batcontrol/discharge_blocked` - Whether discharge is blocked (`true`/`false`)
- `house/batcontrol/api_override_active` - Whether a temporary external/API override is active (`true`/`false`)
- `house/batcontrol/control_source` - Source that last selected the current control state (`api` or `optimizer`)

### Battery Information
- `house/batcontrol/SOC` - State of Charge in % (two decimal places, e.g., `69.00`)
- `house/batcontrol/max_energy_capacity` - Maximum battery capacity in Wh
- `house/batcontrol/stored_energy_capacity` - Energy stored in battery in Wh
- `house/batcontrol/stored_usable_energy_capacity` - Usable energy stored in battery in Wh (considering min SOC)
- `house/batcontrol/reserved_energy_capacity` - Energy reserved for discharge in Wh

### Solar Surplus Information
- `house/batcontrol/solar_surplus_wh` - Expected solar surplus energy in Wh (>0 means usable surplus available)
- `house/batcontrol/solar_active` - Whether solar is currently producing (`true`/`false`)
- `house/batcontrol/night_surplus_wh` - Expected battery surplus in Wh at start of next production window

### Configuration Limits
- `house/batcontrol/always_allow_discharge_limit` - Always discharge limit (0.0-1.0)
- `house/batcontrol/always_allow_discharge_limit_percent` - Always discharge limit in %
- `house/batcontrol/always_allow_discharge_limit_capacity` - Always discharge limit in Wh
- `house/batcontrol/max_charging_from_grid_limit` - Max charging from grid limit (0.0-1.0)
- `house/batcontrol/max_charging_from_grid_limit_percent` - Max charging from grid limit in %
- `house/batcontrol/min_grid_charge_soc` - Optional minimum grid-charge target (0.0-1.0)
- `house/batcontrol/min_grid_charge_soc_percent` - Optional minimum grid-charge target in %
- `house/batcontrol/production_offset` - Production offset multiplier (`1.0` = 100%, `0.8` = 80%, etc.)

### Peak Shaving
See [Peak Shaving](../features/peak-shaving.md) for details:

- `house/batcontrol/peak_shaving/enabled` - Whether peak shaving is enabled (`true`/`false`)
- `house/batcontrol/peak_shaving/mode` - Active mode (`time`, `price`, or `combined`)
- `house/batcontrol/peak_shaving/allow_full_battery_after` - Target hour (0-23)
- `house/batcontrol/peak_shaving/charge_limit` - Current charge limit in W (`-1` = inactive / no limit)
- `house/batcontrol/peak_shaving/price_limit` - Price threshold in EUR/kWh

### Price Information
- `house/batcontrol/min_price_difference` - Minimum price difference in EUR (e.g., `0.050`)
- `house/batcontrol/min_price_difference_rel` - Relative minimum price difference (e.g., `0.100`)
- `house/batcontrol/min_dynamic_price_difference` - Dynamic price difference limit in EUR

### Forecasts (JSON Arrays)
- `house/batcontrol/FCST/production` - Forecasted solar production in W
- `house/batcontrol/FCST/consumption` - Forecasted consumption in W
- `house/batcontrol/FCST/prices` - Forecasted electricity prices in EUR
- `house/batcontrol/FCST/net_consumption` - Forecasted net consumption in W

### Inverter-Specific Topics (per inverter, e.g., inverter 0)
- `house/batcontrol/inverters/0/SOC` - Inverter SOC in %
- `house/batcontrol/inverters/0/stored_energy` - Stored energy in Wh
- `house/batcontrol/inverters/0/free_capacity` - Free capacity in Wh
- `house/batcontrol/inverters/0/max_capacity` - Maximum capacity in Wh
- `house/batcontrol/inverters/0/usable_capacity` - Usable capacity in Wh
- `house/batcontrol/inverters/0/max_grid_charge_rate` - Max grid charge rate in W
- `house/batcontrol/inverters/0/max_pv_charge_rate` - Max PV charge rate in W
- `house/batcontrol/inverters/0/min_soc` - Minimum SOC setting
- `house/batcontrol/inverters/0/max_soc` - Maximum SOC setting
- `house/batcontrol/inverters/0/capacity` - Total capacity in Wh
- `house/batcontrol/inverters/0/em_mode` - Energy Manager mode (Fronius specific)
- `house/batcontrol/inverters/0/em_power` - Energy Manager power setting in W (Fronius specific)

## Command Topics (Input API)

Batcontrol listens to the following `/set` topics for remote control:

### Main Control
- `house/batcontrol/mode/set` - Set operational mode (send `-1`, `0`, `8`, or `10`)
- `house/batcontrol/charge_rate/set` - Set charge rate in W (automatically sets mode to `-1`)
- `house/batcontrol/limit_battery_charge_rate/set` - Set dynamic battery charge rate limit in W

### Configuration
- `house/batcontrol/always_allow_discharge_limit/set` - Set always discharge limit (0.0-1.0)
- `house/batcontrol/max_charging_from_grid_limit/set` - Set max charging from grid limit (0.0-1.0)
- `house/batcontrol/min_price_difference/set` - Set minimum price difference in EUR
- `house/batcontrol/min_price_difference_rel/set` - Set relative minimum price difference (e.g. `0.10` for 10%)
- `house/batcontrol/production_offset/set` - Set production offset multiplier (0.0-2.0)

### Peak Shaving
- `house/batcontrol/peak_shaving/enabled/set` - Enable or disable peak shaving (`true`/`false`)
- `house/batcontrol/peak_shaving/mode/set` - Set mode (`time`, `price`, or `combined`)
- `house/batcontrol/peak_shaving/allow_full_battery_after/set` - Set target hour (0-23)
- `house/batcontrol/peak_shaving/price_limit/set` - Set price threshold in EUR/kWh (`-1` disables the price component)

All `/set` changes are temporary runtime overrides and are not written back to the configuration file.

### Inverter Control (per inverter, e.g., inverter 0)
- `house/batcontrol/inverters/0/max_grid_charge_rate/set` - Set max grid charge rate in W
- `house/batcontrol/inverters/0/max_pv_charge_rate/set` - Set max PV charge rate in W
- `house/batcontrol/inverters/0/em_mode/set` - Set Energy Manager mode (Fronius: 0-2)
- `house/batcontrol/inverters/0/em_power/set` - Set Energy Manager power in W (Fronius specific)

### Testdriver/Dummy Inverter (for testing)
- `house/batcontrol/inverters/0/SOC/set` - Set SOC manually (0-100, testdriver only)

## Forecast Data Format

The forecast topics (`/FCST/*`) publish JSON data with the following structure:

```json
{
  "data": [
    {
      "time_start": 1696435200,
      "value": 2500.5,
      "time_end": 1696438800
    },
    {
      "time_start": 1696438800,
      "value": 3200.0,
      "time_end": 1696442400
    }
  ]
}
```

Where:
- `time_start` - Unix timestamp for start of hour
- `time_end` - Unix timestamp for end of hour
- `value` - Forecasted value (W for production/consumption, EUR for prices)

## Example Configurations

### Basic Setup (No Authentication)
```yaml
mqtt:
  enabled: true
  broker: 192.168.1.100
  port: 1883
  topic: energy/batcontrol
```

### With Authentication
```yaml
mqtt:
  enabled: true
  broker: mqtt.example.com
  port: 1883
  topic: home/energy/batcontrol
  username: batcontrol_user
  password: secure_password_here
  retry_attempts: 3
  retry_delay: 5
```

### Home Assistant Integration
```yaml
mqtt:
  enabled: true
  broker: homeassistant.local
  port: 1883
  topic: batcontrol
  username: mqtt_user
  password: mqtt_password
  auto_discover_enable: true
  auto_discover_topic: homeassistant
```

## Troubleshooting

### Common Issues

1. **Connection Failed**
   - Check broker hostname/IP and port
   - Verify network connectivity
   - Check username/password if authentication is enabled

2. **Messages Not Appearing**
   - Verify the topic configuration
   - Check broker logs for rejected messages
   - Ensure proper permissions for the MQTT user

3. **Home Assistant Auto-Discovery Not Working**
   - Verify `auto_discover_enable: true`
   - Check that Home Assistant MQTT integration is configured
   - Ensure the discovery topic matches Home Assistant configuration

### Debug Logging

Enable MQTT logging for troubleshooting:

```yaml
mqtt:
  enabled: true
  logger: true  # Enable debug logging
```

This will provide detailed information about MQTT connections, published messages, and any errors in the batcontrol log files.

## Security Considerations

- Always use authentication (`username`/`password`) in production
- TLS encryption is currently not functional (see above) — keep MQTT traffic on a trusted local network
- Limit MQTT user permissions to only necessary topics
- Use strong, unique passwords for MQTT authentication