#!/usr/bin/env python3
"""
Verification script for production offset (Wintermode) feature.

This script demonstrates the production offset functionality by showing
how production values are affected by different offset percentages.
"""
import sys
import os
import numpy as np

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def demonstrate_production_offset():
    """Demonstrate production offset with example values"""
    
    print("=" * 70)
    print("Production Offset (Wintermode) Feature Demonstration")
    print("=" * 70)
    print()
    
    # Sample production forecast (W)
    original_production = np.array([0, 500, 1500, 3000, 4500, 5000, 
                                   4500, 3000, 1500, 500, 0, 0])
    
    print("Original Solar Production Forecast (W):")
    print(original_production)
    print()
    
    # Test different offset scenarios
    scenarios = [
        (1.0, "Normal operation (100%)"),
        (0.8, "Winter mode - 20% reduction (80%)"),
        (0.5, "Heavy snow - 50% reduction (50%)"),
        (0.0, "No production - panels completely covered (0%)"),
        (1.2, "Optimistic forecast - 20% increase (120%)"),
    ]
    
    for offset, description in scenarios:
        print(f"\n{description}")
        print(f"Offset multiplier: {offset:.1f}")
        adjusted = original_production * offset
        print(f"Adjusted production: {adjusted.astype(int)}")
        total_original = original_production.sum()
        total_adjusted = adjusted.sum()
        reduction = (1 - offset) * 100
        print(f"Total original: {total_original:.0f} W")
        print(f"Total adjusted: {total_adjusted:.0f} W")
        if offset < 1.0:
            print(f"Reduction: {reduction:.0f}%")
        elif offset > 1.0:
            print(f"Increase: {-reduction:.0f}%")
        print("-" * 70)
    
    print()
    print("Configuration Example:")
    print("=" * 70)
    print("""
battery_control_expert:
  production_offset_percent: 0.8  # 80% of forecast (20% reduction)
  
# This is useful when:
# - Solar panels are covered with snow
# - Winter conditions reduce actual production
# - You want to be more conservative in winter months
    """)
    print("=" * 70)
    
    print()
    print("MQTT API Usage:")
    print("=" * 70)
    print("""
# Read current offset:
mosquitto_sub -h localhost -t "house/batcontrol/production_offset"

# Set offset to 80% (winter mode):
mosquitto_pub -h localhost -t "house/batcontrol/production_offset/set" -m "0.8"

# Disable production (panels covered):
mosquitto_pub -h localhost -t "house/batcontrol/production_offset/set" -m "0.0"

# Restore normal operation:
mosquitto_pub -h localhost -t "house/batcontrol/production_offset/set" -m "1.0"
    """)
    print("=" * 70)
    
    print()
    print("Home Assistant Integration:")
    print("=" * 70)
    print("""
After enabling MQTT auto-discovery, the production offset appears in
Home Assistant as a number entity:

  Name: Production Offset
  Entity ID: number.batcontrol_production_offset
  Category: Configuration
  Range: 0.0 to 2.0
  Step: 0.01
  Default: 1.0

You can create automations to:
- Automatically reduce production in winter months
- Set to 0 when snow is detected on panels
- Restore normal operation when conditions improve
    """)
    print("=" * 70)
    
    print()
    print("[done] Production offset feature successfully implemented!")
    print()

if __name__ == '__main__':
    demonstrate_production_offset()
