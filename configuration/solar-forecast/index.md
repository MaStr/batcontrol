The following providers are currently available:

- [Forecast Solar](https://forecast.solar/)
- local evcc instance - since 0.5.3
- [HomeAssistant Solar Forecast ML](https://zara-toorox.github.io/) - since 0.7.0
- [Solcast](https://solcast.com/) - since 0.8.1

Multiple Installations can be entered, like this:

```
solar_forecast_provider: fcsolarapi
pvinstallations:
  - name: Haus #name
    lat: 48.4334480
    lon: 8.7654968
    declination: 32 #inclination toward horizon 0..90 0=flat 90=vertical (e.g. wallmounted)
    azimuth: -90 # -90:East, 0:South, 90:West -180..180
    kWp: 15.695 # power in kWp
  - name: Garage  #... further installations
    lat: 48.4334480
    lon: 8.7654968
    declination: 32
    azimuth: 87
    kWp: 6.030
```

The **name** must be a unique value.

If a solar forecast provider is unavailable, batcontrol continues with cached values. By default, entries are usable while their age is strictly less than 12 hours. At 12 hours they expire and the existing no-data behavior applies. The HA solar provider also honors its existing `cache_ttl_hours` setting.

All solar providers save the latest raw response for each installation under the shared application's `cache/solar/` directory, with a separate namespace for each provider. Restart retains the original fetch timestamp and does not extend the TTL. See [Application data and provider caches](https://mastr.github.io/batcontrol/getting-started/installation/#application-data-and-provider-caches) for deployment paths, persistence guarantees, and `--clear-cache`.

## Forecast.Solar (Default)

[Forecast Solar](https://forecast.solar/) allows a limited amount of free requests with no subscription or account.

The minimum configuration block is:

```
  - name: Haus #name
    lat: 48.4334480
    lon: 8.7654968
    declination: 32 #inclination toward horizon 0..90 0=flat 90=vertical (e.g. wallmounted)
    azimuth: -90 # -90:East, 0:South, 90:West -180..180
    kWp: 15.695 # power in kWp
```

In addtion you can register and use an api key with adding:

```
  - name: Haus #name
    lat: 48.4334480
    lon: 8.7654968
    declination: 32 #inclination toward horizon 0..90 0=flat 90=vertical (e.g. wallmounted)
    azimuth: -90 # -90:East, 0:South, 90:West -180..180
    kWp: 15.695 # power in kWp
    api: ffff-ffff-fff-ffff
```

If you have an obstructed horizon, you can add a horizon modifier:

```
  - name: Haus #name
    lat: 48.4334480
    lon: 8.7654968
    declination: 32 #inclination toward horizon 0..90 0=flat 90=vertical (e.g. wallmounted)
    azimuth: -90 # -90:East, 0:South, 90:West -180..180
    kWp: 15.695 # power in kWp
    horizon: 30,30,30,0,0,0  # leave empty for default PVGIS horizon, only modify if solar array is shaded by trees or houses
```

## Local evcc instance

evcc is able to collect its own PV forecast, which can be obtained via REST API. batcontrol can make use of that.

```
solar_forecast_provider: evcc-solar
pvinstallations:
  - name: Haus #name
    url: http://evcc.local:7070/api/tariff/solar
```

If evcc is running under HomeAssistant, you should use either `http://homeassistant:7070/api/tariff/solar` or `http://<homeassistant-ip>:7070/api/tariff/solar`

## HomeAssistant Solar Forecast ML

The [HomeAssistant Solar Forecast ML](https://zara-toorox.github.io/) integration (available via HACS) provides machine learning-based solar forecasts directly from your HomeAssistant instance. This provider requires the HACS integration to be installed first. Use the evcc based sensor, which might be additionally enabled in the SolarML Addon. Sensor name is `sensor.solar_forecast_ml_evcc_solar_prognose` . If you have startup issues, define sensor_unit `Wh`.

**Minimum Requirements:**

- batcontrol version: 0.7.2
- HomeAssistant addon minimum version: V16.2.0

The minimum configuration is:

```
solar_forecast_provider: homeassistant-solar-forecast-ml
pvinstallations:
  - name: HA Solar ML Forecast
    url: ws://homeassistant.local:8123       # Your HomeAssistant URL
    api_token: eyJ...                        # Long-lived access token from HA Profile
    entity_id: sensor.solar_forecast_ml_evcc_solar_prognose  # Forecast sensor entity
```

If you're running batcontrol in a HomeAssistant addon, use `ws://homeassistant:8123` as the url. For standalone installations, use your HomeAssistant IP or hostname.

List only the entries of the provider you selected

This provider uses a single `pvinstallations` entry: the one with a matching `type:`, or the one carrying `entity_id`. Entries of *other* providers have to be removed, because `fcsolarapi` and `solcast` read every entry in the list and fail on an entry that does not carry their keys. With several entries and no way to tell which one is meant, batcontrol stops at startup and asks you to add `type: homeassistant-solar-forecast-ml`.

Key names

The credential is `api_token` and the endpoint is `url`, consistently across all configuration sections. The older spellings `base_url`, `apikey` and `apitoken` are still read, but log a deprecation warning at startup. The Home Assistant add-on offers only `url` and `api_token` from version 0.10.0 on - rename them there in the same step as the update.

### Optional Parameters

You can customize the behavior with additional parameters:

```
pvinstallations:
  - name: HA Solar ML Forecast
    url: ws://homeassistant:8123
    api_token: eyJ...
    entity_id: sensor.solar_forecast_ml_prognose_nachste_stunde
    sensor_unit: auto  # Options: 'auto' (default, auto-detect), 'Wh', or 'kWh'
    cache_ttl_hours: 24.0  # Cache duration in hours (default: 12.0)
```

The `sensor_unit` parameter:

- `auto` (default): Automatically detects the unit from the sensor
- `Wh`: If you know your sensor reports in Wh
- `kWh`: If you know your sensor reports in kWh

Setting the explicit unit (`Wh` or `kWh`) can speed up startup by skipping auto-detection.

The `cache_ttl_hours` parameter controls how long an already fetched forecast stays usable as a fallback when HomeAssistant cannot be reached (default: 12 hours). It does not control how often batcontrol asks for a new forecast — that is the refresh interval of the provider.

### Troubleshooting

**Problem:** batcontrol stops at startup with `Auth message incorrectly formatted: expected str at 'access_token'. Got None`

**Solution:** the access token never reached the provider. Check that

- the token is configured as `api_token` — `apikey` is the key name of the fcsolarapi and Solcast providers and was a common mix-up ([issue #451](https://github.com/MaStr/batcontrol/issues/451))
- the token sits in the same `pvinstallations` entry as `entity_id` and `url`
- the entry belongs to the selected provider (see the note above about `type:`)

Since batcontrol 0.10.0 a missing token is reported as a configuration error naming the key, before any connection to HomeAssistant is attempted.

**Problem:** `No "url" configured for installation <name>, falling back to ws://homeassistant.local:8123`

**Solution:** your URL is not in the entry batcontrol selected. Use the key `url` (or the deprecated `base_url`) in the entry that also holds `entity_id`.

## Solcast

[Solcast](https://solcast.com/) provides satellite-based solar forecasts in native 30-minute resolution. batcontrol interpolates the values to 15-minute intervals (linear power interpolation) or aggregates them to hourly values, depending on your `time_resolution_minutes` setting.

Solcast delivers three estimates per 30-minute period: a most-likely value (50 % percentile), a pessimistic one (10 %, more clouds) and an optimistic one (90 %, fewer clouds). By default batcontrol uses the most-likely value.

### Free hobbyist account (registration walkthrough)

Solcast offers a free "Home User" (hobbyist) account which is sufficient for batcontrol:

1. Register at [solcast.com](https://solcast.com/free-rooftop-solar-forecasting) and choose the free **Home User** (hobbyist) plan.
1. In the Solcast toolkit, create a **Rooftop Site**. Enter your location (latitude/longitude), panel tilt, azimuth and capacity (kWp) there — the site geometry lives in your Solcast account, **not** in the batcontrol configuration.
1. Copy the site's **resource id** (shown in the site details, format `xxxx-xxxx-xxxx-xxxx`).
1. Copy your **API key** from the Solcast account settings (configured as `api_token`).

The free account is limited to **one location with up to two rooftop sites** (e.g. an east/west split) and **10 API requests per day**.

### Configuration

The minimum configuration is:

```
solar_forecast_provider: solcast
pvinstallations:
  - name: Haus #name
    resource_id: xxxx-xxxx-xxxx-xxxx  # from your Solcast rooftop site
    api_token: your-solcast-api-key   # from your Solcast account settings
```

For two rooftop sites (e.g. east/west arrays) use one entry per site. The API key is the same for both, each site has its own resource id:

```
solar_forecast_provider: solcast
pvinstallations:
  - name: Dach Ost
    resource_id: xxxx-xxxx-xxxx-aaaa
    api_token: your-solcast-api-key
  - name: Dach West
    resource_id: xxxx-xxxx-xxxx-bbbb
    api_token: your-solcast-api-key
```

### Optional percentile parameter

You can select which estimate batcontrol uses per installation:

```
pvinstallations:
  - name: Haus
    resource_id: xxxx-xxxx-xxxx-xxxx
    api_token: your-solcast-api-key
    percentile: 10  # 10 (pessimistic), 50 (default, most likely), 90 (optimistic)
```

A pessimistic setting (`percentile: 10`) makes batcontrol plan with less expected solar production, which reduces the risk of an undercharged battery on cloudy days.

### Rate limit

batcontrol keeps well within the free tier's 10 requests/day: it enforces a minimum refresh interval of 3 hours per configured site, scaled with the number of sites (one site: every 3 hours, two sites: every 6 hours — at most 8 requests per day, leaving 2 in reserve). If the API reports that the quota is exhausted (HTTP 429), batcontrol pauses requests and keeps working on cached forecast data.

## Adjusting Production Forecasts

### production_offset_percent

If your actual solar production systematically differs from the forecast (e.g., winter snow coverage, panel degradation, or consistently higher performance), you can adjust the entire forecast using the `production_offset_percent` parameter in the `battery_control_expert` section:

```
battery_control_expert:
  production_offset_percent: 0.8  # Use 80% of the forecast (20% reduction)
```

This multiplier is applied to all forecasted values:

- `1.0` = no adjustment (default)
- `0.8` = 80% of forecast (useful for winter/snow conditions)
- `1.1` = 110% of forecast (for systems that consistently outperform)

For detailed information, see [Battery Control Expert - production_offset_percent](https://mastr.github.io/batcontrol/features/battery-control-expert/#adjust-solar-production-forecast).
