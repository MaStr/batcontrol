# EVCC Integration

Batcontrol can integrate with [evcc (Electric Vehicle Charging Controller)](https://evcc.io/) to intelligently manage battery usage during electric vehicle charging. This integration helps prevent unnecessary battery discharge while your EV is charging, optimizing your overall energy management.

## How It Works

When evcc is charging your electric vehicle, batcontrol can automatically:

1. **Block battery discharge** to prevent the home battery from being used while the EV charges
2. **Temporarily adjust discharge limits** based on evcc's buffer SOC settings
3. **Monitor multiple charging loadpoints** for comprehensive EV charging detection
4. **Restore original settings** when charging stops

## Basic Configuration

```yaml
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

| Parameter | Type | Description |
|-----------|------|-------------|
| `enabled` | boolean | Enable or disable evcc integration |
| `broker` | string | MQTT broker hostname or IP address (same as evcc uses) |
| `port` | integer | MQTT broker port (typically 1883 or 8883 for TLS) |
| `status_topic` | string | MQTT topic for evcc online/offline status |
| `loadpoint_topic` | list/string | MQTT topic(s) for loadpoint charging status |

### Basic Parameters Explained

- **`status_topic`**: Usually `evcc/status` - monitors if evcc is online/offline
- **`loadpoint_topic`**: Can be a single string or list of topics like:
  - `evcc/loadpoints/1/charging` (for loadpoint 1)
  - `evcc/loadpoints/2/charging` (for loadpoint 2)
  - Add more loadpoints as needed for your setup

## Advanced Configuration

### Authentication

```yaml
evcc:
  username: mqtt_user
  password: mqtt_password
```

### TLS/SSL Support

> ⚠️ **Note**: TLS/SSL support is currently **untested**. Use with caution in production environments.

```yaml
evcc:
  tls: true
  cafile: /etc/ssl/certs/ca-certificates.crt
  certfile: /etc/ssl/certs/client.crt
  keyfile: /etc/ssl/certs/client.key
  tls_version: tlsv1.2
```

### Battery Management Options

```yaml
evcc:
  block_battery_while_charging: true
  battery_halt_topic: evcc/site/bufferSoc
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_battery_while_charging` | boolean | `true` | If `true`: Block battery discharge while EV is charging. If `false`: Battery discharge follows normal batcontrol algorithm regardless of EV charging status |
| `battery_halt_topic` | string | `evcc/site/bufferSoc` | Topic for dynamic discharge limit control |

## Battery Halt Topic (Advanced)

The `battery_halt_topic` enables dynamic battery discharge limit management based on evcc's buffer SOC setting.

### How It Works

1. **Normal Operation**: Batcontrol uses your configured `always_allow_discharge_limit`
2. **EV Charging Starts**:
   - Batcontrol saves current discharge limit
   - Sets new limit based on evcc's `bufferSoc` value
3. **EV Charging Stops**:
   - Restores original discharge limit
   - Returns to normal battery management

### Example Scenario

- Your normal `always_allow_discharge_limit`: `0.20` (20%)
- EVCC `bufferSoc` setting: `50` (50%)
- **Result**: While EV charges, battery discharge is blocked above 50% SOC instead of 20%

## MQTT Topics Monitored

Batcontrol subscribes to the following evcc MQTT topics:

### Status Monitoring
- `evcc/status` - evcc online/offline status (`online`/`offline`)

### Charging Detection
- `evcc/loadpoints/1/charging` - Loadpoint 1 charging status (`true`/`false`)
- `evcc/loadpoints/2/charging` - Loadpoint 2 charging status (`true`/`false`)
- Additional loadpoints as configured

### Buffer SOC (Optional)
- `evcc/site/bufferSoc` - Dynamic discharge threshold (integer 0-100)

## Behavior During EV Charging

### When Charging Starts
1. **Battery Blocking**: If `block_battery_while_charging: true`, battery discharge is blocked. If `false`, battery discharge continues according to normal batcontrol algorithm
2. **Limit Adjustment**: If `battery_halt_topic` configured, discharge limit is temporarily set to buffer SOC
3. **Logging**: Batcontrol logs: `"evcc is charging, set block"` (only if blocking enabled)

### When Charging Stops
1. **Battery Unblocking**: Battery discharge blocking is removed
2. **Limit Restoration**: Original discharge limit is restored
3. **Logging**: Batcontrol logs: `"evcc is not charging, remove block"`

### When EVCC Goes Offline
1. **Safety Mechanism**: If evcc goes offline while charging, blocks are automatically removed
2. **Limit Restoration**: Original settings are restored
3. **Logging**: Batcontrol logs: `"evcc went offline"` and `"evcc was charging, remove block"`

## Example Configurations

### Single Loadpoint Setup
```yaml
evcc:
  enabled: true
  broker: 192.168.1.100
  port: 1883
  status_topic: evcc/status
  loadpoint_topic: evcc/loadpoints/1/charging
  block_battery_while_charging: true
```

### Multiple Loadpoints with Authentication
```yaml
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
```yaml
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
```yaml
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
   - Verify evcc MQTT broker settings match batcontrol configuration
   - Check network connectivity between batcontrol and MQTT broker
   - Ensure MQTT credentials are correct

2. **Charging Not Detected**
   - Verify loadpoint topic names match your evcc configuration
   - Check evcc MQTT API is enabled and publishing messages
   - Use MQTT client to monitor topics: `mosquitto_sub -h localhost -t evcc/+/+`

3. **Buffer SOC Not Working**
   - Ensure `battery_halt_topic` matches evcc's bufferSoc topic
   - Verify evcc is publishing bufferSoc values
   - Check logs for: `"Enabling battery threshold management"`

### Debug Logging

Enable detailed logging for troubleshooting:

```yaml
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
2. Configure evcc auto-discovery: `homeassistant` topic
3. Configure batcontrol MQTT auto-discovery for the same topic
4. Both systems will create entities in Home Assistant automatically

## Security Considerations

- Use authentication for production MQTT brokers
- Consider TLS encryption for remote connections (though currently untested)
- Ensure MQTT user has appropriate topic permissions
- Keep MQTT credentials secure and unique