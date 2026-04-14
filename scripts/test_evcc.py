#!/usr/bin/env python3
"""
Standalone test script for the EVCC dynamic tariff module.

This script allows you to test the EVCC API integration by providing a URL
and seeing the fetched dynamic pricing data.

Usage:
    python scripts/test_evcc.py <url>

Example:
    python scripts/test_evcc.py http://evcc.lo
    python scripts/test_evcc.py https://your-evcc-instance.com/api/tariff/planner

Requirements:
    - The script should be run from the project root directory
    - The virtual environment should be activated or use the full Python path
"""
import sys
import json
import pytz
from pathlib import Path

# Add the src directory to the Python path to import the modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from batcontrol.dynamictariff.evcc import Evcc


def main():
    """Test the EVCC dynamic tariff functionality"""
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_evcc.py <url>")
        print("Example: python scripts/test_evcc.py http://evcc.lan")
        print("Example: python scripts/test_evcc.py https://your-evcc-instance.com/api/tariff/planner")
        sys.exit(1)

    url = sys.argv[1]
    print(f"Testing EVCC API at: {url}")
    print("=" * 50)
    
    try:
        # Initialize EVCC with Europe/Berlin timezone
        print("Initializing EVCC client...")
        evcc = Evcc(pytz.timezone('Europe/Berlin'), url)
        
        # Fetch raw data first
        print("Fetching raw data from API...")
        raw_data = evcc.get_raw_data_from_provider()
        print(f"Raw data structure: {type(raw_data)}")
        if isinstance(raw_data, dict):
            print(f"Raw data keys: {list(raw_data.keys())}")
        
        # Process and fetch prices
        print("\nProcessing prices...")
        prices = evcc.get_prices()
        
        # Display results
        print("\n" + "=" * 50)
        print("PROCESSED PRICES:")
        print("=" * 50)
        if prices:
            for hour, price in sorted(prices.items()):
                print(f"Hour +{hour:2d}: {price:.4f} EUR/kWh")
        else:
            print("No prices found")
            
        print("\n" + "=" * 50)
        print("RAW JSON DATA:")
        print("=" * 50)
        print(json.dumps(raw_data, indent=2))
        
    except Exception as e:
        print(f"\nError occurred: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print("\nFull traceback:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
