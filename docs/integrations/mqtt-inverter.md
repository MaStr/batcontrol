# MQTT Inverter

The MQTT Inverter driver enables batcontrol to integrate with any battery/inverter system via MQTT topics. It acts as a generic bridge, allowing external systems to provide battery state information and receive control commands over MQTT.

## Architecture Overview

The MQTT inverter driver uses batcontrol's shared MQTT connection (configured in the main batcontrol MQTT API section). It does NOT create a separate MQTT client. This design ensures:

- Single MQTT connection per batcontrol instance
- Consistent MQTT broker configuration
- Shared connection pool and resources
- Unified logging and error handling

## Topic Structure

All topics follow the pattern: `<batcontrol_base_topic>/inverters/$num/<subtopic>`

Where:
- `<batcontrol_base_topic>` is the MQTT base topic from your main MQTT configuration
- `$num` is the inverter number (e.g., 0, 1, 2), **not** the literal string "$num"
- `<subtopic>` is the specific status or command topic

**Example:** If your base_topic is "batcontrol" and inverter number is 0, topics would be:
- `batcontrol/inverters/0/status/soc`
- `batcontrol/inverters/0/command/mode`

## Status Topics (Inverter → batcontrol)

These topics **MUST be published as RETAINED** by your external inverter/bridge system:

| Topic | Description | Type | Retention |
|-------|-------------|------|-----------|
| `<base>/status/capacity` | Battery capacity in Wh | float | **RETAINED** (required) |
| `<base>/status/min_soc` | Minimum SoC limit in % (0-100) | float | **RETAINED** (optional) |
| `<base>/status/max_soc` | Maximum SoC limit in % (0-100) | float | **RETAINED** (optional) |
| `<base>/status/max_charge_rate` | Maximum charge rate in W | float | **RETAINED** (optional) |

These topics should be **updated at least every 2 minutes** to ensure fresh data:

| Topic | Description | Type | Retention |
|-------|-------------|------|-----------|
| `<base>/status/soc` | Current State of Charge in % (0-100) | float | Non-retained (updated frequently) |

## Command Topics (batcontrol → Inverter)

These topics are published by batcontrol and **MUST NOT be retained**:

| Topic | Description | Values |
|-------|-------------|--------|
| `<base>/command/mode` | Set operating mode | `force_charge`, `allow_discharge`, `avoid_discharge` |
| `<base>/command/charge_rate` | Set charge rate in W | float |

## Why Retention Matters

⚠️ **Critical for proper operation:**

- **Status topics MUST be RETAINED** so batcontrol can read the current state immediately on startup
- **Command topics MUST NOT be retained** to avoid re-executing stale commands on reconnect
- If command topics are retained, the inverter may execute old commands after restart, causing unexpected behavior

## Configuration

### Main MQTT Connection

Configure the MQTT connection in batcontrol's main MQTT API section (not in the inverter configuration):

```yaml
mqtt:
  broker: 192.168.1.100
  port: 1883
  user: batcontrol
  password: secret
  base_topic: batcontrol  # Base topic for all MQTT messages
```

### Inverter Configuration

```yaml
inverter:
  type: mqtt
  capacity: 10000              # Battery capacity in Wh (required)
  min_soc: 5                   # Minimum SoC % (default: 5)
  max_soc: 100                 # Maximum SoC % (default: 100)
  max_grid_charge_rate: 5000   # Maximum charge rate in W (required)
  cache_ttl: 120               # Cache TTL for SOC values in seconds (default: 120)
  base_topic: batcontrol/inverter/0  # Optional: override default topic structure
```

### Configuration Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `type` | Yes | - | Must be `mqtt` |
| `capacity` | Yes | - | Battery capacity in Wh |
| `max_grid_charge_rate` | Yes | - | Maximum charge rate from grid in W |
| `min_soc` | No | 5 | Minimum State of Charge in % |
| `max_soc` | No | 100 | Maximum State of Charge in % |
| `cache_ttl` | No | 120 | Cache TTL for SOC values in seconds |
| `base_topic` | No | `<mqtt.base_topic>/inverters/<num>` | Custom base topic for inverter MQTT messages |

## External Bridge Requirements

Your external system (inverter bridge script, inverter firmware, etc.) must:

1. **Publish battery status as RETAINED messages:**
   - Battery capacity in Wh (required)
   - Optional: min_soc, max_soc, max_charge_rate

2. **Publish current SOC regularly (at least every 2 minutes):**
   - Current State of Charge as a normal message (can be retained or non-retained)

3. **Subscribe to command topics (non-retained):**
   - Mode changes (`force_charge`, `allow_discharge`, `avoid_discharge`)
   - Charge rate adjustments

4. **Handle reconnection gracefully:**
   - Re-publish all status topics as RETAINED on reconnect
   - Don't retain command topics to avoid stale command execution

## Example Bridge Implementation

Here's a simple Python example using paho-mqtt to bridge your inverter to batcontrol:

```python
import paho.mqtt.client as mqtt
import time

# Configuration
MQTT_BROKER = "192.168.1.100"
MQTT_PORT = 1883
MQTT_USER = "batcontrol"
MQTT_PASSWORD = "secret"
BASE_TOPIC = "batcontrol/inverters/0"

def on_connect(client, userdata, flags, rc):
    """Called when connected to MQTT broker"""
    print(f"Connected with result code {rc}")
    
    # Publish initial state (RETAINED)
    client.publish(f"{BASE_TOPIC}/status/capacity", "10000", retain=True)
    client.publish(f"{BASE_TOPIC}/status/min_soc", "5", retain=True)
    client.publish(f"{BASE_TOPIC}/status/max_soc", "100", retain=True)
    client.publish(f"{BASE_TOPIC}/status/max_charge_rate", "5000", retain=True)
    
    # Subscribe to commands
    client.subscribe(f"{BASE_TOPIC}/command/#")
    print(f"Subscribed to {BASE_TOPIC}/command/#")

def on_message(client, userdata, message):
    """Handle incoming commands from batcontrol"""
    topic = message.topic
    value = message.payload.decode()
    
    print(f"Received: {topic} = {value}")
    
    if topic == f"{BASE_TOPIC}/command/mode":
        print(f"Setting mode to: {value}")
        # TODO: Implement your inverter control here
        # Examples:
        # - force_charge: Enable grid charging
        # - allow_discharge: Normal operation
        # - avoid_discharge: Prevent battery discharge
        
    elif topic == f"{BASE_TOPIC}/command/charge_rate":
        print(f"Setting charge rate to: {value}W")
        # TODO: Implement your charge rate control here

def publish_soc(client, soc_value):
    """Publish current State of Charge"""
    client.publish(f"{BASE_TOPIC}/status/soc", str(soc_value))

# Create MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message

# Connect to broker
client.connect(MQTT_BROKER, MQTT_PORT, 60)

# Start network loop in background
client.loop_start()

# Main loop: Periodically publish SOC
try:
    while True:
        # TODO: Read actual SOC from your inverter
        soc = 65.5  # Example value
        publish_soc(client, soc)
        
        time.sleep(60)  # Update every 60 seconds
        
except KeyboardInterrupt:
    print("Shutting down...")
    client.loop_stop()
    client.disconnect()
```

## Operating Modes

The MQTT inverter supports three operating modes:

### force_charge
Forces the battery to charge from grid at the specified rate. Used during low-price periods.

```
Topic: <base>/command/mode
Payload: force_charge

Topic: <base>/command/charge_rate
Payload: 5000  # Charge at 5000W
```

### allow_discharge
Normal operation mode. Battery can charge from PV and discharge to supply loads.

```
Topic: <base>/command/mode
Payload: allow_discharge
```

### avoid_discharge
Prevents battery discharge. Battery can still charge from PV but won't discharge to supply loads. Used to preserve battery for later use.

```
Topic: <base>/command/mode
Payload: avoid_discharge
```

## Home Assistant Integration

The MQTT inverter automatically publishes Home Assistant MQTT Discovery messages for all status and command topics. This allows you to monitor your inverter's status and commands in Home Assistant without manual configuration.

Discovered entities include:
- MQTT Inverter Status SOC
- MQTT Inverter Status Capacity
- MQTT Inverter Status Min SOC
- MQTT Inverter Status Max SOC
- MQTT Inverter Status Max Charge Rate
- MQTT Inverter Command Mode
- MQTT Inverter Command Charge Rate


## Limitations

- **No bidirectional acknowledgment:** batcontrol assumes commands succeed immediately
- **No auto-discovery:** All topics must follow the documented structure exactly
- **Network dependency:** MQTT broker must be reliable and accessible
- **Initial state required:** Status topics must be available at batcontrol startup
- **Clock synchronization:** Ensure time is synchronized between batcontrol and your inverter system for accurate scheduling
- **QoS 1 for commands:** Guarantees delivery but not exactly-once semantics (commands may be delivered multiple times)

## Advanced Configuration

### Custom Topic Structure

By default, the MQTT inverter uses the topic structure `<mqtt.base_topic>/inverters/<inverter_num>`. You can override this:

```yaml
inverter:
  type: mqtt
  base_topic: custom/battery/system  # Use custom topic structure
  capacity: 10000
  max_grid_charge_rate: 5000
```

This would result in topics like:
- `custom/battery/system/status/soc`
- `custom/battery/system/command/mode`

Each inverter will have its own set of MQTT topics.

## See Also

- [Inverter Configuration](../configuration/inverter-configuration.md) - General inverter configuration options
- [MQTT API](mqtt-api.md) - Main MQTT API configuration and topics
- [How batcontrol works](../getting-started/how-batcontrol-works.md) - Understanding batcontrol's operation
