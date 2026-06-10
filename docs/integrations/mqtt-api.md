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

> ⚠️ **Note**: TLS/SSL support is currently **untested**. Use with caution in production environments.

```yaml
mqtt:
  tls: true
  cafile: /etc/ssl/certs/ca-certificates.crt
  certfile: /etc/ssl/certs/client.crt
  keyfile: /etc/ssl/certs/client.key
  tls_version: tlsv1.2
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tls` | boolean | `false` | Enable TLS/SSL encryption |
| `cafile` | string | `/etc/ssl/certs/ca-certificates.crt` | Path to Certificate Authority file |
| `certfile` | string | `/etc/ssl/certs/client.crt` | Path to client certificate file |
| `keyfile` | string | `/etc/ssl/certs/client.key` | Path to client private key file |
| `tls_version` | string | `tlsv1.2` | TLS version to use (`tlsv1.2`, `tlsv1.3`) |

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
  - `10` = Discharge Allowed
- `house/batcontrol/charge_rate` - Current charge rate in W
- `house/batcontrol/discharge_blocked` - Whether discharge is blocked (`True`/`False`)

### Battery Information
- `house/batcontrol/SOC` - State of Charge in % (formatted as 3-digit integer, e.g., `069`)
- `house/batcontrol/max_energy_capacity` - Maximum battery capacity in Wh
- `house/batcontrol/stored_energy_capacity` - Energy stored in battery in Wh
- `house/batcontrol/stored_usable_energy_capacity` - Usable energy stored in battery in Wh (considering min SOC)
- `house/batcontrol/reserved_energy_capacity` - Energy reserved for discharge in Wh

### Configuration Limits
- `house/batcontrol/always_allow_discharge_limit` - Always discharge limit (0.0-1.0)
- `house/batcontrol/always_allow_discharge_limit_percent` - Always discharge limit in %
- `house/batcontrol/always_allow_discharge_limit_capacity` - Always discharge limit in Wh
- `house/batcontrol/max_charging_from_grid_limit` - Max charging from grid limit (0.0-1.0)
- `house/batcontrol/max_charging_from_grid_limit_percent` - Max charging from grid limit in %

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
- `house/batcontrol/mode/set` - Set operational mode (send `-1`, `0`, or `10`)
- `house/batcontrol/charge_rate/set` - Set charge rate in W (automatically sets mode to `-1`)

### Configuration
- `house/batcontrol/always_allow_discharge_limit/set` - Set always discharge limit (0.0-1.0)
- `house/batcontrol/max_charging_from_grid_limit/set` - Set max charging from grid limit (0.0-1.0)
- `house/batcontrol/min_price_difference/set` - Set minimum price difference in EUR

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

### Secure TLS Setup (Untested)
```yaml
mqtt:
  enabled: true
  broker: secure-mqtt.example.com
  port: 8883
  topic: batcontrol
  username: secure_user
  password: secure_password
  tls: true
  cafile: /path/to/ca-cert.pem
  certfile: /path/to/client-cert.pem
  keyfile: /path/to/client-key.pem
  tls_version: tlsv1.3
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
- Consider using TLS encryption for remote connections (though currently untested)
- Limit MQTT user permissions to only necessary topics
- Use strong, unique passwords for MQTT authentication