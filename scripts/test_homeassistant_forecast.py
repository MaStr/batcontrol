#!/usr/bin/env python3
"""
Test script for ForecastConsumptionHomeAssistant class

This script demonstrates how to use the ForecastConsumptionHomeAssistant class
to fetch and display consumption forecasts from HomeAssistant.

Usage:
    python test_homeassistant_forecast.py

Configuration:
    Update the configuration variables below with your HomeAssistant details.
"""

import sys
import logging
from datetime import datetime
import pytz

# Add parent directory to path to import batcontrol modules
sys.path.insert(0, '../src')

from batcontrol.forecastconsumption.consumption import Consumption


# Configure logging
# Change level to DEBUG to see detailed WebSocket communication
logging.basicConfig(
    level=logging.DEBUG,  # Changed from INFO to DEBUG
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION - Update these values for your setup
# ============================================================

# HomeAssistant connection details
HOMEASSISTANT_URL = "ws://homeassistant.local:8123"  # Your HomeAssistant URL
HOMEASSISTANT_TOKEN = "YOUR_LONG_LIVED_ACCESS_TOKEN" # Get from Profile -> Long-Lived Access Tokens

# Entity to track (must have long-term statistics enabled)
# Examples:
#   - "sensor.energy_consumption"
#   - "sensor.house_energy_total"
#   - "sensor.grid_import_total"
ENTITY_ID = "sensor.house_energy_total"

# Timezone
TIMEZONE = pytz.timezone("Europe/Berlin")

# History configuration
# Which days to look back (negative values, e.g., -7 = 7 days ago)
#HISTORY_DAYS = [-1,-2,-3]
HISTORY_DAYS = "-1;-2;-3"

# Weights for each history period (1-10)
# Higher weight = more influence on forecast
#HISTORY_WEIGHTS = [1]  # Most recent week has highest weight
HISTORY_WEIGHTS = "1;1;1"  # Most recent week has highest weight

# Cache TTL in hours
CACHE_TTL_HOURS = 48.0

# Multiplier for forecast adjustment
# Use 1.0 for unchanged forecast
# Use >1.0 to increase forecast (e.g., 1.1 = +10%)
# Use <1.0 to decrease forecast (e.g., 0.9 = -10%)
MULTIPLIER = 1.0

# Number of hours to forecast
FORECAST_HOURS = 24

# ============================================================


def print_table_header():
    """Print formatted table header"""
    print("\n" + "=" * 80)
    print(f"{'Hour':<6} {'DateTime':<20} {'Consumption (Wh)':<18} {'Consumption (kWh)':<18}")
    print("=" * 80)


def print_table_row(hour_offset: int, forecast_time: datetime, consumption_wh: float):
    """Print formatted table row"""
    consumption_kwh = consumption_wh / 1000.0
    time_str = forecast_time.strftime("%Y-%m-%d %H:%M")
    print(f"{hour_offset:<6} {time_str:<20} {consumption_wh:>15.2f}   {consumption_kwh:>15.3f}")


def print_table_footer(forecast_data: dict):
    """Print table footer with statistics"""
    print("=" * 80)

    if not forecast_data:
        print("No forecast data available")
        return

    values = list(forecast_data.values())
    total_wh = sum(values)
    avg_wh = total_wh / len(values)
    min_wh = min(values)
    max_wh = max(values)

    print(f"\nStatistics:")
    print(f"  Total:   {total_wh:>10.2f} Wh  ({total_wh/1000:>8.3f} kWh)")
    print(f"  Average: {avg_wh:>10.2f} Wh  ({avg_wh/1000:>8.3f} kWh)")
    print(f"  Minimum: {min_wh:>10.2f} Wh  ({min_wh/1000:>8.3f} kWh)")
    print(f"  Maximum: {max_wh:>10.2f} Wh  ({max_wh/1000:>8.3f} kWh)")
    print("=" * 80 + "\n")


def main():
    """Main function to test ForecastConsumptionHomeAssistant"""

    print("\n" + "=" * 80)
    print("HomeAssistant Consumption Forecast Test")
    print("=" * 80)

    # Print configuration
    print(f"\nConfiguration:")
    print(f"  HomeAssistant URL: {HOMEASSISTANT_URL}")
    print(f"  Entity ID:         {ENTITY_ID}")
    print(f"  Timezone:          {TIMEZONE}")
    print(f"  History Days:      {HISTORY_DAYS}")
    print(f"  History Weights:   {HISTORY_WEIGHTS}")
    print(f"  Cache TTL:         {CACHE_TTL_HOURS} hours")
    print(f"  Multiplier:        {MULTIPLIER}")
    print(f"  Forecast Hours:    {FORECAST_HOURS}")

    try:
        # Build configuration dict for factory
        config = {
            'type': 'homeassistant-api',
            'base_url': HOMEASSISTANT_URL,
            'apitoken': HOMEASSISTANT_TOKEN,
            'entity_id': ENTITY_ID,
            'history_days': HISTORY_DAYS,
            'history_weights': HISTORY_WEIGHTS,
            'cache_ttl_hours': CACHE_TTL_HOURS,
            'multiplier': MULTIPLIER
        }

        # Initialize the forecaster using factory
        logger.info("Initializing forecaster via factory...")
        forecaster = Consumption.create_consumption(TIMEZONE, config)
        logger.info("Forecaster initialized successfully")

        # Fetch historical data and update cache
        logger.info("Fetching historical data from HomeAssistant...")
        forecaster.refresh_data()
        logger.info("Data refresh completed")

        # Get forecast for next N hours
        logger.info(f"Generating {FORECAST_HOURS}-hour forecast...")
        # The factory defaults to 60-minute resolution, so one slot is one hour
        forecast = forecaster.get_forecast(FORECAST_HOURS)

        if not forecast:
            logger.error("No forecast data received!")
            return 1

        # Display forecast in table format
        print_table_header()

        now = datetime.now(tz=TIMEZONE)
        for hour_offset in range(FORECAST_HOURS):
            if hour_offset in forecast:
                forecast_time = now.replace(minute=0, second=0, microsecond=0)
                forecast_time = forecast_time.replace(hour=(now.hour + hour_offset) % 24)
                if (now.hour + hour_offset) >= 24:
                    from datetime import timedelta
                    days_ahead = (now.hour + hour_offset) // 24
                    forecast_time = forecast_time + timedelta(days=days_ahead)

                consumption_wh = forecast[hour_offset]
                print_table_row(hour_offset, forecast_time, consumption_wh)

        print_table_footer(forecast)

        logger.info("Forecast test completed successfully")
        return 0

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
