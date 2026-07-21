The following providers are currently available:

* [Forecast Solar](https://forecast.solar/)
* [Solarprognose.de](https://www.solarprognose.de) - since 0.5.0
* local evcc instance - since 0.5.3
* [HomeAssistant Solar Forecast ML](https://zara-toorox.github.io/) - since 0.7.0
* [Solcast](https://solcast.com/) - since 0.8.1

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

If a solar forecast provider is not available, batcontrol is running on cached values. It stops working if less then 12 hours of forecast are available. That should be enough to overcome outages.


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

## Solarprognose.de
Solarprognose offers a free tier for installations below 10KW. Currently, larger tiers are available for free, but this may change. The provider is asking for donations. You need to register on their website and enter you installation. With using the provided API key, you can run batcontrol with following configuration:

```
solar_forecast_provider: solarprognose
pvinstallations:
  - name: Haus #name
    apikey: 44k4j5j5j5j5j6j6j6j6j6j6j6j6j6j6j6j6
```
This configuration delivers the forecast for the first defined location. The API provider asks to add `project: <your@email.com>` as an additional parameter, that he can contact a person in case of issues.

In addition you can change the algorithm using:

```
pvinstallations:
  - name: Haus #name
    apikey: 44k4j5j5j5j5j6j6j6j6j6j6j6j6j6j6j6j6
    algorithm: own-v1 # (Default is 'mosmix')
```

If you run multiple installations with you account or want to split up forecasts for reasons, you can use the ITEM and ID syntax.

* item: <location|inverter|module_field>
* token: <item token>

For further details see: [API description](https://www.solarprognose.de/web/de/solarprediction/page/api)

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
The [HomeAssistant Solar Forecast ML](https://zara-toorox.github.io/) integration (available via HACS) provides machine learning-based solar forecasts directly from your HomeAssistant instance. This provider requires the HACS integration to be installed first.
Use the evcc based sensor, which might be additionally enabled in the SolarML Addon. Sensor name is `sensor.solar_forecast_ml_evcc_solar_prognose` . If you have startup issues, define sensor_unit `Wh`.

**Minimum Requirements:**
- batcontrol version: 0.7.2
- HomeAssistant addon minimum version: V16.2.0

The minimum configuration is:

```yaml
solar_forecast_provider: homeassistant-solar-forecast-ml
pvinstallations:
  - name: HA Solar ML Forecast
    base_url: ws://homeassistant.local:8123  # Your HomeAssistant URL
    api_token: eyJ...                        # Long-lived access token from HA Profile
    entity_id: sensor.solar_forecast_ml_evcc_solar_prognose  # Forecast sensor entity
```

If you're running batcontrol in a HomeAssistant addon, use `ws://homeassistant:8123` as the base_url. For standalone installations, use your HomeAssistant IP or hostname.

### Optional Parameters

You can customize the behavior with additional parameters:

```yaml
pvinstallations:
  - name: HA Solar ML Forecast
    base_url: ws://homeassistant:8123
    api_token: eyJ...
    entity_id: sensor.solar_forecast_ml_prognose_nachste_stunde
    sensor_unit: auto  # Options: 'auto' (default, auto-detect), 'Wh', or 'kWh'
    cache_ttl_hours: 24.0  # Cache duration in hours (default: 24.0)
```

The `sensor_unit` parameter:
- `auto` (default): Automatically detects the unit from the sensor
- `Wh`: If you know your sensor reports in Wh
- `kWh`: If you know your sensor reports in kWh

Setting the explicit unit (`Wh` or `kWh`) can speed up startup by skipping auto-detection.

## Solcast

[Solcast](https://solcast.com/) provides satellite-based solar forecasts in native 30-minute resolution. batcontrol interpolates the values to 15-minute intervals (linear power interpolation) or aggregates them to hourly values, depending on your `time_resolution_minutes` setting.

Solcast delivers three estimates per 30-minute period: a most-likely value (50 % percentile), a pessimistic one (10 %, more clouds) and an optimistic one (90 %, fewer clouds). By default batcontrol uses the most-likely value.

### Free hobbyist account (registration walkthrough)

Solcast offers a free "Home User" (hobbyist) account which is sufficient for batcontrol:

1. Register at [solcast.com](https://solcast.com/free-rooftop-solar-forecasting) and choose the free **Home User** (hobbyist) plan.
2. In the Solcast toolkit, create a **Rooftop Site**. Enter your location (latitude/longitude), panel tilt, azimuth and capacity (kWp) there — the site geometry lives in your Solcast account, **not** in the batcontrol configuration.
3. Copy the site's **resource id** (shown in the site details, format `xxxx-xxxx-xxxx-xxxx`).
4. Copy your **API key** from the Solcast account settings.

The free account is limited to **one location with up to two rooftop sites** (e.g. an east/west split) and **10 API requests per day**.

### Configuration

The minimum configuration is:

```yaml
solar_forecast_provider: solcast
pvinstallations:
  - name: Haus #name
    resource_id: xxxx-xxxx-xxxx-xxxx  # from your Solcast rooftop site
    apikey: your-solcast-api-key      # from your Solcast account settings
```

For two rooftop sites (e.g. east/west arrays) use one entry per site. The API key is the same for both, each site has its own resource id:

```yaml
solar_forecast_provider: solcast
pvinstallations:
  - name: Dach Ost
    resource_id: xxxx-xxxx-xxxx-aaaa
    apikey: your-solcast-api-key
  - name: Dach West
    resource_id: xxxx-xxxx-xxxx-bbbb
    apikey: your-solcast-api-key
```

### Optional percentile parameter

You can select which estimate batcontrol uses per installation:

```yaml
pvinstallations:
  - name: Haus
    resource_id: xxxx-xxxx-xxxx-xxxx
    apikey: your-solcast-api-key
    percentile: 10  # 10 (pessimistic), 50 (default, most likely), 90 (optimistic)
```

A pessimistic setting (`percentile: 10`) makes batcontrol plan with less expected solar production, which reduces the risk of an undercharged battery on cloudy days.

### Rate limit

batcontrol keeps well within the free tier's 10 requests/day: it enforces a minimum refresh interval of 3 hours per configured site, scaled with the number of sites (one site: every 3 hours, two sites: every 6 hours — at most 8 requests per day, leaving 2 in reserve). If the API reports that the quota is exhausted (HTTP 429), batcontrol pauses requests and keeps working on cached forecast data.

## Adjusting Production Forecasts

### production_offset_percent

If your actual solar production systematically differs from the forecast (e.g., winter snow coverage, panel degradation, or consistently higher performance), you can adjust the entire forecast using the `production_offset_percent` parameter in the `battery_control_expert` section:

```yaml
battery_control_expert:
  production_offset_percent: 0.8  # Use 80% of the forecast (20% reduction)
```

This multiplier is applied to all forecasted values:
- `1.0` = no adjustment (default)
- `0.8` = 80% of forecast (useful for winter/snow conditions)
- `1.1` = 110% of forecast (for systems that consistently outperform)

For detailed information, see [Battery Control Expert - production_offset_percent](../features/battery-control-expert.md#adjust-solar-production-forecast).
