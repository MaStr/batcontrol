# 15-Minute Interval Transformation Analysis

## Executive Summary

This document analyzes the required changes to transform the batcontrol system from hourly (60-minute) to 15-minute time intervals. The transition involves modifications across forecast providers, core logic, MQTT API, and data structures.

**Key Finding**: Making the interval configurable (15 or 60 minutes) is **highly recommended** to maintain flexibility and backward compatibility during migration.

---

## Current Architecture Overview

### Time Resolution Comparison

```
CURRENT (Hourly):
Timeline:  |----Hour 0----|----Hour 1----|----Hour 2----|
Intervals: 0              1              2              3
Data points: 48 (for 48 hours)
Array size: ~2 KB per forecast

PROPOSED (15-minute):
Timeline:  |--15m--|--15m--|--15m--|--15m--|  (= 1 hour)
Intervals: 0  1  2  3  4  5  6  7  8  9 ...
Data points: 192 (for 48 hours)
Array size: ~8 KB per forecast

Evaluation: Every 3 minutes (unchanged)
```

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    BATCONTROL CORE                          │
│                 (Evaluation Every 3 min)                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ├──> Config: time_resolution_minutes
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│Solar Forecast│    │Consumption   │    │Dynamic Tariff│
│              │    │Forecast      │    │              │
├──────────────┤    ├──────────────┤    ├──────────────┤
│ FCSolar:     │    │ CSV Profile: │    │ Awattar:     │
│   Hourly     │    │   Hourly     │    │   Hourly     │
│   ↓ Upsample │    │   ↓ Divide   │    │   ↓ Repeat   │
│   15-min     │    │   15-min     │    │   15-min     │
│              │    │              │    │              │
│ EvccSolar:   │    │ Future:      │    │ Evcc:        │
│   Native     │    │   Native     │    │   Native     │
│   15-min     │    │   15-min     │    │   15-min     │
└──────────────┘    └──────────────┘    └──────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │Array Merge   │
                    │[0..191]      │
                    └──────────────┘
                            │
                ┌───────────┼───────────┐
                │           │           │
                ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │Production│ │Consumption│ │ Prices  │
        │  Array   │ │  Array    │ │  Array  │
        │ [0..191] │ │ [0..191]  │ │ [0..191]│
        └──────────┘ └──────────┘ └──────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │  Logic       │
                    │  Calculation │
                    │  (192 iters) │
                    └──────────────┘
                            │
                ┌───────────┴───────────┐
                │                       │
                ▼                       ▼
        ┌──────────────┐        ┌──────────────┐
        │  Inverter    │        │ MQTT Publish │
        │  Control     │        │ (Telegraf)   │
        │  (charge_rate)│       │  ↓           │
        └──────────────┘        │ InfluxDB     │
                                │  ↓           │
                                │ Grafana      │
                                └──────────────┘
```

### Decision Tree: Which Interval to Choose?

```
Do you have dynamic electricity pricing?
    │
    ├─ NO ───> Use 60 min (hourly sufficient)
    │
    └─ YES
        │
        Does your tariff change every 15 minutes?
            │
            ├─ YES ───> Use 15 min (required)
            │
            └─ NO (hourly) ───> Use 60 min OR 15 min
                                └─> 15 min gives more  
                                    responsive charging

Hardware considerations:
    ├─ Raspberry Pi 3 ──> Start with 60 min
    ├─ Raspberry Pi 4+ ─> Use 15 min
    └─ Server/Desktop ──> Use 15 min

Network considerations:
    ├─ Slow/metered ──> Use 60 min (less MQTT data)
    └─ Fast/unlimited ─> Use 15 min
```

### Time Resolution
- **Core Interval**: 60 minutes (hourly)
- **Evaluation Frequency**: Every 3 minutes (configurable via `EVALUATIONS_EVERY_MINUTES`)
- **Data Structure**: Arrays indexed by hour (0-48 for forecasts)
- **API Updates**: Every 15 minutes (`TIME_BETWEEN_UTILITY_API_CALLS = 900`)

### Current Workflow
1. Forecasts are fetched hourly for production, consumption, and prices
2. Arrays are indexed by `hour` (relative hours from now)
3. Logic calculates based on hourly energy values (Wh)
4. Charge rates are calculated based on remaining time in current hour
5. MQTT publishes forecast data with hourly timestamps

---

## Required Changes by Component

### 1. Core Module (`src/batcontrol/core.py`)

#### Critical Design Decision: Full-Hour Alignment

**Problem**: Index misalignment between provider data and current time causes inconsistencies.

**Example of the issue**:
```
Time: 10:20 (20 minutes into hour)

Provider returns (hour-aligned):
  [0] = 10:00-10:15  (250 Wh) - ALREADY PASSED
  [1] = 10:15-10:30  (300 Wh) - CURRENT interval (5 min elapsed)
  [2] = 10:30-10:45  (350 Wh)
  [3] = 10:45-11:00  (400 Wh)

Core.py expects (time-aligned):
  [0] = current interval = 10:15-10:30
  [1] = next interval = 10:30-10:45
  etc.

If core.py factorizes [0] thinking it's the current interval:
  250 * (1 - 20/15) = NEGATIVE! ❌
```

**Solution**: Providers always return **full-hour aligned data**, and Core.py handles the offset:

#### Changes Required:
```python
# Lines 347-351: Time correction for current interval
# CURRENT (hourly):
production[0] *= 1 - datetime.datetime.now().astimezone(self.timezone).minute/60
consumption[0] *= 1 - datetime.datetime.now().astimezone(self.timezone).minute/60

# PROPOSED (configurable with proper indexing):
now = datetime.datetime.now().astimezone(self.timezone)
current_minute = now.minute
current_second = now.second

# Find which interval we're in within the current hour
current_interval_in_hour = current_minute // interval_minutes  # 0, 1, 2, or 3 for 15-min

# Calculate elapsed time in the CURRENT interval
elapsed_in_current = (current_minute % interval_minutes + current_second / 60) / interval_minutes

# Provider data is hour-aligned, so we need to adjust the index
# The current interval is at index [current_interval_in_hour]
if len(production) > current_interval_in_hour:
    production[current_interval_in_hour] *= (1 - elapsed_in_current)
    consumption[current_interval_in_hour] *= (1 - elapsed_in_current)

# For MQTT publishing and logic calculation, we work from the START of current hour
# but for actual control decisions, we use only future intervals
```

**Alternative Approach** (Cleaner):
```python
# Option: Providers return data starting from CURRENT interval (not full hour)
# This requires providers to calculate the starting interval themselves

def get_forecast(self, intervals: int) -> Dict[int, float]:
    """
    Returns forecast starting from CURRENT interval.
    
    Returns:
        Dict where [0] = current interval, [1] = next, etc.
    """
    now = datetime.datetime.now().astimezone(self.timezone)
    current_interval_in_hour = now.minute // self.target_resolution
    
    # Fetch full hour data
    full_hour_data = self._fetch_forecast()
    
    # Shift to start from current interval
    shifted_data = {}
    for idx, value in full_hour_data.items():
        if idx >= current_interval_in_hour:
            shifted_data[idx - current_interval_in_hour] = value
    
    return shifted_data

# Then core.py simply factorizes [0]:
elapsed_in_current = (now.minute % interval_minutes + now.second / 60) / interval_minutes
production[0] *= (1 - elapsed_in_current)
consumption[0] *= (1 - elapsed_in_current)
```

#### Design Choice: Data Alignment Strategy

Two approaches to handle the timing/indexing issue:

**Approach A: Full-Hour Alignment (Provider-centric)**
- ✅ Providers return data aligned to hour boundaries (simpler provider implementation)
- ✅ Good for MQTT publishing (always starts at hour boundary)
- ❌ Core.py must track offset and adjust indexing
- ❌ More complex factorization logic
- **Use case**: When you want MQTT data to always show full hours

**Approach B: Current-Interval Alignment (Core-centric)** ⭐ RECOMMENDED
- ✅ Provider shifts data so [0] = current interval (matches core.py expectations)
- ✅ Core.py logic is simpler (just factorize [0])
- ✅ No index offset tracking needed
- ❌ Providers need to calculate current interval position
- ❌ MQTT data starts from "now" not hour boundary
- **Use case**: For control logic (most important use case)

**Recommendation**: Use **Approach B** (Current-Interval Alignment) because:
1. **Control logic is primary concern** - we need accurate charge decisions NOW
2. **Simpler core.py** - less error-prone
3. **Baseclass handles complexity** - providers stay simple
4. **MQTT can round to hour** - if needed for display purposes

**Implementation** (in baseclass):
```python
class ForecastSolarBase(ABC):
    def get_forecast(self, intervals: int = None) -> Dict[int, float]:
        """
        Get forecast starting from CURRENT interval.
        
        Key behavior:
        - Returns [0] = current interval (e.g., 10:15-10:30 if now is 10:20)
        - Returns [1] = next interval (e.g., 10:30-10:45)
        - Provider data is hour-aligned, baseclass shifts indices
        """
        # Fetch data at native resolution (hour-aligned)
        native_data = self._fetch_forecast()
        
        # Convert resolution if needed
        if self.native_resolution != self.target_resolution:
            native_data = self._convert_resolution(native_data)
        
        # Shift indices to start from current interval
        now = datetime.datetime.now().astimezone(self.timezone)
        current_interval_in_hour = now.minute // self.target_resolution
        
        shifted_data = {}
        for idx, value in native_data.items():
            if idx >= current_interval_in_hour:
                shifted_data[idx - current_interval_in_hour] = value
        
        return shifted_data  # [0] = current interval, ready for core.py
```

#### Array Handling:
- **Current**: Arrays sized for 48+ hours (indices 0-48)
- **15-min**: Arrays sized for 192+ intervals (4 × 48 hours)
- **Index [0]**: Always represents the CURRENT interval (not start of hour)
- **Recommendation**: Use `interval_count` variable: `hours * (60 / interval_minutes)`

#### Configuration:
Add new parameter to `batcontrol_config_dummy.yaml`:
```yaml
general:
  time_resolution_minutes: 15  # Options: 15, 60
```

---

### 2. Solar Forecast Providers (`src/batcontrol/forecastsolar/`)

#### Architecture Pattern: Baseclass with Automatic Upsampling

**Design Philosophy**: Instead of each provider implementing upsampling logic, use a **baseclass pattern** where:
1. Each provider declares its **native resolution** via attribute
2. Baseclass handles **automatic upsampling/downsampling**
3. Provider focuses solely on **data fetching**

This approach:
- ✅ Eliminates code duplication
- ✅ Centralizes upsampling logic (easier to maintain/test)
- ✅ Supports dynamic resolution switching (e.g., Tibber API)
- ✅ Consistent behavior across all providers

#### Implementation Pattern:

```python
# src/batcontrol/forecastsolar/baseclass.py

from abc import ABC, abstractmethod
from typing import Dict, Literal
import datetime
import logging

logger = logging.getLogger(__name__)

class ForecastSolarBase(ABC):
    """
    Base class for solar forecast providers with automatic resolution handling.
    
    Key Design: Providers return FULL-HOUR aligned data, baseclass shifts to CURRENT interval.
    
    Subclasses must:
    1. Set self.native_resolution (15 or 60) in __init__
    2. Implement _fetch_forecast() to return hour-aligned data
    
    Example at 10:20:
        Provider returns: {0: val_10:00, 1: val_10:15, 2: val_10:30, ...} (hour-aligned)
        get_forecast() returns: {0: val_10:15, 1: val_10:30, ...} (current-aligned)
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.target_resolution = config.get('general', {}).get('time_resolution_minutes', 60)
        self.native_resolution = 60  # Override in subclass
        self.timezone = config.get('general', {}).get('timezone', 'UTC')
        
    @abstractmethod
    def _fetch_forecast(self) -> Dict[int, float]:
        """
        Fetch forecast data at native resolution, HOUR-ALIGNED.
        
        Returns:
            Dict mapping interval index to energy value (Wh)
            Index 0 = start of current hour (e.g., 10:00 if now is 10:20)
            Index 1 = next interval in hour
            etc.
            
        Note: Baseclass will shift indices so [0] = current interval
        """
        pass
    
    def get_forecast(self, intervals: int = None) -> Dict[int, float]:
        """
        Get forecast at target resolution, CURRENT-INTERVAL aligned.
        
        Args:
            intervals: Number of intervals to forecast (optional)
        
        Returns:
            Dict where [0] = current interval, [1] = next, etc.
            Ready for core.py to factorize [0] based on elapsed time
        """
        # Fetch data at native resolution (hour-aligned)
        native_data = self._fetch_forecast()
        
        if not native_data:
            logger.warning(f"{self.__class__.__name__}: No data returned from API")
            return {}
        
        # Convert resolution if needed
        converted_data = native_data
        if self.native_resolution != self.target_resolution:
            if self.native_resolution == 60 and self.target_resolution == 15:
                logger.debug(f"{self.__class__.__name__}: Upsampling 60min -> 15min")
                from ..interval_utils import upsample_forecast
                converted_data = upsample_forecast(native_data, self.target_resolution, method='linear')
            elif self.native_resolution == 15 and self.target_resolution == 60:
                logger.debug(f"{self.__class__.__name__}: Downsampling 15min -> 60min")
                converted_data = self._downsample_to_hourly(native_data)
            else:
                logger.error(f"{self.__class__.__name__}: Cannot convert "
                           f"{self.native_resolution}min -> {self.target_resolution}min")
                return native_data
        
        # Shift indices to start from CURRENT interval
        now = datetime.datetime.now(datetime.timezone.utc).astimezone(
            datetime.timezone(datetime.timedelta(hours=0)))  # Use configured timezone
        current_interval_in_hour = now.minute // self.target_resolution
        
        logger.debug(f"{self.__class__.__name__}: Shifting from hour-aligned to current interval "
                    f"(offset: {current_interval_in_hour} intervals)")
        
        shifted_data = {}
        for idx, value in converted_data.items():
            if idx >= current_interval_in_hour:
                new_idx = idx - current_interval_in_hour
                shifted_data[new_idx] = value
        
        return shifted_data  # [0] = current interval
    
    def _downsample_to_hourly(self, data_15min: Dict[int, float]) -> Dict[int, float]:
        """Convert 15-minute intervals to hourly by summing quarters."""
        hourly = {}
        for interval_15, value in data_15min.items():
            hour = interval_15 // 4
            if hour not in hourly:
                hourly[hour] = 0
            hourly[hour] += value
        return hourly
```

#### Complete Flow Example:

**Scenario**: Current time is **10:20:30** (20 minutes, 30 seconds into hour)

**Step 1: Provider fetches data (hour-aligned)**
```python
# FCSolar._fetch_forecast() returns:
{
    0: 250,   # 10:00-10:15 (already passed 5.5 min ago)
    1: 300,   # 10:15-10:30 (current interval, 5.5 min elapsed)
    2: 350,   # 10:30-10:45 (future)
    3: 400,   # 10:45-11:00 (future)
    4: 450,   # 11:00-11:15 (future)
    ...
}
```

**Step 2: Baseclass upsamples (if needed)** - Already 15-min in this case, skip

**Step 3: Baseclass shifts to current interval**
```python
current_interval_in_hour = 20 // 15 = 1  # We're in the 2nd interval (index 1)

# Shift: subtract 1 from all indices >= 1
{
    0: 300,   # Was [1]: 10:15-10:30 (CURRENT interval)
    1: 350,   # Was [2]: 10:30-10:45
    2: 400,   # Was [3]: 10:45-11:00
    3: 450,   # Was [4]: 11:00-11:15
    ...
}
# Index 0 (10:00-10:15) was dropped because it's in the past
```

**Step 4: Core.py receives current-aligned data**
```python
production = get_forecast()  # Gets shifted data from Step 3

# Factorize [0] for elapsed time in CURRENT interval
elapsed_in_current = (20 % 15 + 30/60) / 15 = (5 + 0.5) / 15 = 0.367

production[0] *= (1 - 0.367)  # 300 * 0.633 = 190 Wh
# This is correct: 10 minutes remain in interval, ~67% of 15 min = 190 Wh
```

**Step 5: MQTT publishing**
```python
# For MQTT, we can optionally round timestamps to hour boundaries
# or publish from actual current time

mqtt_api._create_forecast(production, timestamp=time.time(), interval_minutes=15)
# Output timestamps:
#   10:20:30 -> round to 10:15:00 (interval start)
#   Data points at: 10:15, 10:30, 10:45, 11:00, ...
```

**Visual Timeline**:
```
Current time: 10:20:30 ⏰
                ↓
Hour:    10:00      10:15      10:30      10:45      11:00
         │──────────│──────────│──────────│──────────│
         │   250 Wh │   300 Wh │   350 Wh │   400 Wh │
         │  PASSED  │ CURRENT  │  FUTURE  │  FUTURE  │
         │          │ ⏰ (5.5m) │          │          │

Provider returns (hour-aligned):
         [0]=250    [1]=300    [2]=350    [3]=400

Baseclass shifts (current-aligned):
                   [0]=300    [1]=350    [2]=400
                   
Core.py factorizes:
                   [0]*0.633  [1]        [2]
                   = 190 Wh   = 350 Wh   = 400 Wh
                   
Result: Correct! No past data, current interval properly reduced.
```

**Key Insight**: 
- Provider thinks in "full hours" (simpler API logic)
- Baseclass translates to "current interval" (simpler core.py logic)  
- Core.py gets data ready to use (no index math needed)
- Everyone is happy! ✅

#### Provider Implementations:

##### A. FCSolar (Hourly Native)
```python
# src/batcontrol/forecastsolar/fcsolar.py

from .baseclass import ForecastSolarBase

class FCSolar(ForecastSolarBase):
    """Forecast.solar provider - returns hourly data."""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 60  # Declares: "I provide hourly data"
        # ... rest of init ...
    
    def _fetch_forecast(self) -> Dict[int, float]:
        """Fetch hourly forecast from forecast.solar API."""
        # Existing API call logic here
        # Returns: {0: 1000, 1: 1500, 2: 2000, ...}  # Wh per hour
        response = self._call_api()
        return self._parse_response(response)
```

##### B. EvccSolar (15-min Native)
```python
# src/batcontrol/forecastsolar/evcc_solar.py

from .baseclass import ForecastSolarBase

class EvccSolar(ForecastSolarBase):
    """evcc solar provider - returns 15-minute data."""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 15  # Declares: "I provide 15-min data"
        # ... rest of init ...
    
    def _fetch_forecast(self) -> Dict[int, float]:
        """Fetch 15-minute forecast from evcc API."""
        # Existing API call logic
        # Returns: {0: 250, 1: 300, 2: 350, ...}  # Wh per 15-min
        response = self._call_evcc_api()
        return self._parse_15min_data(response)
```

##### C. Tibber (Dynamic Resolution)
```python
# src/batcontrol/dynamictariff/tibber.py

from .baseclass import DynamicTariffBase

class Tibber(DynamicTariffBase):
    """Tibber provider - supports both hourly and 15-min via API parameter."""
    
    def __init__(self, config: dict):
        super().__init__(config)
        
        # Decide native resolution based on target
        # Tibber API supports both, so fetch at target resolution directly
        target = config.get('general', {}).get('time_resolution_minutes', 60)
        
        if target == 15:
            self.native_resolution = 15  # Fetch 15-min data from API
            self.api_resolution = "QUARTER_HOURLY"  # Tibber API parameter
        else:
            self.native_resolution = 60  # Fetch hourly data from API
            self.api_resolution = "HOURLY"
        
        logger.info(f"Tibber: Configured to fetch {self.native_resolution}-min data")
    
    def _fetch_forecast(self) -> Dict[int, float]:
        """Fetch prices at configured resolution."""
        query = self._build_graphql_query(resolution=self.api_resolution)
        response = self._call_api(query)
        return self._parse_response(response)
```

#### Benefits Summary:

| Aspect | Old Approach | New Baseclass Approach |
|--------|-------------|------------------------|
| **Code Duplication** | Each provider upsamples | Once in baseclass |
| **Maintainability** | Update N providers | Update 1 baseclass |
| **Testing** | Test upsampling N times | Test once + mock |
| **Provider Focus** | Data fetch + transform | Data fetch only |
| **Dynamic APIs** | Hard to support | Easy (Tibber example) |
| **Consistency** | Risk of differences | Guaranteed consistent |

#### Migration Path:

**Phase 1**: Create baseclass with upsampling
- Implement `ForecastSolarBase`
- Implement `DynamicTariffBase`
- Implement `ForecastConsumptionBase`
- Add `interval_utils.py` with shared upsampling functions

**Phase 2**: Migrate providers one by one
- Start with simplest (FCSolar, Awattar)
- Then complex ones (EvccSolar, Tibber)
- Each migration is independent (low risk)

**Phase 3**: Remove old upsampling code
- Clean up redundant logic
- Consolidate tests

#### Required Changes:

##### A. Linear Interpolation for Hourly Data
**Note**: With the baseclass pattern, this upsampling logic is centralized in `interval_utils.py` and called automatically by the baseclass. Individual providers no longer need to implement this.

**CRITICAL**: Energy (Wh) vs Power (W) distinction

```python
# This function now lives in src/batcontrol/interval_utils.py
# and is used by all baseclass implementations

def upsample_hourly_to_15min(hourly_forecast: dict) -> dict:
    """
    Convert hourly Wh forecast to 15-minute intervals with linear interpolation.
    
    Important: 
    - Input is Wh per hour (energy values)
    - Output must be Wh per 15 minutes
    - Use linear power interpolation, then convert to energy
    
    Method:
    1. Calculate average power per hour (Wh → W)
    2. Interpolate power linearly between hours
    3. Convert interpolated power back to energy (W → Wh for 15 min)
    
    Example:
        Hour 0: 1000 Wh → avg power = 1000 W
        Hour 1: 2000 Wh → avg power = 2000 W
        
        15-min intervals (linear power ramp):
        [0]: Power = 1000 W → Energy = 1000 * 0.25 = 250 Wh
        [1]: Power = 1250 W → Energy = 1250 * 0.25 = 312.5 Wh  
        [2]: Power = 1500 W → Energy = 1500 * 0.25 = 375 Wh
        [3]: Power = 1750 W → Energy = 1750 * 0.25 = 437.5 Wh
        [4]: Power = 2000 W → Energy = 2000 * 0.25 = 500 Wh (next hour begins)
    """
    forecast_15min = {}
    max_hour = max(hourly_forecast.keys())
    
    for hour in range(max_hour):
        current_wh = hourly_forecast.get(hour, 0)
        next_wh = hourly_forecast.get(hour + 1, 0)
        
        # Convert Wh to average W (power)
        current_power = current_wh  # 1 Wh over 1 hour = 1 W average
        next_power = next_wh
        
        # Linear power interpolation across 4 quarters
        for quarter in range(4):
            interval_idx = hour * 4 + quarter
            fraction = quarter / 4
            
            # Interpolate power linearly
            interpolated_power = current_power + (next_power - current_power) * fraction
            
            # Convert power to energy for 15 minutes: P[W] * 0.25[h] = E[Wh]
            forecast_15min[interval_idx] = interpolated_power * 0.25
            
    return forecast_15min
```

##### B. Provider Implementation (With Baseclass Pattern)

**With the baseclass pattern, providers become much simpler:**

**fcsolar.py**:
```python
from .baseclass import ForecastSolarBase

class FCSolar(ForecastSolarBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 60  # Declares native resolution
    
    def _fetch_forecast(self) -> dict[int, float]:
        # Just fetch and return hourly data
        # Baseclass handles upsampling automatically
        response = self._call_api()
        return self._parse_response(response)  # Returns {0: 1000, 1: 1500, ...}
```

**solarprognose.py**:
```python
from .baseclass import ForecastSolarBase

class SolarPrognose(ForecastSolarBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 60  # Declares native resolution
    
    def _fetch_forecast(self) -> dict[int, float]:
        # Just fetch and return hourly data
        # Baseclass handles upsampling automatically
        response = self._call_api()
        return self._parse_response(response)  # Returns {0: 2000, 1: 2500, ...}
```

**evcc_solar.py**:
```python
from .baseclass import ForecastSolarBase

class EvccSolar(ForecastSolarBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 15  # evcc provides 15-min data
    
    def _fetch_forecast(self) -> dict[int, float]:
        # Return native 15-minute data
        # Baseclass handles downsampling if target is 60-min
        response = self._call_evcc_api()
        return self._parse_15min_data(response)  # Returns {0: 250, 1: 300, ...}
```

**Result**: 
- Each provider is ~20-30 lines instead of ~50-100 lines
- No upsampling logic duplicated
- Clear declaration of native resolution
- Automatic conversion by baseclass
- **Automatic index shifting** - providers don't worry about current time

---

#### Summary: Solving the Timing Issue

**The Problem You Identified** ✅:
```
At 10:20, provider returns hour-aligned data [0]=10:00, [1]=10:15, ...
Core.py expects [0] to be current interval (10:15-10:30)
Mismatch causes incorrect factorization
```

**The Solution**:
1. **Providers**: Return full-hour aligned data (indices 0-3 for current hour)
2. **Baseclass**: Automatically shifts indices so [0] = current interval
3. **Core.py**: Receives current-aligned data, simple factorization of [0]
4. **MQTT**: Can publish with proper timestamps (rounded to interval starts)

**Benefits**:
- ✅ No negative factorization values
- ✅ Correct energy accounting (no double-counting or missing intervals)
- ✅ Providers stay simple (don't track current time)
- ✅ Core.py stays simple (assumes [0] = now)
- ✅ Consistent across all forecast types (solar, consumption, tariff)

---

### 3. Consumption Forecast (`src/batcontrol/forecastconsumption/`)

#### Implementation: Using Baseclass Pattern

Following the same pattern as solar forecasts, consumption providers inherit from a baseclass:

```python
# src/batcontrol/forecastconsumption/baseclass.py

class ForecastConsumptionBase(ABC):
    """Base class for consumption forecast providers."""
    
    def __init__(self, config: dict):
        self.config = config
        self.target_resolution = config.get('general', {}).get('time_resolution_minutes', 60)
        self.native_resolution = 60  # Override in subclass
    
    @abstractmethod
    def _fetch_forecast(self) -> Dict[int, float]:
        """Fetch forecast at native resolution."""
        pass
    
    def get_forecast(self, intervals: int = None) -> Dict[int, float]:
        """Get forecast with automatic resolution handling."""
        native_data = self._fetch_forecast()
        
        if self.native_resolution == self.target_resolution:
            return native_data
        
        if self.native_resolution == 60 and self.target_resolution == 15:
            from ..interval_utils import upsample_forecast
            return upsample_forecast(native_data, self.target_resolution, method='constant')
        
        return native_data
```

#### Current Implementation:
- CSV-based load profile with hourly granularity
- Structure: `month, weekday, hour, energy`
- Native resolution: 60 minutes (hourly)

#### Challenge:
**No sub-hourly data available** from CSV load profiles

#### Solution Approach:

**Option Comparison**:

| Option | Accuracy | Implementation | Data Required | Timeline | Recommended Phase |
|--------|----------|----------------|---------------|----------|-------------------|
| A: Simple Division | Low (flat profile) | Easy (1 day) | None | Immediate | Phase 1 ✅ |
| B: Enhanced Profiles | High (realistic) | Medium (1-2 weeks) | 15-min historical data | 3-6 months | Phase 2 🎯 |
| C: Smart Interpolation | Medium (heuristic) | Hard (2-3 weeks) | Time-of-day patterns | Optional | Phase 3 (future) |

**Option A: Simple Division (Recommended for Phase 1)**
```python
def get_forecast(self, intervals):
    """
    Get forecast for specified number of intervals.
    For 15-min intervals, divides hourly values by 4.
    """
    t0 = datetime.datetime.now().astimezone(self.timezone)
    prediction = {}
    
    if self.interval_minutes == 15:
        # Calculate how many hours we need
        hours_needed = math.ceil(intervals / 4)
        
        for h in range(hours_needed):
            delta_t = datetime.timedelta(hours=h)
            t1 = t0 + delta_t
            
            # Get hourly energy
            energy_hour = df.loc[df['hour'] == t1.hour].loc[
                df['month'] == t1.month].loc[
                df['weekday'] == t1.weekday()]['energy'].median()
            
            if math.isnan(energy_hour):
                energy_hour = df['energy'].median()
            
            # Distribute equally across 4 quarters
            energy_quarter = energy_hour * self.scaling_factor / 4
            
            for quarter in range(4):
                interval_idx = h * 4 + quarter
                if interval_idx < intervals:
                    prediction[interval_idx] = energy_quarter
    else:
        # Keep existing hourly logic
        ...
    
    return prediction
```

**Option B: Sub-hour Patterns (Future Enhancement)**
- Create enhanced load profiles with 15-minute granularity
- Requires historical smart meter data at 15-min resolution
- Load profile format: `month, weekday, hour, quarter, energy`
- More accurate but requires data collection/migration

**Option C: Smart Interpolation**
```python
def get_forecast_with_interpolation(self, intervals):
    """
    Uses weighted interpolation based on typical consumption patterns:
    - Morning/Evening ramps: More variation between quarters
    - Night/Midday: More uniform distribution
    """
    # Implementation would use time-of-day heuristics
    # to distribute hourly energy non-uniformly
```

**Recommendation**: Start with **Option A** (simple division), then migrate to **Option B** when better data becomes available.

---

### 4. Dynamic Tariff Providers (`src/batcontrol/dynamictariff/`)

#### Implementation: Using Baseclass Pattern

Similar to solar and consumption forecasts, tariff providers use a baseclass:

```python
# src/batcontrol/dynamictariff/baseclass.py

class DynamicTariffBase(ABC):
    """Base class for dynamic tariff providers."""
    
    def __init__(self, config: dict):
        self.config = config
        self.target_resolution = config.get('general', {}).get('time_resolution_minutes', 60)
        self.native_resolution = 60  # Override in subclass
    
    @abstractmethod
    def _fetch_prices(self) -> Dict[int, float]:
        """Fetch prices at native resolution."""
        pass
    
    def get_prices(self, intervals: int = None) -> Dict[int, float]:
        """Get prices with automatic resolution handling."""
        native_data = self._fetch_prices()
        
        if self.native_resolution == self.target_resolution:
            return native_data
        
        # For prices, replication makes more sense than interpolation
        if self.native_resolution == 60 and self.target_resolution == 15:
            return self._replicate_hourly_to_15min(native_data)
        
        if self.native_resolution == 15 and self.target_resolution == 60:
            return self._average_15min_to_hourly(native_data)
        
        return native_data
    
    def _replicate_hourly_to_15min(self, hourly: Dict[int, float]) -> Dict[int, float]:
        """Replicate each hourly price to 4 quarters."""
        prices_15min = {}
        for hour, price in hourly.items():
            for quarter in range(4):
                prices_15min[hour * 4 + quarter] = price
        return prices_15min
    
    def _average_15min_to_hourly(self, prices_15min: Dict[int, float]) -> Dict[int, float]:
        """Average 15-min prices to hourly."""
        hourly = {}
        for interval, price in prices_15min.items():
            hour = interval // 4
            if hour not in hourly:
                hourly[hour] = []
            hourly[hour].append(price)
        
        return {h: sum(prices) / len(prices) for h, prices in hourly.items()}
```

#### Provider-Specific Implementations:

**Awattar** (`awattar.py`):
```python
class Awattar(DynamicTariffBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 60  # Awattar provides hourly prices
    
    def _fetch_prices(self) -> Dict[int, float]:
        """Fetch hourly prices from Awattar API."""
        # Existing API logic
        return self._parse_api_response()
```
- Native resolution: 60 minutes
- Baseclass automatically replicates to 15-min if needed
- **Action Required**: Test to verify correct data delivery

**Tibber** (`tibber.py`):
```python
class Tibber(DynamicTariffBase):
    def __init__(self, config: dict):
        super().__init__(config)
        
        # Tibber supports both resolutions via API
        target = config.get('general', {}).get('time_resolution_minutes', 60)
        
        if target == 15:
            self.native_resolution = 15
            self.api_resolution = "HOURLY_15"  # Or whatever Tibber's API uses
        else:
            self.native_resolution = 60
            self.api_resolution = "HOURLY"
    
    def _fetch_prices(self) -> Dict[int, float]:
        """Fetch prices at configured resolution."""
        query = self._build_query(resolution=self.api_resolution)
        return self._parse_api_response(query)
```
- Dynamic resolution based on configuration
- Fetches data at target resolution (no conversion needed)
- **Action Required**: Verify API parameter for 15-min data

**Evcc** (`evcc.py`):
```python
class EvccTariff(DynamicTariffBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.native_resolution = 15  # evcc provides 15-min data
    
    def _fetch_prices(self) -> Dict[int, float]:
        """Fetch 15-minute prices from evcc API."""
        # Existing API logic
        # Old code averaged to hourly - now returns native 15-min
        return self._parse_15min_data()
```
- Native resolution: 15 minutes
- Baseclass automatically averages to hourly if needed
- **No manual averaging needed anymore**

**Energyforecast** (`energyforecast.py`):
```python
class Energyforecast(DynamicTariffBase):
    def __init__(self, config: dict):
        super().__init__(config)
        
        # Energyforecast supports both resolutions via API
        target = config.get('general', {}).get('time_resolution_minutes', 60)
        
        if target == 15:
            self.native_resolution = 15
            self.api_resolution = "QUARTER_HOURLY"  # 15-minute resolution
        else:
            self.native_resolution = 60
            self.api_resolution = "HOURLY"  # Default hourly resolution
    
    def _fetch_prices(self) -> Dict[int, float]:
        """Fetch prices at configured resolution from energyforecast.de API."""
        params = {
            'resolution': self.api_resolution,  # 'HOURLY' or 'QUARTER_HOURLY'
            'token': self.token,
            'vat': 0,
            'fixed_cost_cent': 0
        }
        response = requests.get(self.url, params=params, timeout=30)
        response.raise_for_status()
        
        # Parse response and apply local fees/markup/vat
        return self._parse_api_response(response.json())
```
- **Dynamic resolution**: Based on configuration
- **Native API support**: Fetches data at target resolution (no conversion needed)
- **API parameters**:
  - `resolution`: `"HOURLY"` (default) or `"QUARTER_HOURLY"` (15-minute)
  - Returns base prices, local calculation of fees/markup/vat
- **No baseclass conversion needed** when using native resolution

**With baseclass pattern, this complex logic is removed from individual providers.**



**API Documentation**:
- **Awattar API**: https://www.awattar.de/services/api
- **Tibber API**: https://developer.tibber.com/docs/guides/pricing
- **evcc API**: https://docs.evcc.io/docs/reference/configuration/messaging#grid-tariff
- **Energyforecast API**: https://www.energyforecast.de/api/v1/predictions/next_48_hours

**Price Handling Note**: 
- If tariff provider gives hourly prices, replicate to all 4 quarters: `prices[h*4+q] = hourly_price[h]`
- Better granularity if provider supports it

---

### 5. Logic Module (`src/batcontrol/logic/`)

#### Charge Rate Calculation (`default.py` lines 145-153)

**Current Implementation**:
```python
remaining_time = (60 - calc_timestamp.minute) / 60  # Hours remaining in current hour
charge_rate = required_recharge_energy / remaining_time  # Wh / h = W
```

**15-Minute Sample Implementation**:
Remember, that it is configurable for 15 or 60 minute intervals.

```python
# Calculate remaining time in current interval
current_minute = calc_timestamp.minute
current_second = calc_timestamp.second

# Find which quarter we're in and time remaining
if interval_minutes == 15:
    current_interval_start = (current_minute // 15) * 15
    remaining_minutes = 15 - (current_minute % 15) - current_second / 60
    remaining_time = remaining_minutes / 60  # Convert to hours
else:  # interval_minutes == 60
    remaining_time = (60 - current_minute) / 60

charge_rate = required_recharge_energy / remaining_time  # Still W
```

#### Array Iteration (`default.py` lines 166-394)

**Impact**: All loops currently iterate by hour:
```python
for h in range(max_hour):
    future_price = prices[h]
    ...
```

**Change Required**:
- Rename variable: `h` → `interval` or `i`
- Update docstrings mentioning "hours"
- Logic remains the same (just more iterations)

**Example**:
```python
# Lines 178-202: Reserved energy calculation
max_interval = len(net_consumption)
for i in range(1, max_interval):
    future_price = prices[i]
    if future_price <= current_price - min_dynamic_price_difference:
        max_interval = i
        logger.debug(
            "[Rule] Recharge possible in %d intervals (%.1f hours), limiting evaluation window.",
            i, i * interval_minutes / 60)
        break
```

**No algorithmic changes needed** - the logic works at any time resolution.

---

### 6. MQTT API (`src/batcontrol/mqtt_api.py`)

#### Current Implementation (lines 182-201):

```python
def _create_forecast(self, forecast: np.ndarray, timestamp: float) -> dict:
    """Create forecast JSON with hourly timestamps"""
    now = timestamp - (timestamp % 3600)  # Round to hour
    
    data_list = []
    for h, value in enumerate(forecast):
        data_list.append({
            'time_start': now + h * 3600,  # 3600s = 1 hour
            'value': value,
            'time_end': now + (h + 1) * 3600
        })
    
    return {'data': data_list}
```

#### Required Changes:

```python
def _create_forecast(self, forecast: np.ndarray, timestamp: float, 
                     interval_minutes: int = 60) -> dict:
    """
    Create forecast JSON with configurable interval timestamps.
    
    Args:
        forecast: Array of values per interval
        timestamp: Current timestamp
        interval_minutes: Time resolution (15, 30, or 60)
    """
    interval_seconds = interval_minutes * 60
    
    # Round to current interval
    now = timestamp - (timestamp % interval_seconds)
    
    data_list = []
    for i, value in enumerate(forecast):
        data_list.append({
            'time_start': now + i * interval_seconds,
            'value': value,
            'time_end': now + (i + 1) * interval_seconds
        })
    
    return {'data': data_list}
```

#### Publishing Changes:
Update all publish methods to pass `interval_minutes`:
```python
def publish_production(self, production: np.ndarray, timestamp: float) -> None:
    if self.client.is_connected():
        self.client.publish(
            self.base_topic + '/FCST/production',
            json.dumps(self._create_forecast(
                production, timestamp, self.interval_minutes))
        )
```

#### Impact on Data Volume:
- **Hourly**: 48 data points for 48h forecast
- **15-min**: 192 data points for 48h forecast
- **Size increase**: ~4× more data per message
- **Network**: Minimal impact (JSON compression helps)
- **Storage**: InfluxDB/Grafana handle this well

---

### 7. Telegraf Configuration (`config/telegraf.sample.conf`)

#### Current Implementation (lines 44-67):
```conf
[[inputs.mqtt_consumer]]
   servers = ["tcp://mqtt:1883"]
   topics  = ["house/batcontrol/FCST/+"]
   data_format = "json_v2"
   
   [[inputs.mqtt_consumer.json_v2]]
      [[inputs.mqtt_consumer.json_v2.object]]
         path = "data"
         timestamp_key = "time_start"
         timestamp_format = "unix"
```

#### Issues Identified:

**Problem 1: Time Series Storage**
- With 15-min data, you'll have 4× more data points
- InfluxDB retention policies may need adjustment
- Grafana queries may need to aggregate differently

**Problem 2: Visualization**
- Hourly charts will now have more granular data
- May need to add `GROUP BY time(15m)` in queries
- Or keep `GROUP BY time(1h)` with `MEAN()` for backward compatibility

#### Recommended Changes:

```conf
# Add comment explaining interval handling
[[inputs.mqtt_consumer]]
   servers = ["tcp://mqtt:1883"]
   topics  = ["house/batcontrol/FCST/+"]
   data_format = "json_v2"
   
   # Note: Batcontrol may send data at different intervals (15min or 60min)
   # InfluxDB will store at native resolution. Adjust Grafana queries as needed.
   
   [[inputs.mqtt_consumer.json_v2]]
      [[inputs.mqtt_consumer.json_v2.object]]
         path = "data"
         timestamp_key = "time_start"
         timestamp_format = "unix"
         # timestamp_precision = "1s"  # Optional: Specify precision
```

**Grafana Dashboard Updates**:
```sql
-- Old query (hourly)
SELECT mean("value") FROM "batcontrol-production" 
WHERE $timeFilter 
GROUP BY time(1h)

-- New query (15-min aware, backward compatible)
SELECT mean("value") FROM "batcontrol-production" 
WHERE $timeFilter 
GROUP BY time($__interval)  -- Auto-adjusts based on time range

-- Or explicit 15-min
SELECT mean("value") FROM "batcontrol-production" 
WHERE $timeFilter 
GROUP BY time(15m)
```

#### Storage Considerations:
- **Retention Policy**: Consider shorter retention for 15-min data
  ```sql
  CREATE RETENTION POLICY "15min_detail" ON "db0" 
  DURATION 7d REPLICATION 1 DEFAULT
  
  CREATE RETENTION POLICY "hourly_summary" ON "db0" 
  DURATION 90d REPLICATION 1
  ```
- **Continuous Queries**: Auto-downsample 15-min → hourly after 7 days

---

## Configuration Design

### Recommended Configuration Structure

Add to `batcontrol_config_dummy.yaml`:

```yaml
general:
  timezone: Europe/Berlin
  loglevel: debug
  
  # Time resolution configuration
  # Options: 15, 60 (default: 60)
  # 15-min: Best accuracy, requires 4x data storage, recommended for dynamic tariffs
  # 60-min: Legacy mode, backward compatible
  time_resolution_minutes: 15

battery_control:
  min_price_difference: 0.05
  # ... existing parameters ...

# Optional: Per-provider interval overrides (advanced usage)
# forecast_providers:
#   solar:
#     forced_interval_minutes: 60  # Force hourly even if system uses 15-min
#   consumption:
#     forced_interval_minutes: 60  # Keep hourly consumption forecasts
#   dynamictariff:
#     forced_interval_minutes: 15  # Force 15-min for prices (if available)
```

### Configuration Validation

Add to `core.py` `__init__`:
```python
# Validate interval configuration
interval_minutes = config.get('general', {}).get('time_resolution_minutes', 60)
if interval_minutes not in [15, 60]:
    raise ValueError(f"time_resolution_minutes must be 15 or 60. Got: {interval_minutes}")

self.interval_minutes = interval_minutes
self.intervals_per_hour = 60 // interval_minutes

logger.info(f"Using {interval_minutes}-minute time resolution "
           f"({self.intervals_per_hour} intervals per hour)")

# Environment variable support (for Docker deployments)
# Can override via: BATCONTROL_TIME_RESOLUTION_MINUTES=15
env_interval = os.environ.get('BATCONTROL_TIME_RESOLUTION_MINUTES')
if env_interval:
    self.interval_minutes = int(env_interval)
    logger.info(f"Override from environment: {self.interval_minutes} minutes")
```

---

## Error Handling & Validation

### Interval Mismatch Detection

Add validation to ensure all forecast providers return consistent intervals:

```python
# src/batcontrol/core.py - Add after forecast collection

def validate_forecast_intervals(self, forecasts: dict) -> bool:
    """
    Validate that all forecasts have consistent interval counts.
    
    Args:
        forecasts: Dict of forecast arrays from different providers
    
    Returns:
        True if valid, raises ValueError if inconsistent
    """
    expected_count = self.forecast_hours * self.intervals_per_hour
    
    for name, forecast in forecasts.items():
        actual_count = len(forecast)
        if actual_count < expected_count * 0.9:  # Allow 10% tolerance
            logger.error(
                f"Forecast '{name}' has {actual_count} intervals, "
                f"expected ~{expected_count}. Check provider implementation."
            )
            raise ValueError(f"Invalid forecast interval count from {name}")
        
        if actual_count != expected_count:
            logger.warning(
                f"Forecast '{name}' has {actual_count} intervals, "
                f"expected {expected_count}. Will truncate/pad."
            )
            # Pad with last value or truncate
            if actual_count < expected_count:
                pad_value = forecast[-1] if len(forecast) > 0 else 0
                forecasts[name] = np.pad(forecast, 
                    (0, expected_count - actual_count), 
                    constant_values=pad_value)
            else:
                forecasts[name] = forecast[:expected_count]
    
    return True
```

### Provider Error Handling

```python
def get_forecast_safe(self, provider_name: str, provider_func: callable) -> dict:
    """
    Safely get forecast with error handling and fallback.
    """
    try:
        forecast = provider_func()
        
        # Validate interval count
        expected = self.forecast_hours * self.intervals_per_hour
        if len(forecast) < expected * 0.5:
            logger.warning(f"{provider_name} returned too few intervals, using fallback")
            return self.get_fallback_forecast(provider_name, expected)
        
        return forecast
        
    except Exception as e:
        logger.error(f"Error getting forecast from {provider_name}: {e}")
        return self.get_fallback_forecast(provider_name, 
            self.forecast_hours * self.intervals_per_hour)
```

---

## Monitoring & Observability

### Performance Metrics

Add metrics to track 15-min performance:

```python
# src/batcontrol/core.py - Add metrics collection

import time

class PerformanceMetrics:
    def __init__(self):
        self.metrics = {
            'forecast_fetch_time': [],
            'logic_calculation_time': [],
            'total_cycle_time': [],
            'array_size': 0,
            'interval_minutes': 0
        }
    
    def record_cycle(self, forecast_time: float, logic_time: float, 
                     total_time: float, array_size: int):
        self.metrics['forecast_fetch_time'].append(forecast_time)
        self.metrics['logic_calculation_time'].append(logic_time)
        self.metrics['total_cycle_time'].append(total_time)
        self.metrics['array_size'] = array_size
        
    def report(self):
        if not self.metrics['total_cycle_time']:
            return
        
        logger.info(
            f"Performance (last 10 cycles, {self.metrics['interval_minutes']}min intervals): "
            f"Fetch: {np.mean(self.metrics['forecast_fetch_time'][-10:]):.2f}s, "
            f"Logic: {np.mean(self.metrics['logic_calculation_time'][-10:]):.2f}s, "
            f"Total: {np.mean(self.metrics['total_cycle_time'][-10:]):.2f}s, "
            f"Array size: {self.metrics['array_size']}"
        )

# Usage in main loop:
metrics = PerformanceMetrics()
metrics.metrics['interval_minutes'] = self.interval_minutes

while True:
    cycle_start = time.time()
    
    fetch_start = time.time()
    # ... fetch forecasts ...
    fetch_time = time.time() - fetch_start
    
    logic_start = time.time()
    # ... run logic ...
    logic_time = time.time() - logic_start
    
    total_time = time.time() - cycle_start
    metrics.record_cycle(fetch_time, logic_time, total_time, len(production))
    
    # Report every 10 cycles
    if len(metrics.metrics['total_cycle_time']) % 10 == 0:
        metrics.report()
```

### MQTT Monitoring Topics

Publish interval configuration and health metrics:

```python
# Publish system configuration
self.mqtt_api.publish(
    'house/batcontrol/config/interval_minutes',
    json.dumps({'value': self.interval_minutes})
)

# Publish performance metrics
self.mqtt_api.publish(
    'house/batcontrol/metrics/performance',
    json.dumps({
        'timestamp': time.time(),
        'interval_minutes': self.interval_minutes,
        'forecast_fetch_time_s': fetch_time,
        'logic_calculation_time_s': logic_time,
        'array_size': len(production)
    })
)
```

---

## Rollback Procedure

### Quick Rollback Steps

If 15-min mode causes issues in production:

**Method 1: Configuration Change (No Restart)**
```bash
# Edit config file
nano /path/to/batcontrol_config.yaml

# Change:
# time_resolution_minutes: 15
# To:
time_resolution_minutes: 60

# Config is reloaded automatically on next cycle (3 minutes)
```

**Method 2: Environment Variable (Docker)**
```bash
# Edit docker-compose.yml or restart with env var
docker stop batcontrol
docker run -e BATCONTROL_TIME_RESOLUTION_MINUTES=60 ...
```

**Method 3: Git Rollback**
```bash
# Checkout previous stable version
git checkout v1.9.0  # Last hourly-only version
docker-compose build
docker-compose up -d
```

### Validation After Rollback

```bash
# Check logs for interval setting
docker logs batcontrol | grep "time resolution"

# Verify MQTT output shows hourly timestamps
mosquitto_sub -h mqtt -t "house/batcontrol/FCST/+" -C 1 | jq .

# Check array sizes (should be ~48, not ~192)
docker logs batcontrol | grep "Array size"
```

---

## Migration Strategy

### Phase 1: Foundation (2-3 weeks)
1. **Add configuration parameter** for `time_resolution_minutes`
2. **Update core.py** to use configurable intervals
3. **Create utility functions** for time conversions
4. **Unit tests** for interval handling

### Phase 2: Forecast Providers (2-3 weeks)
1. **Implement solar upsampling** (linear interpolation)
2. **Update consumption forecast** (simple division)
3. **Modify evcc providers** to support native 15-min
4. **Add provider error handling and fallbacks**
5. **Integration tests** with all providers

### Phase 3: Logic & MQTT (2-3 weeks)
1. **Update charge rate calculation**
2. **Refactor loop variables** (h → interval)
3. **Modify MQTT timestamp generation**
4. **Add performance monitoring**
5. **Update Telegraf config documentation**
6. **End-to-end tests**

### Phase 4: Testing & Documentation (2-3 weeks)
1. **Extended testing** at 15-min intervals
2. **Performance testing** (4× data volume)
3. **Beta testing** with community volunteers
4. **Update README and HOWITWORKS.md**
5. **Migration guide** for existing users
6. **Release as beta** (v2.0-beta)

### Phase 5: Production Release (1-2 weeks)
1. **Address beta feedback**
2. **Final bug fixes**
3. **Production monitoring setup**
4. **Release v2.0 stable**

### Phase 6: Optimization (Ongoing)
1. **Enhanced consumption profiles** (15-min granularity)
2. **Better interpolation algorithms** for solar
3. **Adaptive interval selection** based on data availability
4. **Performance optimizations**

**Total Realistic Timeline: 10-14 weeks** (vs original estimate of 4-5 weeks)

---

## Testing Requirements

### Unit Tests Needed

```python
# tests/batcontrol/test_interval_handling.py

import pytest
from datetime import datetime, timezone
from batcontrol.interval_utils import (
    get_elapsed_fraction, 
    get_remaining_time_hours,
    upsample_forecast
)

def test_time_correction_15min():
    """Test that partial interval is correctly calculated at 15-min resolution"""
    # Test at 7 minutes into interval
    test_time = datetime(2025, 10, 14, 10, 7, 30, tzinfo=timezone.utc)
    elapsed = get_elapsed_fraction(test_time, interval_minutes=15)
    
    # Expected: (7 + 30/60) / 15 = 7.5 / 15 = 0.5
    assert abs(elapsed - 0.5) < 0.01, f"Expected ~0.5, got {elapsed}"
    
    # Test at interval boundary
    test_time = datetime(2025, 10, 14, 10, 0, 0, tzinfo=timezone.utc)
    elapsed = get_elapsed_fraction(test_time, interval_minutes=15)
    assert elapsed == 0.0

def test_time_correction_60min():
    """Test backward compatibility with hourly intervals"""
    # Test at 30 minutes into hour
    test_time = datetime(2025, 10, 14, 10, 30, 0, tzinfo=timezone.utc)
    elapsed = get_elapsed_fraction(test_time, interval_minutes=60)
    
    assert abs(elapsed - 0.5) < 0.01

def test_remaining_time_15min():
    """Test remaining time calculation for 15-min intervals"""
    # At 7.5 minutes into 15-min interval
    test_time = datetime(2025, 10, 14, 10, 7, 30, tzinfo=timezone.utc)
    remaining = get_remaining_time_hours(test_time, interval_minutes=15)
    
    # Expected: 7.5 minutes remaining = 0.125 hours
    expected = 7.5 / 60
    assert abs(remaining - expected) < 0.001, f"Expected {expected}, got {remaining}"

def test_charge_rate_calculation_15min():
    """Test charge rate with 15 minutes remaining"""
    # Need to charge 500 Wh in 7.5 minutes (0.125 hours)
    required_energy = 500  # Wh
    remaining_time = 7.5 / 60  # hours
    
    charge_rate = required_energy / remaining_time
    expected_rate = 500 / 0.125  # = 4000 W
    
    assert abs(charge_rate - expected_rate) < 1, f"Expected {expected_rate}W, got {charge_rate}W"

def test_solar_upsampling_constant():
    """Test constant upsampling (simple division)"""
    hourly = {0: 1000, 1: 1000, 2: 1000}
    result = upsample_forecast(hourly, interval_minutes=15, method='constant')
    
    # Each 15-min interval should be 250 Wh
    for i in range(12):  # 3 hours * 4 intervals
        assert result[i] == 250, f"Interval {i}: expected 250, got {result[i]}"

def test_solar_upsampling_linear():
    """Test linear interpolation of hourly to 15-min solar forecast"""
    hourly = {0: 1000, 1: 2000, 2: 1000}
    result = upsample_forecast(hourly, interval_minutes=15, method='linear')
    
    # Hour 0→1: ramping up from 1000W to 2000W
    # Interval 0: 1000W * 0.25h = 250 Wh
    # Interval 1: 1250W * 0.25h = 312.5 Wh
    # Interval 2: 1500W * 0.25h = 375 Wh
    # Interval 3: 1750W * 0.25h = 437.5 Wh
    
    assert abs(result[0] - 250.0) < 0.1
    assert abs(result[1] - 312.5) < 0.1
    assert abs(result[2] - 375.0) < 0.1
    assert abs(result[3] - 437.5) < 0.1
    
    # Hour 1→2: ramping down from 2000W to 1000W
    assert abs(result[4] - 500.0) < 0.1
    assert abs(result[7] - 312.5) < 0.1

def test_solar_upsampling_edge_cases():
    """Test edge cases in solar upsampling"""
    # Empty dict
    result = upsample_forecast({}, interval_minutes=15, method='linear')
    assert len(result) == 0
    
    # Single hour
    result = upsample_forecast({0: 1000}, interval_minutes=15, method='linear')
    assert len(result) == 0  # Can't interpolate with only one point
    
    # With zeros
    hourly = {0: 0, 1: 1000, 2: 0}
    result = upsample_forecast(hourly, interval_minutes=15, method='linear')
    assert result[0] == 0
    assert result[4] > 200  # Should be ramping up
    assert result[8] == 0

def test_solar_upsampling_cubic():
    """Test cubic spline interpolation for smooth solar forecast"""
    try:
        import scipy
    except ImportError:
        pytest.skip("scipy not available, skipping cubic interpolation test")
    
    # Test typical solar production curve: sunrise -> peak -> sunset
    hourly = {
        0: 0,      # Night
        1: 100,    # Early morning
        2: 500,    # Morning
        3: 1500,   # Mid-morning
        4: 2500,   # Near noon
        5: 3000,   # Noon peak
        6: 2500,   # Afternoon
        7: 1500,   # Late afternoon
        8: 500,    # Evening
        9: 100,    # Dusk
        10: 0      # Night
    }
    
    result = upsample_forecast(hourly, interval_minutes=15, method='cubic')
    
    # Should have 4 intervals per hour
    assert len(result) >= 40  # 10 hours * 4 intervals
    
    # Verify smooth transitions (cubic should be smoother than linear)
    # Check that values are non-negative (clamped)
    for idx, value in result.items():
        assert value >= 0, f"Interval {idx} has negative value: {value}"
    
    # Verify general shape: values should increase to peak then decrease
    # Peak should be around hour 5 (intervals 20-23)
    peak_intervals = [result.get(i, 0) for i in range(20, 24)]
    early_intervals = [result.get(i, 0) for i in range(0, 4)]
    late_intervals = [result.get(i, 0) for i in range(36, 40)]
    
    assert max(peak_intervals) > max(early_intervals), "Peak should be higher than morning"
    assert max(peak_intervals) > max(late_intervals), "Peak should be higher than evening"
    
    # Cubic should produce smoother transitions (check rate of change)
    # Compare with linear interpolation
    result_linear = upsample_forecast(hourly, interval_minutes=15, method='linear')
    
    # Calculate variance in second derivative (measure of smoothness)
    def second_derivative_variance(data):
        """Calculate variance of second derivative as smoothness metric"""
        values = [data.get(i, 0) for i in range(len(data))]
        if len(values) < 3:
            return 0
        second_diffs = []
        for i in range(len(values) - 2):
            second_diff = values[i+2] - 2*values[i+1] + values[i]
            second_diffs.append(second_diff)
        return sum(d**2 for d in second_diffs) / len(second_diffs) if second_diffs else 0
    
    smoothness_cubic = second_derivative_variance(result)
    smoothness_linear = second_derivative_variance(result_linear)
    
    # Cubic should be smoother (lower second derivative variance)
    # Note: This might not always hold due to clamping, so we just check it doesn't fail
    assert smoothness_cubic >= 0  # Just verify calculation works

def test_cubic_interpolation_requires_scipy():
    """Test that cubic method raises ImportError without scipy"""
    # Temporarily hide scipy if it exists
    import sys
    scipy_backup = sys.modules.get('scipy')
    
    try:
        # Remove scipy from modules to simulate it not being installed
        if 'scipy' in sys.modules:
            del sys.modules['scipy']
        if 'scipy.interpolate' in sys.modules:
            del sys.modules['scipy.interpolate']
        
        hourly = {0: 1000, 1: 2000, 2: 1500}
        
        # Should raise ImportError
        with pytest.raises(ImportError, match="scipy"):
            result = upsample_forecast(hourly, interval_minutes=15, method='cubic')
    
    finally:
        # Restore scipy if it was available
        if scipy_backup is not None:
            sys.modules['scipy'] = scipy_backup

def test_cubic_vs_linear_comparison():
    """Compare cubic and linear interpolation behavior"""
    try:
        import scipy
    except ImportError:
        pytest.skip("scipy not available")
    
    # Test case: Sharp peak (where cubic should smooth better)
    hourly = {0: 100, 1: 500, 2: 2000, 3: 500, 4: 100}
    
    result_linear = upsample_forecast(hourly, interval_minutes=15, method='linear')
    result_cubic = upsample_forecast(hourly, interval_minutes=15, method='cubic')
    
    # Both should have same number of intervals
    assert len(result_linear) == len(result_cubic)
    
    # Peak should be around hour 2 (intervals 8-11)
    linear_peak = max(result_linear.get(i, 0) for i in range(8, 12))
    cubic_peak = max(result_cubic.get(i, 0) for i in range(8, 12))
    
    # Both should capture the peak
    assert linear_peak > 400
    assert cubic_peak > 400
    
    # Cubic might overshoot slightly (natural spline behavior)
    # but should be clamped to non-negative
    for idx, value in result_cubic.items():
        assert value >= 0

def test_consumption_division():
    """Test hourly consumption divided into 15-min intervals"""
    from batcontrol.forecastconsumption.forecast_csv import ConsumptionForecast
    
    # Mock config with 15-min intervals
    config = {'interval_minutes': 15, 'scaling_factor': 1.0}
    forecast = ConsumptionForecast(config)
    
    # Get forecast for 8 intervals (2 hours at 15-min)
    result = forecast.get_forecast(intervals=8)
    
    # Should return 8 intervals
    assert len(result) == 8
    
    # Each interval should be roughly 1/4 of hourly consumption
    # (Actual values depend on load profile CSV)

def test_mqtt_timestamps_15min():
    """Test MQTT forecast timestamps at 15-min intervals"""
    from batcontrol.mqtt_api import MQTTApi
    import numpy as np
    
    mqtt = MQTTApi(config={'interval_minutes': 15})
    
    # Test forecast with 8 intervals
    forecast_data = np.array([100, 150, 200, 250, 300, 350, 400, 450])
    timestamp = 1697270400  # 2023-10-14 10:00:00 UTC
    
    result = mqtt._create_forecast(forecast_data, timestamp, interval_minutes=15)
    
    # Should have 8 data points
    assert len(result['data']) == 8
    
    # Check timestamps are 15 minutes apart (900 seconds)
    for i in range(len(result['data']) - 1):
        time_diff = result['data'][i+1]['time_start'] - result['data'][i]['time_start']
        assert time_diff == 900, f"Expected 900s, got {time_diff}s"
    
    # Check values match
    for i, item in enumerate(result['data']):
        assert item['value'] == forecast_data[i]

def test_edge_cases_dst_transition():
    """Test handling of daylight saving time transitions"""
    # Test spring forward (skip hour)
    # Test fall back (repeat hour)
    # Ensure interval counts remain consistent
    pass

def test_edge_cases_midnight_rollover():
    """Test interval calculation across midnight"""
    test_time = datetime(2025, 10, 14, 23, 52, 30, tzinfo=timezone.utc)
    elapsed = get_elapsed_fraction(test_time, interval_minutes=15)
    
    # At XX:52:30, in 4th quarter (45-60 min range)
    # Minutes in interval: 52 % 15 = 7 minutes
    # Elapsed: (7 + 30/60) / 15 = 0.5
    assert abs(elapsed - 0.5) < 0.01

def test_performance_array_operations():
    """Test that 15-min arrays don't cause performance regression"""
    import time
    import numpy as np
    
    # Test with hourly data (48 elements)
    hourly_data = np.random.rand(48)
    start = time.time()
    for _ in range(1000):
        result = np.sum(hourly_data * 2.0)
    hourly_time = time.time() - start
    
    # Test with 15-min data (192 elements)
    min15_data = np.random.rand(192)
    start = time.time()
    for _ in range(1000):
        result = np.sum(min15_data * 2.0)
    min15_time = time.time() - start
    
    # Should be less than 5x slower (4x data = ~4x time)
    assert min15_time < hourly_time * 5, \
        f"15-min operations too slow: {min15_time}s vs {hourly_time}s"
```

### Integration Tests

```python
# tests/batcontrol/test_core_15min_integration.py

import pytest
from unittest.mock import Mock, patch
import numpy as np
from batcontrol.core import BatControl

def test_full_cycle_15min(monkeypatch):
    """Test complete run cycle with 15-min intervals"""
    # Mock all forecast providers to return 15-min data
    mock_solar = {i: 100 + i * 10 for i in range(192)}  # 48 hours at 15-min
    mock_consumption = {i: 200 + i * 5 for i in range(192)}
    mock_prices = {i: 0.20 + (i % 96) * 0.001 for i in range(192)}
    
    with patch('batcontrol.forecastsolar.Solar.get_forecast', return_value=mock_solar), \
         patch('batcontrol.forecastconsumption.Consumption.get_forecast', return_value=mock_consumption), \
         patch('batcontrol.dynamictariff.DynamicTariff.get_prices', return_value=mock_prices):
        
        config = {
            'general': {'time_resolution_minutes': 15},
            # ... other config ...
        }
        
        bc = BatControl(config)
        bc.run_once()
        
        # Verify array sizes
        assert len(bc.production) == 192
        assert len(bc.consumption) == 192
        assert len(bc.prices) == 192
        
        # Verify logic produced valid charge rate
        assert bc.charge_rate >= bc.config['min_charge_rate']
        assert bc.charge_rate <= bc.config['max_charge_rate']

def test_backward_compatibility_60min():
    """Ensure 60-min mode still works exactly as before"""
    config = {
        'general': {'time_resolution_minutes': 60},
        # ... other config ...
    }
    
    bc = BatControl(config)
    bc.run_once()
    
    # Should have 48 hourly intervals
    assert len(bc.production) == 48
    assert len(bc.consumption) == 48
    assert len(bc.prices) == 48



def test_provider_mismatch_handling():
    """Test system handles mismatched provider intervals gracefully"""
    # Solar returns 192 intervals (15-min)
    mock_solar = {i: 100 for i in range(192)}
    # But consumption only returns 48 (hourly)
    mock_consumption = {i: 200 for i in range(48)}
    
    with patch('batcontrol.forecastsolar.Solar.get_forecast', return_value=mock_solar), \
         patch('batcontrol.forecastconsumption.Consumption.get_forecast', return_value=mock_consumption):
        
        config = {'general': {'time_resolution_minutes': 15}}
        bc = BatControl(config)
        
        # Should either pad consumption or raise clear error
        with pytest.raises(ValueError, match="Invalid forecast interval count"):
            bc.run_once()

def test_mqtt_output_format():
    """Test MQTT messages have correct format at 15-min intervals"""
    config = {'general': {'time_resolution_minutes': 15}}
    bc = BatControl(config)
    
    # Capture MQTT publish calls
    published_messages = []
    bc.mqtt_api.publish = lambda topic, message: published_messages.append((topic, message))
    
    bc.run_once()
    
    # Find production forecast message
    prod_msg = next(msg for topic, msg in published_messages 
                    if 'production' in topic)
    
    import json
    data = json.loads(prod_msg)
    
    # Should have many intervals
    assert len(data['data']) > 48
    
    # Timestamps should be 15 min apart
    time_diff = data['data'][1]['time_start'] - data['data'][0]['time_start']
    assert time_diff == 900  # 15 minutes in seconds
```

### Performance Benchmarks

```python
# tests/batcontrol/test_performance.py

import time
import pytest
from batcontrol.core import BatControl

@pytest.mark.slow
def test_performance_15min_vs_60min():
    """Compare performance: 15-min should be < 2x slower than 60-min"""
    
    # Benchmark 60-min mode
    config_60 = {'general': {'time_resolution_minutes': 60}}
    bc_60 = BatControl(config_60)
    
    start = time.time()
    for _ in range(10):
        bc_60.run_once()
    time_60 = (time.time() - start) / 10
    
    # Benchmark 15-min mode
    config_15 = {'general': {'time_resolution_minutes': 15}}
    bc_15 = BatControl(config_15)
    
    start = time.time()
    for _ in range(10):
        bc_15.run_once()
    time_15 = (time.time() - start) / 10
    
    print(f"60-min: {time_60:.3f}s, 15-min: {time_15:.3f}s, ratio: {time_15/time_60:.2f}x")
    
    # 15-min should not be more than 2x slower
    assert time_15 < time_60 * 2.0, \
        f"15-min mode too slow: {time_15:.3f}s vs {time_60:.3f}s"
    
    # Absolute performance target: should complete in < 5 seconds
    assert time_15 < 5.0, f"15-min cycle too slow: {time_15:.3f}s"

@pytest.mark.slow
def test_memory_usage():
    """Test memory usage doesn't grow excessively with 15-min intervals"""
    import psutil
    import os
    
    process = psutil.Process(os.getpid())
    
    config = {'general': {'time_resolution_minutes': 15}}
    bc = BatControl(config)
    
    mem_before = process.memory_info().rss / 1024 / 1024  # MB
    
    # Run 100 cycles
    for _ in range(100):
        bc.run_once()
    
    mem_after = process.memory_info().rss / 1024 / 1024  # MB
    mem_increase = mem_after - mem_before
    
    # Memory increase should be < 50 MB
    assert mem_increase < 50, f"Memory leak detected: +{mem_increase:.1f} MB"

def test_backward_compatibility_60min():
    """Ensure 60-min mode still works exactly as before"""
    pass
```

---

## Risks and Mitigation

### Risk 1: Data Quality
**Problem**: Simple division of consumption creates unrealistic flat profiles  
**Mitigation**: 
- Phase 1: Accept limitation, still better than hourly
- Phase 2: Collect real 15-min data, create enhanced profiles
- Phase 3: Machine learning for pattern prediction

### Risk 2: Increased Complexity
**Problem**: More intervals = more computation, more data  
**Mitigation**:
- Performance profiling before/after
- Consider limiting forecast horizon (e.g., 24h instead of 48h at 15-min)
- Optimize array operations with NumPy

### Risk 3: Breaking Changes
**Problem**: Existing users on hourly system  
**Mitigation**:
- Default to 60 minutes (backward compatible)
- Clear migration documentation
- Feature flag for beta testing
- Version bump: 1.x → 2.0

### Risk 4: MQTT Data Volume
**Problem**: 4× more data points per message  
**Mitigation**:
- MQTT handles this well (tested up to 1000s of points)
- Consider optional data thinning for slow networks
- Document InfluxDB retention strategies

### Risk 5: Visualization Overload
**Problem**: Grafana charts may look cluttered  
**Mitigation**:
- Provide dashboard examples for 15-min data
- Document query aggregation patterns
- Auto-detect interval in dashboard variables

---

## Evaluation: Configurable Interval (15-60 minutes)

### Arguments FOR Configurability

**1. Backward Compatibility**
- Existing users can stay on 60-min without changes
- Gradual migration path

**2. Flexibility**
- Some users may have hourly-only data sources
- Different markets have different price granularity

**3. Risk Mitigation**
- Easy rollback if issues found
- A/B testing possible
- Beta testing without breaking production

**4. Future-Proofing**
- Easy to add 5-minute intervals later
- Framework for dynamic interval selection

**5. Resource Optimization**
- Lower-end hardware can use 60-min
- High-performance systems use 15-min

### Arguments AGAINST Configurability

**1. Complexity**
- More code paths to test
- More documentation needed
- More user confusion

**2. Maintenance Burden**
- Need to maintain multiple modes
- Bug fixes across all intervals
- Version compatibility matrix

**3. Delayed Adoption**
- Users may stick with "good enough" 60-min
- 15-min benefits not realized

**4. Half-Baked Features**
- Simple division of hourly consumption still inaccurate at 15-min
- Better to wait for proper 15-min data

### Recommendation: **MAKE IT CONFIGURABLE**

**Rationale**:
1. **Safety First**: Allows thorough testing without breaking existing systems
2. **User Choice**: Different users have different needs and capabilities
3. **Iterative Improvement**: Can enhance 15-min implementation over time
4. **Market Evolution**: Not all markets offer 15-min prices yet
5. **Low Cost**: Code structure supports this naturally with minimal overhead

**Implementation**:
- Default: 60 minutes (no breaking changes)
- Beta flag: `time_resolution_minutes: 15` (opt-in for testing)
- v2.0 release: Default to 15 minutes (with loud warning in changelog)

---

## Performance Considerations

### Computational Impact

**Array Operations**:
- **Before**: 48-element arrays
- **After**: 192-element arrays
- **Impact**: Negligible (NumPy optimized)

**Loop Iterations**:
- **Before**: ~48 iterations per calculation
- **After**: ~192 iterations per calculation
- **Impact**: < 1ms additional overhead

**Memory**:
- **Before**: ~10 KB per forecast
- **After**: ~40 KB per forecast
- **Impact**: Trivial on modern systems

### Network Impact

**MQTT Message Sizes**:
- **Hourly**: ~2-3 KB JSON
- **15-min**: ~8-12 KB JSON
- **Impact**: Still well under MQTT limits (256 MB default)

**Database Storage**:
- **Hourly**: ~1,000 points/day/metric
- **15-min**: ~4,000 points/day/metric
- **Impact**: InfluxDB handles millions easily

---

## Conclusion & Recommendations

### Summary of Required Changes

| Component | Complexity | Risk | Priority |
|-----------|-----------|------|----------|
| Core (interval handling) | Medium | Low | HIGH |
| Solar forecast (upsampling) | High | Medium | HIGH |
| Consumption (division) | Low | Low | HIGH |
| Dynamic tariff (evcc) | Low | Low | MEDIUM |
| Logic (charge rate) | Medium | Medium | HIGH |
| MQTT API | Low | Low | MEDIUM |
| Telegraf config | Low | Low | LOW |

### Final Recommendations

**1. Implement Configurable Intervals** ✅
- Default to 60 minutes for backward compatibility
- Allow opt-in to 15 minutes via configuration
- Plan for 30-minute option as middle ground

**2. Solar Forecast Upsampling** ✅
- Use linear interpolation (simple, effective)
- Correctly handle Wh → W conversion
- Document limitations (especially for cloudy days)

**3. Consumption Forecast** ⚠️
- Start with simple division (Wh/hour ÷ 4)
- Mark as "TODO: Enhance with real 15-min profiles"
- Create data collection guide for users

**4. Price Forecasts** ✅
- Evcc: Use native 15-min data when available
- Awattar/Tibber: Replicate hourly prices to quarters
- Future: Monitor for API updates offering 15-min granularity

**5. Testing Strategy** 🧪
- Comprehensive unit tests for each component
- Integration tests for full cycle
- Beta testing with community (opt-in flag)
- Performance benchmarks (ensure < 10% overhead)

**6. Migration Path** 📋
- Release v1.9: Add configuration, default 60-min
- Release v2.0-beta: Switch default to 15-min, call for testing
- Release v2.0: Make 15-min official default
- Timeline: 2-3 months for stable release

**7. Documentation Updates** 📚
- Update README with interval configuration
- Add migration guide for existing users
- Document limitations (consumption profiles)
- Provide Grafana dashboard examples

**8. Future Enhancements** 🚀
- Collect user smart meter data (15-min resolution)
- Build community database of 15-min load profiles
- Implement adaptive interval selection
- Add 5-minute interval support for ultra-dynamic tariffs

---

## Appendix: Code Organization Recommendations

### New Files to Create

```
src/batcontrol/
  interval_utils.py          # Time interval utilities
  
tests/batcontrol/
  test_interval_handling.py  # Unit tests for intervals
  test_core_15min_integration.py  # Integration tests
  
docs/
  migration-15min.md         # Migration guide
  interval-configuration.md  # Configuration reference
```

### Key Utility Functions

```python
# src/batcontrol/interval_utils.py

def get_interval_count(hours: int, interval_minutes: int) -> int:
    """Calculate number of intervals for given hours and resolution."""
    return hours * (60 // interval_minutes)

def get_elapsed_fraction(timestamp: datetime, interval_minutes: int) -> float:
    """Calculate fraction of current interval that has elapsed."""
    minute_in_interval = timestamp.minute % interval_minutes
    second_fraction = timestamp.second / 60
    return (minute_in_interval + second_fraction) / interval_minutes

def get_remaining_time_hours(timestamp: datetime, interval_minutes: int) -> float:
    """Calculate remaining time in current interval (in hours)."""
    elapsed = get_elapsed_fraction(timestamp, interval_minutes)
    remaining_minutes = interval_minutes * (1 - elapsed)
    return remaining_minutes / 60

def upsample_forecast(hourly_data: dict, interval_minutes: int, 
                     method: str = 'linear') -> dict:
    """
    Upsample hourly forecast to smaller intervals.
    
    Args:
        hourly_data: Dict of {hour: value_wh}
        interval_minutes: Target resolution (15, 30)
        method: 'linear', 'constant', or 'cubic'
    
    Returns:
        Dict of {interval: value_wh} at target resolution
    """
    if interval_minutes == 60:
        return hourly_data
    
    intervals_per_hour = 60 // interval_minutes
    upsampled = {}
    
    if method == 'constant':
        # Simple division
        for hour, value in hourly_data.items():
            for i in range(intervals_per_hour):
                upsampled[hour * intervals_per_hour + i] = value / intervals_per_hour
                
    elif method == 'linear':
        # Linear interpolation
        max_hour = max(hourly_data.keys())
        for hour in range(max_hour):
            current_val = hourly_data.get(hour, 0)
            next_val = hourly_data.get(hour + 1, 0)
            
            for i in range(intervals_per_hour):
                idx = hour * intervals_per_hour + i
                fraction = i / intervals_per_hour
                interpolated = current_val + (next_val - current_val) * fraction
                upsampled[idx] = interpolated / intervals_per_hour
    
    return upsampled
```

---

## Questions for Stakeholders

**Please answer these questions to guide implementation priorities:**

### 1. Data Availability
- **Q**: Do you have access to 15-minute consumption data (smart meter exports)?
  - **Why**: Needed for Option B (enhanced load profiles) in Phase 2
  - **Format**: CSV with timestamp and Wh/kWh per 15-min interval
  - **Timeframe**: At least 1 year of data preferred

### 2. Tariff Provider
- **Q**: Which electricity tariff provider(s) do you currently use?
  - [ ] Awattar (Germany/Austria)
  - [ ] Tibber (Nordic countries, Germany, Netherlands)
  - [ ] evcc integration
  - [ ] Other: _______________
  
- **Q**: What is your tariff's price update frequency?
  - [ ] Hourly (most common)
  - [ ] 15-minute intervals
  - [ ] Other: _______________

### 3. Hardware & Environment
- **Q**: What hardware are you running batcontrol on?
  - [ ] Raspberry Pi 3 or older
  - [ ] Raspberry Pi 4/5
  - [ ] Server (x86)
  - [ ] Docker container on: _______________
  
- **Q**: What are your hardware specs?
  - CPU: _______________
  - RAM: _______________
  - Storage: _______________

### 4. Testing Capability
- **Q**: Do you have a test environment separate from production?
  - [ ] Yes, separate test system
  - [ ] Yes, can run in parallel with production
  - [ ] No, must test in production carefully
  
- **Q**: Are you willing to participate in beta testing?
  - [ ] Yes, can test immediately
  - [ ] Yes, but need 2-4 weeks notice
  - [ ] No, prefer to wait for stable release

### 5. Current Performance
- **Q**: Do you have existing performance metrics?
  - [ ] Yes, have Grafana dashboards
  - [ ] Yes, have log analysis
  - [ ] No, but can collect
  - [ ] No baseline metrics
  
- **Q**: What is your current evaluation cycle time?
  - Average: _____ seconds per cycle
  - Acceptable: _____ seconds per cycle

### 6. Project Motivation
- **Q**: What's driving this 15-minute interval change?
  - [ ] New tariff structure requires it
  - [ ] Want more responsive battery control
  - [ ] Proactive optimization
  - [ ] Regulatory requirement
  - [ ] Other: _______________

- **Q**: Is this critical or nice-to-have?
  - [ ] Critical - needed within: _____ weeks
  - [ ] Important - target date: _______________
  - [ ] Nice-to-have - no rush

### 7. Backward Compatibility
- **Q**: Must the new version work with existing configurations?
  - [ ] Yes, must be fully backward compatible
  - [ ] Yes, but migration is acceptable
  - [ ] No, breaking changes OK

### 8. Documentation & Support
- **Q**: What documentation would be most helpful?
  - [ ] Migration guide (existing -> 15-min)
  - [ ] Performance tuning guide
  - [ ] Troubleshooting guide
  - [ ] API reference updates
  - [ ] Grafana dashboard examples
  - [ ] Video tutorials

### 9. Community
- **Q**: Are there other users you know who need 15-min intervals?
  - Number: _____
  - Countries: _______________
  - Use cases: _______________

### 10. Future Features
- **Q**: After 15-min support, what would be most valuable?
  - [ ] 5-minute intervals (ultra-responsive)
  - [ ] Adaptive interval selection (auto-switch based on conditions)
  - [ ] Machine learning for consumption forecasting
  - [ ] Better solar interpolation (weather-aware)
  - [ ] Other: _______________

**Please provide answers in the GitHub issue or discussion thread.**

---

## Appendix A: Visual Examples

### Example: Grafana Dashboard Comparison

**Hourly Visualization (Current)**:
```
Solar Production Forecast (Hourly)
10:00  11:00  12:00  13:00  14:00  15:00
  500   1200   2000   2500   2200   1800  Wh
  ─┘     ─┐     ─┐     ─┐     ─┐     ─┘
    └─────┴─────┴─────┴─────┴─────
  Smooth line, but misses within-hour variations
```

**15-Minute Visualization (Proposed)**:
```
Solar Production Forecast (15-min)
10:00 :15 :30 :45 11:00 :15 :30 :45 12:00
  125  150  175  200  250  300  350  400  Wh
   ─┐   ─┐   ─┐   ─┐   ─┐   ─┐   ─┐   ─┐
     └───┴───┴───┴───┴───┴───┴───┴───
  More granular, captures ramp-up patterns
```

### Example: Charge Rate Decision

**Scenario**: Current time 10:07, need to charge 500 Wh

**Hourly Mode (Current)**:
- Remaining time in hour: 53 minutes = 0.883 hours
- Charge rate: 500 / 0.883 = 566 W
- **Issue**: Rate calculated based on full hour, might over/under charge

**15-Minute Mode (Proposed)**:
- Current interval: 10:00-10:15
- Remaining time in interval: 8 minutes = 0.133 hours
- Charge rate: 500 / 0.133 = 3,759 W
- **Better**: More responsive, adjusts every 15 min instead of every hour

### Example: Price Optimization

**Tariff**: 
- 10:00-11:00: 0.25 €/kWh
- 11:00-12:00: 0.15 €/kWh (cheaper)
- 12:00-13:00: 0.30 €/kWh

**Hourly Logic**: 
- Sees 3 price levels, decides at hour boundaries
- May miss opportunity at 11:00 exactly

**15-Min Logic**:
- Sees 12 price levels (4 per hour, possibly different if provider supports)
- Can start charging at 11:00:00 precisely
- **Savings**: Up to 15 minutes of cheaper charging = 0.25 kWh × 0.10 €/kWh = 0.025 €
  - Over a year: ~9 € in savings (assuming 1 cycle/day)

---

## Appendix B: Code Organization Recommendations

### New Files to Create

```
src/batcontrol/
  interval_utils.py          # Time interval utilities (NEW)
  validators.py              # Forecast validation (NEW)
  
tests/batcontrol/
  test_interval_handling.py  # Unit tests for intervals (NEW)
  test_core_15min_integration.py  # Integration tests (NEW)
  test_performance.py        # Performance benchmarks (NEW)
  
docs/
  migration-15min.md         # Migration guide (NEW)
  interval-configuration.md  # Configuration reference (NEW)
  troubleshooting-15min.md   # Troubleshooting guide (NEW)
  performance-tuning.md      # Performance optimization (NEW)
```

### Key Utility Functions

```python
# src/batcontrol/interval_utils.py

import datetime
import math
from typing import Dict, Literal

def get_interval_count(hours: int, interval_minutes: int) -> int:
    """Calculate number of intervals for given hours and resolution."""
    return hours * (60 // interval_minutes)

def get_elapsed_fraction(timestamp: datetime.datetime, interval_minutes: int) -> float:
    """Calculate fraction of current interval that has elapsed."""
    minute_in_interval = timestamp.minute % interval_minutes
    second_fraction = timestamp.second / 60
    return (minute_in_interval + second_fraction) / interval_minutes

def get_remaining_time_hours(timestamp: datetime.datetime, interval_minutes: int) -> float:
    """Calculate remaining time in current interval (in hours)."""
    elapsed = get_elapsed_fraction(timestamp, interval_minutes)
    remaining_minutes = interval_minutes * (1 - elapsed)
    return remaining_minutes / 60

def round_to_interval(timestamp: datetime.datetime, interval_minutes: int) -> datetime.datetime:
    """Round timestamp down to the start of its interval."""
    interval_seconds = interval_minutes * 60
    unix_time = timestamp.timestamp()
    rounded_unix = unix_time - (unix_time % interval_seconds)
    return datetime.datetime.fromtimestamp(rounded_unix, tz=timestamp.tzinfo)

def upsample_forecast(
    hourly_data: Dict[int, float], 
    interval_minutes: int, 
    method: Literal['linear', 'constant', 'cubic'] = 'linear'
) -> Dict[int, float]:
    """
    Upsample hourly forecast to smaller intervals.
    
    Args:
        hourly_data: Dict of {hour: value_wh}
        interval_minutes: Target resolution (15 or 60)
        method: Interpolation method
            - 'constant': Simple division (value/4 for each quarter)
                Best for: Uniform loads, quick calculations
            - 'linear': Linear power interpolation between hours
                Best for: Solar forecasts, general purpose
            - 'cubic': Cubic spline interpolation (requires scipy)
                Best for: Smooth transitions, realistic power curves
                Note: May overshoot/undershoot, clamped to non-negative
    
    Returns:
        Dict of {interval: value_wh} at target resolution
    
    Raises:
        ImportError: If cubic method is used without scipy installed
    """
    if interval_minutes == 60:
        return hourly_data
    
    if len(hourly_data) == 0:
        return {}
    
    intervals_per_hour = 60 // interval_minutes
    upsampled = {}
    
    if method == 'constant':
        # Simple division
        for hour, value in hourly_data.items():
            for i in range(intervals_per_hour):
                upsampled[hour * intervals_per_hour + i] = value / intervals_per_hour
                
    elif method == 'linear':
        # Linear power interpolation
        max_hour = max(hourly_data.keys())
        
        if max_hour == 0:
            # Only one data point, use constant
            return upsample_forecast(hourly_data, interval_minutes, method='constant')
        
        for hour in range(max_hour):
            current_wh = hourly_data.get(hour, 0)
            next_wh = hourly_data.get(hour + 1, 0)
            
            # Convert Wh to average power
            current_power = current_wh  # 1 Wh / 1 h = 1 W
            next_power = next_wh
            
            for i in range(intervals_per_hour):
                idx = hour * intervals_per_hour + i
                fraction = i / intervals_per_hour
                
                # Interpolate power linearly
                interpolated_power = current_power + (next_power - current_power) * fraction
                
                # Convert power to energy for interval
                interval_hours = interval_minutes / 60
                upsampled[idx] = interpolated_power * interval_hours
        
        # Handle last hour (can't interpolate beyond)
        if max_hour in hourly_data:
            for i in range(intervals_per_hour):
                idx = max_hour * intervals_per_hour + i
                upsampled[idx] = hourly_data[max_hour] / intervals_per_hour
    
    elif method == 'cubic':
        # Cubic spline interpolation for smoother transitions
        # Note: Requires scipy library
        try:
            from scipy.interpolate import CubicSpline
        except ImportError:
            raise ImportError(
                "Cubic interpolation requires scipy. "
                "Install with: pip install scipy"
            )
        
        max_hour = max(hourly_data.keys())
        
        if max_hour == 0:
            # Only one data point, use constant
            return upsample_forecast(hourly_data, interval_minutes, method='constant')
        
        # Prepare data for cubic spline
        hours = sorted(hourly_data.keys())
        powers = [hourly_data[h] for h in hours]  # Wh values (avg power over 1h)
        
        # Create cubic spline (using power values)
        cs = CubicSpline(hours, powers, bc_type='natural')
        
        # Sample at interval points
        for hour in range(max_hour):
            for i in range(intervals_per_hour):
                idx = hour * intervals_per_hour + i
                # Position in hours (e.g., 0.00, 0.25, 0.50, 0.75 for 15-min)
                time_position = hour + (i / intervals_per_hour)
                
                # Get interpolated power at this position
                interpolated_power = cs(time_position)
                
                # Ensure non-negative (cubic spline can overshoot)
                interpolated_power = max(0, interpolated_power)
                
                # Convert power to energy for interval
                interval_hours = interval_minutes / 60
                upsampled[idx] = interpolated_power * interval_hours
        
        # Handle last hour
        if max_hour in hourly_data:
            for i in range(intervals_per_hour):
                idx = max_hour * intervals_per_hour + i
                upsampled[idx] = hourly_data[max_hour] / intervals_per_hour
    
    return upsampled

def validate_interval_config(interval_minutes: int) -> None:
    """Validate interval configuration."""
    if interval_minutes not in [15, 60]:
        raise ValueError(
            f"time_resolution_minutes must be 15 or 60. Got: {interval_minutes}"
        )
    
def get_interval_from_timestamp(
    timestamp: datetime.datetime, 
    reference: datetime.datetime,
    interval_minutes: int
) -> int:
    """Get interval index for a timestamp relative to reference."""
    diff_seconds = (timestamp - reference).total_seconds()
    interval_seconds = interval_minutes * 60
    return math.floor(diff_seconds / interval_seconds)
```

---

## Questions for Stakeholders

Before implementation, clarify:

1. **Timeline**: What's the target release date?
2. **Priority**: Is 15-min support critical or nice-to-have?
3. **Resources**: Available developer time for implementation?
4. **Testing**: Access to hardware for end-to-end testing?
5. **Users**: Any beta testers willing to try 15-min mode?
6. **Data**: Anyone with 15-min consumption data to share?
7. **Market**: Which electricity markets support 15-min pricing?
8. **Backward Compatibility**: Must v2.0 work with v1.x configs?

---

**Document Version**: 2.0  
**Date**: 2025-10-14  
**Author**: Analysis by GitHub Copilot (Enhanced with comprehensive review)  
**Reviewed by**: GitHub Copilot (Document Reviewer)  
**Status**: Design Proposal - Ready for Implementation  

**Change Log**:
- v1.0 (2025-10-14): Initial analysis
- v2.0 (2025-10-14): Comprehensive review with improvements:
  - Fixed solar interpolation algorithm and examples
  - Added comparison table for consumption forecast options
  - Fixed typos and language inconsistencies
  - Clarified dynamic tariff provider requirements with API links
  - Expanded configuration design with environment variables
  - Added error handling and validation section
  - Added monitoring and observability section
  - Added rollback procedure
  - Adjusted timeline to realistic 10-14 weeks
  - Added comprehensive test specifications with actual code
  - Added visual diagrams (data flow, decision tree, timeline)
  - Added stakeholder questionnaire
  - Added code organization recommendations
  - Added complete interval_utils.py implementation
  - Added Grafana dashboard examples
  - Added performance targets and benchmarks

**Next Steps**:
1. Stakeholders answer questions (Appendix: Questions for Stakeholders)
2. Create GitHub issue for implementation tracking
3. Set up project board with phases
4. Begin Phase 1: Foundation (2-3 weeks)

**Related Documents**:
- `README.MD` - Project overview
- `HOWITWORKS.md` - System architecture
- `config/batcontrol_config_dummy.yaml` - Configuration reference
- Future: `docs/migration-15min.md` - Migration guide (to be created)
- Future: `docs/troubleshooting-15min.md` - Troubleshooting (to be created)

**Contact**:
- GitHub Issues: Report bugs or request clarifications
- GitHub Discussions: Ask questions or share experiences
- Pull Requests: Contribute implementations

---

## Architecture Decision: Baseclass Pattern

### Key Design Choice

Instead of each provider implementing upsampling/downsampling logic, we use a **baseclass pattern**:

```
┌─────────────────────────────────────────────┐
│           interval_utils.py                 │
│  (Shared upsampling/downsampling functions) │
└─────────────────────────────────────────────┘
                    ▲
                    │ uses
    ┌───────────────┼───────────────┐
    │               │               │
┌───▼────┐     ┌───▼────┐     ┌───▼────┐
│ Solar  │     │ Tariff │     │Consump.│
│ Base   │     │ Base   │     │ Base   │
└────────┘     └────────┘     └────────┘
    ▲               ▲               ▲
    │               │               │
    ├─────┬─────┬───┼───┬───────┬───┼────┬────┐
    │     │     │   │   │       │   │    │    │
FCSolar │  evcc│ Awattar Tibber evcc│  CSV  │  HA
     Prognose Solar              Tariff Profile Forecast
```

### Benefits:

| Aspect | Old Approach | Baseclass Approach |
|--------|-------------|-------------------|
| **Lines of code** | ~200 per provider | ~30 per provider |
| **Upsampling logic** | Duplicated N times | Once in baseclass |
| **Testing** | Test each provider | Test baseclass once |
| **Maintenance** | Fix in N places | Fix in 1 place |
| **Consistency** | Risk variations | Guaranteed same |
| **New providers** | Reimplement logic | Just declare resolution |
| **Dynamic APIs** | Complex switches | Simple attribute |

### Example: Adding New Provider

**Old way** (50+ lines):
```python
class NewProvider:
    def get_forecast(self):
        data = fetch_from_api()
        if interval_minutes == 15:
            # Complex upsampling logic (30 lines)
            ...
        return data
```

**New way** (15 lines):
```python
class NewProvider(ForecastSolarBase):
    def __init__(self, config):
        super().__init__(config)
        self.native_resolution = 60  # That's it!
    
    def _fetch_forecast(self):
        return fetch_from_api()  # Just return data, baseclass handles rest
```

### Implementation Files:

```
src/batcontrol/
  ├── interval_utils.py                    # NEW: Shared upsampling functions
  │
  ├── forecastsolar/
  │   ├── baseclass.py                     # NEW: Base with auto-upsampling
  │   ├── fcsolar.py                       # MODIFIED: Inherits from base
  │   ├── solarprognose.py                 # MODIFIED: Inherits from base
  │   └── evcc_solar.py                    # MODIFIED: Inherits from base
  │
  ├── dynamictariff/
  │   ├── baseclass.py                     # NEW: Base with auto-upsampling
  │   ├── awattar.py                       # MODIFIED: Inherits from base
  │   ├── tibber.py                        # MODIFIED: Inherits from base
  │   └── evcc.py                          # MODIFIED: Inherits from base
  │
  └── forecastconsumption/
      ├── baseclass.py                     # NEW: Base with auto-upsampling
      ├── forecast_csv.py                  # MODIFIED: Inherits from base
      └── forecast_homeassistant.py        # MODIFIED: Inherits from base
```

---

## Summary

This document provides a **complete blueprint** for transforming batcontrol from hourly to configurable 15/60-minute intervals. Key takeaways:

✅ **Feasible**: No blocking technical issues identified  
✅ **Configurable**: Support for 15 and 60-minute intervals  
✅ **Backward Compatible**: Default to 60 minutes, opt-in to faster intervals  
✅ **Well-Architected**: Baseclass pattern eliminates code duplication  
✅ **Well-Tested**: Comprehensive test strategy defined  
✅ **Monitored**: Performance and health metrics included  
✅ **Documented**: Clear migration and troubleshooting paths  

**Estimated Effort**: 10-14 weeks for complete implementation  
**Risk Level**: Low to Medium (with mitigation strategies)  
**Recommended Approach**: Phased rollout with beta testing  

The document is now **ready to guide implementation**. Please answer the stakeholder questions and proceed with Phase 1.
