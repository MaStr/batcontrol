**Optimize your electricity costs by re-charging your PV battery when electricity is cheap and there is not enough solar power available.**
To integrate batcontrol with Home Assistant, use the following repository: [batcontrol_ha_addon](https://github.com/MaStr/batcontrol_ha_addon)

[![Pylint](https://github.com/MaStr/batcontrol/actions/workflows/pylint.yml/badge.svg?branch=main)](https://github.com/MaStr/batcontrol/actions/workflows/pylint.yml)
[![Docker Image CI](https://github.com/MaStr/batcontrol/actions/workflows/docker-image.yml/badge.svg?branch=main)](https://github.com/MaStr/batcontrol/actions/workflows/docker-image.yml)


[Wiki/Documentation](https://github.com/MaStr/batcontrol/wiki)

## Prerequisites:

1. A PV installation with a BYD Battery and a Fronius Gen24 inverter.
2. A zone based pricing, like Octopus, or an EPEX Spot based contract with hourly electricity pricing, like Awattar, Tibber etc. (Get a €50 bonus on sign-up to Tibber using this [link](https://invite.tibber.com/x8ci52nj).)
3. Customer login details to the inverter.

**OR** use the MQTT inverter driver to integrate any battery/inverter system - see [MQTT Inverter Integration](#mqtt-inverter-integration) below.

## MQTT Inverter Integration

Batcontrol can integrate with any battery/inverter system via MQTT topics. This allows you to use batcontrol with systems other than Fronius Gen24 inverters.

### How it works

The MQTT inverter driver enables batcontrol to:
- Read battery state (SOC, capacity, mode) from MQTT topics
- Send control commands (mode changes, charge rate) via MQTT topics
- Dynamically update configuration (min/max SoC, charge rate) via MQTT

### Prerequisites for MQTT Integration

1. An MQTT broker (e.g., Mosquitto) accessible to both batcontrol and your inverter/bridge
2. A bridge script or system that:
   - Publishes battery status to MQTT (as RETAINED messages)
   - Subscribes to control commands from batcontrol
   - Controls your actual inverter/battery system

### Topic Structure

All topics use the pattern: `<base_topic>/<subtopic>`

**Status Topics (MUST be RETAINED):**
- `<base_topic>/status/soc` - State of Charge in % (float, 0-100)
- `<base_topic>/status/capacity` - Battery capacity in Wh (float)
- `<base_topic>/status/mode` - Current mode (string: 'force_charge', 'allow_discharge', 'avoid_discharge')
- `<base_topic>/status/min_soc` - Minimum SoC limit in % (optional)
- `<base_topic>/status/max_soc` - Maximum SoC limit in % (optional)
- `<base_topic>/status/max_charge_rate` - Maximum charge rate in W (optional)

**Command Topics (MUST NOT be retained):**
- `<base_topic>/command/mode` - Set mode (string)
- `<base_topic>/command/charge_rate` - Set charge rate in W (float)

### Configuration Example

```yaml
inverter:
  type: mqtt
  mqtt_broker: 192.168.1.100
  mqtt_port: 1883
  mqtt_user: batcontrol
  mqtt_password: secret
  base_topic: inverter
  capacity: 10000              # Battery capacity in Wh (required)
  min_soc: 10                  # Minimum SoC % (default: 10)
  max_soc: 95                  # Maximum SoC % (default: 95)
  max_grid_charge_rate: 5000   # Maximum charge rate in W (required)
```

### Example Bridge Implementation

Here's a minimal Python bridge example using paho-mqtt:

```python
import paho.mqtt.client as mqtt

# Connect to MQTT broker
client = mqtt.Client()
client.username_pw_set("batcontrol", "secret")
client.connect("192.168.1.100", 1883, 60)

# Publish initial state (RETAINED)
client.publish("inverter/status/soc", "65.5", retain=True)
client.publish("inverter/status/capacity", "10000", retain=True)
client.publish("inverter/status/mode", "allow_discharge", retain=True)

# Subscribe to commands and implement control logic
def on_message(client, userdata, message):
    topic = message.topic
    value = message.payload.decode()
    
    if topic == "inverter/command/mode":
        print(f"Setting mode to: {value}")
        # Implement your inverter control here
        
    elif topic == "inverter/command/charge_rate":
        print(f"Setting charge rate to: {value}W")
        # Implement your charge rate control here

client.on_message = on_message
client.subscribe("inverter/command/#")
client.loop_forever()
```

### Important Notes

- **Status topics MUST be RETAINED** so batcontrol can read state immediately on startup
- **Command topics MUST NOT be retained** to avoid executing stale commands after restart
- Your bridge must re-publish all status topics on reconnect
- See `src/batcontrol/inverter/mqtt_inverter.py` for complete documentation

## Preparations:

# Local installation
## Preparations:
1. Verify the credentials of your inverter for customer or technician access. You can use either.
2. Obtain your Tibber API key from [Tibber Developer](https://developer.tibber.com/), if you use Tibber. 
3. Create your `batcontrol_config.yaml` in the config folder.
4. Configure your electricity tariff and pv installation.
5. Customize your load profile or use the default one and set your annual consumption.
6. If you have run any third-party tools using Modbus or ran some Modbus commands yourself, switch these off and restart the inverter.


## Installation:

## Install:
```sh
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
```


virtualenv venv
source venv/bin/activate
pip install .
```

## Testing:

The project uses pytest for running tests. To run the tests:

```sh
# Install test dependencies
pip install pytest pytest-cov

# Run tests
./run_tests.sh
```

Alternatively, you can run pytest directly:

```sh
python -m pytest tests/
```

To run tests with coverage report:

```sh
python -m pytest tests/ --cov=src/batcontrol
```

## run
```sh
python -m batcontrol # When venv is activated
```

# Docker usage

## Preparations

```
mkdir -p ./config -p ./logs
```

- Download the the latest [batcontrol_config.yaml](https://raw.githubusercontent.com/MaStr/batcontrol/refs/heads/main/config/batcontrol_config_dummy.yaml) sample, adjust and place it to config/batcontrol_config.yaml.

- Use the default load_profile (automatically) or create your own.-
### Plain Docker

```
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  muexx/batcontrol:latest
```

### Docker-compose example

Create docker-compose.yml with the following content:

```
version: '3.8'

services:
  batcontrol:
    image: muexx/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    restart: unless-stopped
```

Then start the container using `docker-compose up -d`.

### Adjusting Timezone

To adjust the timezone for logging and output, set the `TZ` environment variable.

#### Plain Docker

```
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  -e TZ=Europe/Berlin \
  muexx/batcontrol:latest
```

#### Docker-compose example

Add the `TZ` environment variable to your `docker-compose.yml`:

```
version: '3.8'

services:
  batcontrol:
    image: muexx/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped
```

# FAQs

## How are the different config parameters related to each other?

The parameters follow this order:

`MIN_SOC -> Backup-Power-Reserved -> max_charging_from_grid_limit -> always_allow_discharge_limit -> MAX_SOC`

The `always_allow_discharge_limit` parameter overrides any logic and allows the battery to discharge freely according to the inverter's logic.

The `max_charging_from_grid_limit` parameter charges from the minimum up to the `always_allow_discharge_limit`.

## What inverter settings will this change?

1. The software will enable the Solar.API (local network only), without which it cannot work and this will remain enabled on shut down.
2. It will save the current configuration of the battery and its usage schedule (and this will be restored to the original settings on shut down).
3. Charging from grid will be enabled (and this will be restored to the original setting on shut down).
4. The battery settings will be changed on every run of the software according to the three modes.

## Can I run other software that attempts to control the battery and inverter at the same time?

Running other software that controls the inverter or battery is currently not supported and will likely cause conflicts. If you have previously run software that controls the inverter with modbus, disable modbus and restart the inverter before running batcontrol.

## What if I need to change inverter settings while batcontrol is running?

Changing settings through the local webinterface of the inverter is possible, but not recommended. 
The recommended course of action is to shut down batcontrol, make the necessary changes and then restart batcontrol to avoid any conflicts and ensure the correct configuration is saved.

# Uninstall

To uninstall batcontrol and restore the inverter settings follow these steps:
1. Check if batcontrol is running.
2. If batcontrol is not running, check your inverters battery control schedule in the web UI. If it does not look right, batcontrol might have crashed recently. In that case restart batcontrol and then shut it down after it has finished the first run and has gone to sleep.
3. If batcontrol is running, shut it down while it is sleeping to restore the saved inverter settings.
4. Check your inverters local web UI to confirm that the inverter settings have been correctly restored.
5. Remove the software from wherever it was running from or delete the docker container.
6. Disable the Solar API in your inverters web UI, if it is not needed by any other third party software or wallboxes. For example the Fronius Wattpilot will NOT work with the solar API disabled.

# Local development

For running batcontrol off the repository for development puroposes, it is required to uses pip with [Developermode](https://setuptools.pypa.io/en/latest/userguide/development_mode.html)

```sh
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
python -m venv .venv
# Activate your environment with:
#      `source .venv/bin/activate` on Unix/macOS
# or   `.venv\Scripts\activate` on Windows

pip install --editable . 

# Now you have access to your package
# as if it was installed in .venv
python -m batcontrol
```
