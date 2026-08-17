# evcc Integration

Batcontrol can integrate with [evcc (Electric Vehicle Charging Controller)](https://evcc.io/) to intelligently manage battery usage during electric vehicle charging. This integration helps prevent unnecessary battery discharge while your EV is charging, optimizing your overall energy management.

## How It Works

When evcc is charging your electric vehicle, batcontrol can automatically:

1. **Block battery discharge** to prevent the home battery from being used while the EV charges
1. **Temporarily adjust discharge limits** based on evcc's buffer SOC settings
1. **Monitor multiple charging loadpoints** for comprehensive EV charging detection
1. **Restore original settings** when charging stops

## Basic Configuration

```
evcc:
  enabled: true
  broker: localhost
  port: 1883
  status_topic: evcc/status
  loadpoint_topic:
    - evcc/loadpoints/1/charging
    - evcc/loadpoints/2/charging
  block_battery_while_charging: true
```

### Required Parameters

| Parameter         | Type        | Description                                            |
| ----------------- | ----------- | ------------------------------------------------------ |
| `enabled`         | boolean     | Enable or disable evcc integration                     |
| `broker`          | string      | MQTT broker hostname or IP address (same as evcc uses) |
| `port`            | integer     | MQTT broker port (typically 1883 or 8883 for TLS)      |
| `status_topic`    | string      | MQTT topic for evcc online/offline status              |
| `loadpoint_topic` | list/string | MQTT topic(s) for loadpoint charging status            |

### Basic Parameters Explained

- **`status_topic`**: Usually `evcc/status` - monitors if evcc is online/offline
- **`loadpoint_topic`**: Can be a single string or list of topics like:
- `evcc/loadpoints/1/charging` (for loadpoint 1)
- `evcc/loadpoints/2/charging` (for loadpoint 2)
- Add more loadpoints as needed for your setup

## Advanced Configuration

### Authentication

```
evcc:
  username: mqtt_user
  password: mqtt_password
```

### TLS/SSL Support

Batcontrol supports TLS-encrypted MQTT connections to the evcc broker. Configure TLS with flat keys at the same level as other evcc connection parameters:

```
evcc:
  broker: mqtt.home.local
  port: 8883
  tls: true
  cafile: /etc/ssl/certs/ca-certificates.crt   # path to CA certificate (required when tls: true)
  certfile: /etc/ssl/certs/client.crt           # path to client certificate (optional, for mutual TLS)
  keyfile: /etc/ssl/certs/client.key            # path to client private key (optional, for mutual TLS)
```

| Parameter  | Type    | Description                                                                                                   |
| ---------- | ------- | ------------------------------------------------------------------------------------------------------------- |
| `tls`      | boolean | Enable TLS encryption (default: `false`). Use port 8883 for TLS-secured brokers                               |
| `cafile`   | string  | Path to the CA certificate file. **Required** when `tls: true`                                                |
| `certfile` | string  | Path to the client certificate file. Optional; required for mutual TLS                                        |
| `keyfile`  | string  | Path to the client private key file. Optional; required for mutual TLS (must be set together with `certfile`) |

> **Note**: `certfile` and `keyfile` must both be set or both be absent — partial mutual TLS configuration raises an error at startup.

### Battery Management Options

```
evcc:
  block_battery_while_charging: true
  battery_halt_topic: evcc/site/bufferSoc
```

| Parameter                      | Type    | Default            | Description                                                                                                                                                 |
| ------------------------------ | ------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `block_battery_while_charging` | boolean | `true`             | If `true`: Block battery discharge while EV is charging. If `false`: Battery discharge follows normal batcontrol algorithm regardless of EV charging status |
| `battery_halt_topic`           | string  | *unset (disabled)* | Topic for dynamic discharge limit control, e.g. `evcc/site/bufferSoc`                                                                                       |

## Battery Halt Topic (Advanced)

The `battery_halt_topic` enables dynamic battery discharge limit management based on evcc's buffer SOC setting.

### How It Works

1. **Normal Operation**: Batcontrol uses your configured `always_allow_discharge_limit`
1. **EV Charging Starts**:
1. Batcontrol saves current discharge limit
1. Sets new limit based on evcc's `bufferSoc` value
1. **EV Charging Stops**:
1. Restores original discharge limit
1. Returns to normal battery management

### Example Scenario

- Your normal `always_allow_discharge_limit`: `0.20` (20%)
- evcc `bufferSoc` setting: `50` (50%)
- **Result**: While EV charges, battery discharge is blocked above 50% SOC instead of 20%

## MQTT Topics Monitored

Batcontrol subscribes to the following evcc MQTT topics:

### Status Monitoring

- `evcc/status` - evcc online/offline status (`online`/`offline`)

### Charging Detection

- `evcc/loadpoints/1/charging` - Loadpoint 1 charging status (`true`/`false`)
- `evcc/loadpoints/2/charging` - Loadpoint 2 charging status (`true`/`false`)
- Additional loadpoints as configured

### Loadpoint Mode and Connection State (derived automatically)

For every configured `.../charging` topic, batcontrol additionally subscribes to the sibling topics:

- `evcc/loadpoints/1/mode` - Loadpoint charging mode (`pv`, `now`, `minpv`, `off`)
- `evcc/loadpoints/1/connected` - Whether an EV is connected (`true`/`false`)

These are used by [peak shaving](https://mastr.github.io/batcontrol/features/peak-shaving/index.md): peak shaving is automatically disabled while evcc is actively charging or while an EV is connected in PV mode, and re-enabled when the EV disconnects or the mode changes.

### Buffer SOC (Optional)

- `evcc/site/bufferSoc` - Dynamic discharge threshold (integer 0-100)

## Behavior During EV Charging

### When Charging Starts

1. **Battery Blocking**: If `block_battery_while_charging: true`, battery discharge is blocked. If `false`, battery discharge continues according to normal batcontrol algorithm
1. **Limit Adjustment**: If `battery_halt_topic` configured, discharge limit is temporarily set to buffer SOC
1. **Logging**: Batcontrol logs: `"evcc is charging, set block"` (only if blocking enabled)

### When Charging Stops

1. **Battery Unblocking**: Battery discharge blocking is removed
1. **Limit Restoration**: Original discharge limit is restored
1. **Logging**: Batcontrol logs: `"evcc is not charging, remove block"`

### When evcc Goes Offline

1. **Safety Mechanism**: If evcc goes offline while charging, blocks are automatically removed
1. **Limit Restoration**: Original settings are restored
1. **Logging**: Batcontrol logs: `"evcc went offline"` and `"evcc was charging, remove block"`

## Example Configurations

### Single Loadpoint Setup

```
evcc:
  enabled: true
  broker: 192.168.1.100
  port: 1883
  status_topic: evcc/status
  loadpoint_topic: evcc/loadpoints/1/charging
  block_battery_while_charging: true
```

### Multiple Loadpoints with Authentication

```
evcc:
  enabled: true
  broker: evcc.local
  port: 1883
  status_topic: evcc/status
  loadpoint_topic:
    - evcc/loadpoints/1/charging
    - evcc/loadpoints/2/charging
  block_battery_while_charging: true
  username: batcontrol
  password: secure_password
```

### Advanced Setup with Buffer SOC

```
evcc:
  enabled: true
  broker: mqtt.home.local
  port: 1883
  status_topic: evcc/status
  loadpoint_topic:
    - evcc/loadpoints/1/charging
  block_battery_while_charging: true
  battery_halt_topic: evcc/site/bufferSoc
  username: mqtt_user
  password: mqtt_pass
```

### Monitoring Only (No Battery Blocking)

```
evcc:
  enabled: true
  broker: localhost
  port: 1883
  status_topic: evcc/status
  loadpoint_topic:
    - evcc/loadpoints/1/charging
  block_battery_while_charging: false  # Battery discharge follows normal batcontrol algorithm
```

**Use Case**: This configuration allows you to monitor EV charging status without affecting battery discharge behavior. The battery will charge/discharge according to batcontrol's normal price-based algorithm, regardless of whether the EV is charging.

## Troubleshooting

### Common Issues

1. **Connection Failed**
1. Verify evcc MQTT broker settings match batcontrol configuration
1. Check network connectivity between batcontrol and MQTT broker
1. Ensure MQTT credentials are correct
1. **Charging Not Detected**
1. Verify loadpoint topic names match your evcc configuration
1. Check evcc MQTT API is enabled and publishing messages
1. Use MQTT client to monitor topics: `mosquitto_sub -h localhost -t evcc/+/+`
1. **Buffer SOC Not Working**
1. Ensure `battery_halt_topic` matches evcc's bufferSoc topic
1. Verify evcc is publishing bufferSoc values
1. Check logs for: `"Enabling battery threshold management"`

### Debug Logging

Enable detailed logging for troubleshooting:

```
evcc:
  enabled: true
  # ... other config ...
  logger: true  # Enable MQTT debug logging
```

### Log Messages to Watch For

- `"evcc is online"` - evcc status detection working
- `"Loadpoint evcc/loadpoints/1/charging is charging"` - charging detection
- `"evcc is charging, set block"` - battery blocking activated
- `"Enabling battery threshold management"` - buffer SOC feature active
- `"New battery_halt value: 50"` - buffer SOC updated

## Integration with Home Assistant

When using both batcontrol and evcc with Home Assistant:

1. Use the same MQTT broker for all three systems
1. Configure evcc auto-discovery: `homeassistant` topic
1. Configure batcontrol MQTT auto-discovery for the same topic
1. Both systems will create entities in Home Assistant automatically

## Security Considerations

- Use authentication for production MQTT brokers
- Use TLS encryption (`tls: true` with a valid `cafile`) when the MQTT broker is not on a fully trusted local network
- Ensure MQTT user has appropriate topic permissions
- Keep MQTT credentials secure and unique
