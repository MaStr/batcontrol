# Consumption Forecast

Batcontrol uses consumption forecasting to predict your household's energy usage for optimal battery management. This helps the system make smart decisions about when to charge or discharge your battery based on expected consumption patterns.

## Available Forecast Providers

Batcontrol supports two methods for consumption forecasting:

1. **CSV** - Static load profile based on typical consumption patterns
2. **HomeAssistant API** - (Since 0.5.4) Dynamic forecast based on your actual historical consumption data

---

## 1. CSV-Based Forecast

The CSV method uses a predefined load profile file with typical consumption patterns. This is the default and simplest option.

### Configuration (Since 0.5.0)

```yaml
consumption_forecast:
  type: csv
  csv:
    annual_consumption: 4500 # Total consumption in kWh per year
    load_profile: load_profile.csv # Name of the load profile file in config folder
```

### Configuration (Before 0.5.0)

```yaml
consumption_forecast:
    annual_consumption: 4500 # Total consumption in kWh per year
    load_profile: load_profile.csv # Name of the load profile file in config folder
```

### CSV File Format

The CSV file must be placed in the `config/` folder and contain the following fields:

```csv
month,weekday,hour,energy
```

**Field Definitions:**
- `month`: 1-12 (January = 1, December = 12)
- `weekday`: 0-6 (Monday = 0, Sunday = 6)
- `hour`: 0-23 (midnight = 0, 11 PM = 23)
- `energy`: Consumption in Wh (Watt-hours)

### Example CSV Entry

```csv
1,0,8,350
```
This means: In January, on Monday, at 8 AM, the consumption is 350 Wh.

### How CSV Scaling Works

When batcontrol loads the CSV profile, it:

1. Calculates the total annual consumption from the load profile
2. Compares it to your configured `annual_consumption`
3. Scales all hourly values proportionally to match your actual consumption

**Example log output:**
```
INFO [FC Cons] The annual consumption of the applied load profile is 3225.29 kWh
INFO [FC Cons] The hourly values from the load profile are scaled with a factor of 1.40 to match the annual consumption of 4500 kWh
```

### Default Load Profile

If no load profile is specified, batcontrol uses `default_load_profile.csv` as a fallback.

---

## 2. HomeAssistant API-Based Forecast

The HomeAssistant API method provides **dynamic consumption forecasting** based on your actual historical consumption data. This is the most accurate method as it learns from your real usage patterns.

### How It Works

1. **Connects to HomeAssistant** via WebSocket API
2. **Fetches historical data** from configured time periods (e.g., last 7, 14, 21 days)
3. **Calculates weighted averages** for each hour of the week
4. **Generates forecasts** for up to 48 hours ahead
5. **Caches results** to minimize API calls

### Prerequisites

- HomeAssistant instance accessible from batcontrol
- Long-term statistics enabled for your consumption sensor
- HomeAssistant Long-Lived Access Token

### Configuration

```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://homeassistant.local:8123   # Your HomeAssistant URL
    apitoken: YOUR_LONG_LIVED_ACCESS_TOKEN     # Long-Lived Access Token
    entity_id: sensor.energy_consumption        # Entity ID with consumption data
    sensor_unit: auto                           # Options: 'auto', 'Wh', or 'kWh' (since 0.5.7)
    history_days: "-7;-14;-21"               # Days to look back (negative values)
    history_weights: "1,1,1"                 # Weight for each history period (1-10)
    cache_ttl_hours: 48.0                      # Cache duration in hours
    multiplier: 1.0                             # Forecast adjustment multiplier
```

### Configuration Parameters

#### Required Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `base_url` | HomeAssistant URL (ws is correct)  | `ws://homeassistant.local:8123` |
| `apitoken` | Long-Lived Access Token from HomeAssistant | `eyJ0eXAiOiJKV1Qi...` |
| `entity_id` | Entity ID tracking consumption (must have long-term statistics) | `sensor.energy_consumption` |

#### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sensor_unit` | `auto` | **Since 0.5.7**: Sensor unit configuration. Options: `'auto'` (auto-detect), `'Wh'`, or `'kWh'`. Set to `'Wh'` or `'kWh'` to skip auto-detection (faster startup, recommended for large HA installations). |
| `history_days` | `"-7;-14;-21"` | List of day offsets to fetch historical data. Negative values = days in the past. |
| `history_weights` | `"1;1;1"` | Weight for each history period (1-10). Higher = more influence. Must match length of `history_days`. |
| `cache_ttl_hours` | `48.0` | How long to cache computed statistics (in hours) |
| `multiplier` | `1.0` | Global multiplier for all forecast values. Use `1.1` for +10%, `0.9` for -10% |

### Getting a HomeAssistant Access Token

1. Open HomeAssistant web interface
2. Click on your profile (bottom left)
3. Scroll down to **"Long-Lived Access Tokens"**
4. Click **"Create Token"**
5. Give it a name (e.g., "Batcontrol")
6. Copy the token (you won't be able to see it again!)

### Sensor Unit Configuration (Since 0.5.7)

The `sensor_unit` parameter controls how batcontrol detects the unit of measurement for your consumption sensor.

**Options:**
- `auto` (default) - Automatically detect unit by querying HomeAssistant
- `Wh` - Sensor reports in Watt-hours (no conversion needed)
- `kWh` - Sensor reports in Kilowatt-hours (values multiplied by 1000)

**When to use explicit configuration (`Wh` or `kWh`):**
- **Large HomeAssistant installations** with many entities (faster startup, avoids "message too big" errors)
- **Performance optimization** - skips the auto-detection query on every startup
- **Consistent behavior** - eliminates the need to fetch all entity states

**How to check your sensor's unit:**
1. Open HomeAssistant → Developer Tools → States
2. Find your entity (e.g., `sensor.energy_consumption`)
3. Check the `unit_of_measurement` attribute
4. Set `sensor_unit` accordingly in your configuration

**Example:**
```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://homeassistant.local:8123
    apitoken: YOUR_TOKEN
    entity_id: sensor.energy_consumption
    sensor_unit: kWh  # Explicit configuration - faster startup!
```

**Note:** If you're unsure, leave it as `auto` (default). Batcontrol will automatically detect the correct unit.

### Entity Requirements

The entity you specify must:
- Be a **sensor** entity
- Track **cumulative energy consumption** (in Wh)
- Have **long-term statistics enabled**
- Provide **hourly statistics** via the HomeAssistant recorder

**Good entity examples:**
- `sensor.energy_consumption`
- `sensor.house_energy_total`
- `sensor.grid_import_total`

**Not suitable:**
- Instantaneous power sensors (W)
- Entities without statistics
- Non-energy entities

### Example Sensor Configuration Using SunSpec Integration
- install SunSpec via HACS and configure with the IP from your inverter (see the SunSpec documentation if further informations are needed)
- Create a template helper as sensor: Settings -> Devices & Services -> Helper -> Create helper -> Template -> Sensor
- Name: e.g. `sensor.energy_consumption` or `sensor.house_energy_total`
- State:

```yaml
{{
states('sensor.smartmeter_ac_meter_total_watt_hours_imported') | float -
states('sensor.smartmeter_ac_meter_total_watt_hours_exported') | float + 
(states('sensor.inverter_mppt_module_0_lifetime_energy') | float + 
states('sensor.inverter_mppt_module_1_lifetime_energy') | float + 
states('sensor.inverter_mppt_module_3_lifetime_energy') | float - 
states('sensor.inverter_mppt_module_2_lifetime_energy') | float)
}}
```
- Unit of measurement: `kWh`
- Device class: `Energy`
- State class: `Total`


### How History Weights Work

The `history_weights` parameter allows you to give more importance to recent data vs. older data.

**Example 1: Equal weighting**
```yaml
history_days: "-7;-14;-21"
history_weights: "1;1;1"
```
All three weeks have equal influence (33.3% each).

**Example 2: Recent data preferred**
```yaml
history_days: "-7;-14;-21"
history_weights: "3;2;1"
```
- Last week: 50% influence (3/6)
- Two weeks ago: 33% influence (2/6)
- Three weeks ago: 17% influence (1/6)

**Example 3: Short-term forecast**
```yaml
history_days: "-1;-2;-3"
history_weights: "3;2;1"
```
Uses only the last 3 days for very dynamic forecasting.

### Multiplier for Forecast Adjustment

The `multiplier` parameter allows you to globally adjust all forecast values:

- `1.0` = No adjustment (default)
- `1.1` = Increase forecast by 10%
- `0.9` = Decrease forecast by 10%
- `1.2` = Increase forecast by 20%

**Use cases:**
- You know consumption will increase (e.g., guests coming, new appliances)
- You want to be more conservative with battery discharge
- Seasonal adjustments without changing historical data

### Caching Behavior

To minimize load on HomeAssistant:
- Computed statistics are **cached** for `cache_ttl_hours`
- Cache stores consumption values per weekday/hour combination
- Cache is automatically refreshed when data is missing
- Cache survives batcontrol restarts (in-memory cache)

**Cache key format:** `"weekday_hour"` (e.g., `"0_14"` = Monday 14:00)

### WebSocket Communication

The HomeAssistant forecaster uses the modern **WebSocket API** for efficient communication:

1. Establishes WebSocket connection
2. Authenticates with access token
3. Fetches hourly statistics using `recorder/statistics_during_period`
4. Processes and caches results
5. Reuses connection for multiple requests when possible

This is more efficient than the REST API for frequent data fetches.

---

## Testing Your Configuration

### Test Script

Batcontrol includes a test script to verify your HomeAssistant configuration:

```bash
cd batcontrol/scripts
python test_homeassistant_forecast.py
```

Edit the configuration section in the script:

```python
HOMEASSISTANT_URL = "ws://homeassistant.local:8123"
HOMEASSISTANT_TOKEN = "YOUR_LONG_LIVED_ACCESS_TOKEN"
ENTITY_ID = "sensor.energy_consumption"
HISTORY_DAYS = [-7, -14, -21]
HISTORY_WEIGHTS = [3, 2, 1]
```

The script will:
- Connect to HomeAssistant
- Fetch historical data
- Generate a 24-hour forecast
- Display results in a formatted table with statistics

---

## Troubleshooting

### CSV Method

**Problem:** "The annual consumption of the applied load profile is X kWh"

**Solution:** This is just informational. The profile will be automatically scaled to match your `annual_consumption` setting.

**Problem:** "No load profile specified, using default"

**Solution:** Specify a valid `load_profile` filename in your configuration.

### HomeAssistant API Method

**Problem:** "Authentication failed"

**Solution:**
- Verify your access token is correct
- Check if the token has been revoked in HomeAssistant
- Create a new Long-Lived Access Token

**Problem:** "ConnectionClosedError: sent 1009 (message too big)" or "websockets.exceptions.ConnectionClosedError: frame exceeds limit"

**Solution (Since 0.5.7):**
This error occurs when your HomeAssistant instance has many entities (sensors, lights, automations, etc.) and the response exceeds the WebSocket size limit during auto-detection.

**Quick Fix:** Set the `sensor_unit` parameter explicitly to skip auto-detection:
```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://homeassistant.local:8123
    apitoken: YOUR_TOKEN
    entity_id: sensor.energy_consumption
    sensor_unit: kWh  # or 'Wh' depending on your sensor
```

**How to determine your sensor unit:**
1. Open HomeAssistant → Developer Tools → States
2. Find your entity (e.g., `sensor.energy_consumption`)
3. Check the `unit_of_measurement` attribute
4. Use `kWh` if it shows "kWh", or `Wh` if it shows "Wh"

**Technical details:** Batcontrol 0.5.7+ uses a 4MB WebSocket frame limit (up from 1MB) and allows you to skip the auto-detection query entirely by configuring the sensor unit explicitly.

**Problem:** "No statistics data returned for entity"

**Solution:**
- Verify the entity exists in HomeAssistant
- Check if long-term statistics are enabled for this entity
- Wait for HomeAssistant to collect at least one hour of statistics
- Check HomeAssistant logs for recorder issues

**Problem:** "Connection refused"

**Solution:**
- Verify `base_url` is correct and accessible from batcontrol
- Check if HomeAssistant is running
- Verify network connectivity
- Check firewall rules

**Problem:** "Length of history_days must match history_weights"

**Solution:** Ensure both lists have the same number of elements:
```yaml
history_days: "-7;-14;-21"      # 3 elements
history_weights: "3;2;1"         # 3 elements
```

**Problem:** "History weights must be between 1 and 10"

**Solution:** Use only values from 1 to 10 in `history_weights`.

**Problem:** Empty or incomplete forecast

**Solution:**
- Check if HomeAssistant has enough historical data (at least 7 days recommended)
- Verify the entity is recording data continuously
- Check cache TTL - try reducing it temporarily
- Enable DEBUG logging to see detailed fetch information

**Problem:** Forecast values are too small or too large

**Solution (Since 0.5.7):** 
Batcontrol automatically detects whether your sensor reports in Wh or kWh and applies the correct conversion. If values are incorrect:

1. **Check auto-detection:** Let batcontrol auto-detect (default `sensor_unit: auto`)
2. **Verify sensor unit:** Check your sensor's `unit_of_measurement` in HomeAssistant
3. **Set explicitly:** If auto-detection fails, set `sensor_unit` manually:
   ```yaml
   sensor_unit: kWh  # if sensor reports in kWh
   # or
   sensor_unit: Wh   # if sensor reports in Wh
   ```

**Legacy workaround (before 0.5.7):** If auto-detection is not available, use the `multiplier` parameter:
```yaml
multiplier: 1000  # Convert kWh to Wh (if sensor reports in kWh)
```

**Note:** Batcontrol expects all consumption values in Wh (Watt-hours) internally.

---

## Comparison: CSV vs. HomeAssistant API

| Feature | CSV | HomeAssistant API |
|---------|-----|-------------------|
| **Accuracy** | Generic patterns | Based on your actual usage |
| **Setup Complexity** | Simple | Moderate (requires HA setup) |
| **Maintenance** | Manual updates needed | Automatic learning |
| **Dependencies** | None | HomeAssistant + Long-term stats |
| **Flexibility** | Low (static profile) | High (adapts to changes) |
| **Performance** | Fast (local file) | Cached (WebSocket API) |
| **Best For** | Testing, consistent usage | Real-world scenarios |

---

## Recommendations

- **Start with CSV** for initial testing and setup
- **Switch to HomeAssistant API** once you have historical data for accurate forecasting
- Use **recent history weighting** (e.g., `[3, 2, 1]`) for more responsive forecasts
- Set `cache_ttl_hours` to `24-48` hours for good balance between accuracy and API load
- Use **multiplier** for temporary adjustments rather than changing configuration frequently
- Monitor logs to ensure forecasts are being generated correctly

---

## Advanced Tips

### Seasonal Adjustments

For seasonal changes, consider:
- Using shorter `history_days` periods (e.g., `-7, -14` instead of `-7, -14, -21`)
- Adjusting the `multiplier` seasonally
- Creating different CSV profiles for different seasons

### Multiple Consumption Points

If you have multiple consumption sensors, you can:
- Create a template sensor in HomeAssistant that combines them
- Use the combined sensor's entity_id in batcontrol configuration

### Debugging

Enable DEBUG logging to see detailed information:
```python
logging.basicConfig(level=logging.DEBUG)
```

Look for:
- WebSocket connection messages
- Statistics fetch results
- Cache hit/miss events
- Weighted average calculations

---

## Example Configurations

### Example 1: Simple Setup (CSV)

```yaml
consumption_forecast:
  type: csv
  csv:
    annual_consumption: 4500
    load_profile: load_profile.csv
```

### Example 2: HomeAssistant with Equal Weights

```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://192.168.1.100:8123
    apitoken: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
    entity_id: sensor.house_energy_total
    sensor_unit: auto  # Auto-detect (default)
    history_days: "-7;-14;-21"
    history_weights: "1;1;1"
    cache_ttl_hours: 48.0
    multiplier: 1.0
```

### Example 3: HomeAssistant with Explicit Unit (Recommended for Large Installations)

```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://homeassistant.local:8123
    apitoken: your_token_here
    entity_id: sensor.energy_consumption
    sensor_unit: kWh  # Explicit unit - faster startup, no auto-detection needed
    history_days: "-7;-14;-21"
    history_weights: "3;2;1"  # Recent week has most influence
    cache_ttl_hours: 24.0
    multiplier: 1.1  # Increase forecast by 10%
```

### Example 4: Short-term Dynamic Forecast

```yaml
consumption_forecast:
  type: homeassistant-api
  homeassistant_api:
    base_url: ws://homeassistant.local:8123
    apitoken: your_token_here
    entity_id: sensor.energy_consumption
    sensor_unit: Wh  # Explicit unit for optimal performance
    history_days: "-1;-2;-3"  # Only last 3 days
    history_weights: "5;3;2"   # Yesterday has most weight
    cache_ttl_hours: 12.0        # Shorter cache
    multiplier: 1.0
```

---

## Related Documentation

- [Batcontrol Configuration](batcontrol-configuration.md)
- [How Batcontrol Works](../getting-started/how-batcontrol-works.md)
- [MQTT API](../integrations/mqtt-api.md)
- [Solar Forecast](solar-forecast.md)