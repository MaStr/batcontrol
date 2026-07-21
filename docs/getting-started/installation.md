# Installation

Batcontrol can be run as a Docker container, a Home Assistant add-on, or directly from a local Python environment.

## What You Need

Collect the following before you start configuring.

### Inverter access

- Local IP address or hostname of the inverter on your network
- Customer or technician login credentials

### Electricity tariff (one of the following)

- **Dynamic provider** (Tibber, aWATTar, evcc, EnergyForecast.de): API key if required by the provider
- **Static tariff**: your electricity price in €/kWh — sufficient for peak shaving without dynamic charging

### Solar forecast (one of the following)

- **Forecast.Solar** (default, free, no account required): GPS coordinates of your PV installation, roof azimuth (−90 = East, 0 = South, 90 = West), tilt angle in degrees (0 = horizontal, 90 = vertical), and system size in kWp
- **Solar-Prognose.de**: API key
- **evcc**: a running evcc instance with solar forecast data
- **Home Assistant Solar Forecast ML**: a running Home Assistant instance with the Solar Forecast ML integration and the entity ID of the forecast sensor

### Consumption forecast

Your annual electricity consumption in kWh. The default load profile covers typical household patterns, so this single number is enough to get started.

## Configuration File

Docker (plain or Compose) and local Python installs use a `batcontrol_config.yaml` in the `config/` directory. If none is present when the Docker container starts for the first time, batcontrol copies a sample configuration that uses a dummy inverter. Home Assistant add-on configuration is done via the add-on UI (no local YAML file).

Once you are ready, edit `config/batcontrol_config.yaml` and set your inverter type, credentials, tariff provider, and PV details. See [Main Configuration](../configuration/batcontrol-configuration.md) and the other configuration pages for a full reference.

## Docker

This is the recommended way to run batcontrol for most users.

### Setup

```sh
mkdir -p ./config ./logs
```

### Plain Docker

```sh
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  mastr950/batcontrol:latest
```

### Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  batcontrol:
    image: mastr950/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    restart: unless-stopped
```

Then start the container:

```sh
docker compose up -d
```

### Timezone

By default the container uses UTC. Set the `TZ` environment variable to match your local time zone, which affects log timestamps and time-based scheduling.

Plain Docker:

```sh
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  -e TZ=Europe/Berlin \
  mastr950/batcontrol:latest
```

Docker Compose:

```yaml
services:
  batcontrol:
    image: mastr950/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped
```

## Home Assistant Add-on

Add the [batcontrol_ha_addon](https://github.com/MaStr/batcontrol_ha_addon) repository to your Home Assistant add-on store. Configuration is done through the add-on UI rather than a YAML file.

## Local Python

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
uv venv --python 3.13 --allow-existing
source .venv/bin/activate
uv pip install .
```

Place your `batcontrol_config.yaml` in the `config/` directory, then run:

```sh
python -m batcontrol
```

## Inverter Preparation

Before the first run, verify the following:

- Confirm your inverter login credentials (customer or technician access both work).
- If you have previously run any third-party tools that use Modbus, or executed Modbus commands directly, disable Modbus and restart the inverter before starting batcontrol.

Batcontrol will enable the Solar.API on first run (local network only), enable charging from grid, and save the current battery and time-of-use configuration, which it restores on a clean shutdown. See [Inverter Configuration](../configuration/inverter-configuration.md#changes-batcontrol-makes-to-the-inverter-configuration) for the full list of changes.

## Next Steps

After the first successful start, work through this in order:

1. **Check the logs.** Confirm batcontrol connects to your inverter and enters the control loop without errors. The log shows the current mode, the next decision, and the price/forecast values used.

2. **Switch to your real inverter.** Edit `config/batcontrol_config.yaml`, change `inverter.type` from `dummy` to `fronius_gen24` (or `fronius-modbus`), and add your inverter address and credentials.

3. **Set your annual consumption.** Update `consumption_forecast.csv.annual_consumption` with your actual value in kWh. This scales the default load profile to your household.

4. **Configure your tariff provider.** If you use a dynamic provider (Tibber, aWATTar, etc.), set the correct type and API key. For a flat rate, use `tariff_zones` with a single zone price — batcontrol will still run peak shaving.

5. **Review battery charge limits.** The defaults (`max_charging_from_grid_limit: 89%`, `always_allow_discharge_limit: 90%`) are conservative starting values. Adjust them to match your usage patterns after a few days of observation.

6. **Enable peak shaving** (optional). If your PV installation frequently reaches full battery charge before midday, enable peak shaving to spread the charging and keep buffer capacity for the afternoon. Requires `battery_control.type: next` and `peak_shaving.enabled: true`. See [Peak Shaving](../features/peak-shaving.md).

7. **Connect to Home Assistant** (optional). Enable the MQTT API to get real-time state, price, and forecast data as Home Assistant entities, and to override battery limits at runtime. See [MQTT API](../integrations/mqtt-api.md).

8. **Connect evcc** (optional). If you charge an electric vehicle, the evcc integration can hold battery discharge while the car is charging. See [evcc Connection](../integrations/evcc-connection.md).

## Uninstalling

Batcontrol saves your inverter's original battery settings and restores them on a clean shutdown. To uninstall safely:

1. If batcontrol is currently running, wait until it finishes a cycle and goes to sleep, then shut it down. This ensures the saved inverter settings are restored.
2. If batcontrol is not running and you suspect it crashed during a run, restart it, let it complete one cycle and sleep, then shut it down cleanly.
3. Verify that the battery control schedule in your inverter's local web UI looks correct after shutdown.
4. Remove the Docker container or the local installation directory.
5. Disable the Solar API in your inverter's web UI if no other software requires it. Note: the Fronius Wattpilot wallbox requires the Solar API to be enabled.

## Development Setup

For working on the batcontrol source code, use an editable install:

```sh
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
uv venv --python 3.13 --allow-existing
source .venv/bin/activate
uv pip install --editable '.[test]'
```

Run the test suite:

```sh
./run_tests.sh
# or
python -m pytest tests/
# with coverage:
python -m pytest tests/ --cov=src/batcontrol
```
