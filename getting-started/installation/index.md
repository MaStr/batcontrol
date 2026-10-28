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

Once you are ready, edit `config/batcontrol_config.yaml` and set your inverter type, credentials, tariff provider, and PV details. See [Main Configuration](https://mastr.github.io/batcontrol/configuration/batcontrol-configuration/index.md) and the other configuration pages for a full reference.

## Docker

This is the recommended way to run batcontrol for most users.

### Setup

```
mkdir -p ./config ./logs ./data
```

### Plain Docker

```
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  mastr950/batcontrol:latest
```

### Docker Compose

Create a `docker-compose.yml`:

```
services:
  batcontrol:
    image: mastr950/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - ./data:/app/data
    restart: unless-stopped
```

Then start the container:

```
docker compose up -d
```

### Dashboard and API

batcontrol can serve a read-only [dashboard](https://mastr.github.io/batcontrol/features/dashboard/index.md) and [HTTP API](https://mastr.github.io/batcontrol/integrations/rest-api/index.md) on port 8080. It is off by default; to use it, set this in `batcontrol_config.yaml`:

```
api:
  enabled: true
  host: 0.0.0.0   # 127.0.0.1 would be unreachable from outside the container
  port: 8080
```

and publish the port:

```
docker run -d \
  --name batcontrol \
  -p 8080:8080 \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  mastr950/batcontrol:latest
```

```
services:
  batcontrol:
    image: mastr950/batcontrol:latest
    ports:
      - "8080:8080"
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - ./data:/app/data
    restart: unless-stopped
```

The dashboard is then at `http://<host>:8080/`. The interactive API documentation at `http://<host>:8080/docs` is off by default; enable it with `api.docs_enabled: true`. A published port has no authentication of its own — set `api.auth_token` or keep it on a trusted network.

### Timezone

By default the container uses UTC. Set the `TZ` environment variable to match your local time zone, which affects log timestamps and time-based scheduling.

Plain Docker:

```
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  -e TZ=Europe/Berlin \
  mastr950/batcontrol:latest
```

Docker Compose:

```
services:
  batcontrol:
    image: mastr950/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - ./data:/app/data
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped
```

## Home Assistant Add-on

Add the [batcontrol_ha_addon](https://github.com/MaStr/batcontrol_ha_addon) repository to your Home Assistant add-on store. Configuration is done through the add-on UI rather than a YAML file.

## Local Python

Requires Python 3.11 or newer (3.11 - 3.14 are supported and tested in CI; the Docker image ships Python 3.14) and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
uv venv --python 3.14 --allow-existing
source .venv/bin/activate
uv pip install .
```

Place your `batcontrol_config.yaml` in the `config/` directory, then run:

```
python -m batcontrol
```

## Application data and provider caches

Batcontrol uses one shared data directory, independent of the configuration file:

| Deployment            | Data directory                                      |
| --------------------- | --------------------------------------------------- |
| Local Python          | `./data`, relative to the working directory         |
| Docker                | `/app/data`                                         |
| Home Assistant add-on | `/data/data`, inside HA's persistent `/data` volume |

Use `--data-dir PATH` to select another directory. For a service, an absolute path avoids depending on its working directory. No YAML or HA schema setting is needed.

All solar providers and tariff providers using `RelaxedCaching` save their latest raw responses under `data/cache/solar/` and `data/cache/tariff/`. Providers have separate subdirectories, and each solar installation has one file named with a hash of its name. Configuration and credentials are not copied into cache files; provider responses may still contain private installation or household data. Files are atomically replaced with owner-only (`0600`) permissions.

On startup, providers load usable cached responses and attempt their normal refresh. If the provider is unavailable, the cached response remains usable only until its original TTL expires. The default TTL is 12 hours; the HA solar provider also honors its existing `cache_ttl_hours` setting. Restart never renews the fetch timestamp. Missing, expired, corrupt, or unwritable cache files leave the existing in-memory cache and no-data fallback behavior in place.

Persist raw timestamped responses rather than relative forecast slots so the provider can recalculate the remaining forecast at the current time. HA solar's automatic unit detection can use unit metadata from a usable cached response. Local CSV consumption profiles and the separate dynamic network-fee fetcher are outside this cache's scope.

For Docker, use a bind mount as shown above or a named volume at `/app/data`. The Dockerfile also declares that path as a volume, but an automatically created anonymous volume is not reliably reused when replacing a container.

### Clearing the cache

After stopping any existing controller instance, start once with:

```
python -m batcontrol --data-dir ./data --clear-cache
```

`--clear-cache` removes only the `cache` subdirectory, including entries for inactive providers, before providers are initialized. Batcontrol then starts normally and can save new responses. If deletion fails, startup aborts rather than silently loading the old cache. Omit the flag on subsequent starts.

Both container entrypoints forward CLI arguments. For a one-time Docker Compose startup, temporarily set:

```
command: ["/bin/sh", "/app/entrypoint.sh", "--clear-cache"]
```

Remove that command after the maintenance start so future restarts retain cache. For deployments without access to startup arguments, including HA's add-on UI, stop the controller and delete only the `cache` subdirectory from its persistent data volume, then start it again. Never run a second controller to clear cache.

Clear the cache after changing a site's geometry, a forecast sensor, or a tariff account/market if the previous response no longer describes that configuration. Provider separation prevents cross-provider reuse; cache identity does not hash all configuration values.

## Inverter Preparation

Before the first run, verify the following:

- Confirm your inverter login credentials (customer or technician access both work).
- If you have previously run any third-party tools that use Modbus, or executed Modbus commands directly, disable Modbus and restart the inverter before starting batcontrol.

If you are using the `fronius_gen24` backend, batcontrol will enable the Solar.API at startup (local network only), enable charging from grid, and save the current battery and time-of-use configuration, which it restores on a clean shutdown. See [Inverter Configuration](https://mastr.github.io/batcontrol/configuration/inverter-configuration/#changes-batcontrol-makes-to-the-inverter-configuration) for the full list of changes.

## Next Steps

After the first successful start, work through this in order:

1. **Check the logs.** Confirm batcontrol connects to your inverter and enters the control loop without errors. The log shows the current mode, the next decision, and the price/forecast values used.
1. **Switch to your real inverter.** Edit `config/batcontrol_config.yaml`, change `inverter.type` from `dummy` to `fronius_gen24` (or `fronius-modbus`), and add your inverter address and credentials.
1. **Set your annual consumption.** Update `consumption_forecast.csv.annual_consumption` with your actual value in kWh. This scales the default load profile to your household.
1. **Configure your tariff provider.** If you use a dynamic provider (Tibber, aWATTar, etc.), set the correct type and API key. For a flat rate, use `tariff_zones` with a single zone price — batcontrol will still run peak shaving.
1. **Review battery charge limits.** The defaults (`max_charging_from_grid_limit: 89%`, `always_allow_discharge_limit: 90%`) are conservative starting values. Adjust them to match your usage patterns after a few days of observation.
1. **Enable peak shaving** (optional). If your PV installation frequently reaches full battery charge before midday, enable peak shaving to spread the charging and keep buffer capacity for the afternoon. Requires `peak_shaving.enabled: true`. See [Peak Shaving](https://mastr.github.io/batcontrol/features/peak-shaving/index.md).
1. **Connect to Home Assistant** (optional). Enable the MQTT API to get real-time state, price, and forecast data as Home Assistant entities, and to override battery limits at runtime. See [MQTT API](https://mastr.github.io/batcontrol/integrations/mqtt-api/index.md).
1. **Connect evcc** (optional). If you charge an electric vehicle, the evcc integration can hold battery discharge while the car is charging. See [evcc Connection](https://mastr.github.io/batcontrol/integrations/evcc-connection/index.md).

## Uninstalling

Batcontrol saves your inverter's original battery settings and restores them on a clean shutdown. To uninstall safely:

1. If batcontrol is currently running, wait until it finishes a cycle and goes to sleep, then shut it down. This ensures the saved inverter settings are restored.
1. If batcontrol is not running and you suspect it crashed during a run, restart it, let it complete one cycle and sleep, then shut it down cleanly.
1. Verify that the battery control schedule in your inverter's local web UI looks correct after shutdown.
1. Remove the Docker container or the local installation directory.
1. Disable the Solar API in your inverter's web UI if no other software requires it. Note: the Fronius Wattpilot wallbox requires the Solar API to be enabled.

## Development Setup

For working on the batcontrol source code, use an editable install:

```
git clone https://github.com/MaStr/batcontrol.git
cd batcontrol
uv venv --python 3.14 --allow-existing
source .venv/bin/activate
uv pip install --editable '.[test]'
```

Run the test suite:

```
./run_tests.sh
# or
python -m pytest tests/
# with coverage:
python -m pytest tests/ --cov=src/batcontrol
```
