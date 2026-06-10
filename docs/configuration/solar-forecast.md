The following providers are currently available:

* [Forecast Solar](https://forecast.solar/)
* [Solarprognose.de](https://www.solarprognose.de) - since 0.5.0
* local evcc instance - since 0.5.3
* [HomeAssistant Solar Forecast ML](https://zara-toorox.github.io/) - since 0.7.0

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

The minimum configuration block is are:

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

For further details see: [API description|(https://www.solarprognose.de/web/de/solarprediction/page/api)

## Local evcc instance
evcc is able to collect its own PV forecast, which can be obtained via REST API. batcontrol can make use of that.


```
solar_forecast_provider: evcc-solar
pvinstallations:
  - name: Haus #name
    url: http://evcc.local:7070/api/tariff/solar

```
If evcc is running under HomeAssistant, you should you either `http://homeassistant:7070/api/tariff/grid` or `http://<homeassistant-ip>:7070/api/tariff/grid`

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
