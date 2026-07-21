"""
Utility functions for time interval conversions and upsampling.

This module provides functions to convert between different time resolutions
(e.g., hourly or 30-minute to 15-minute intervals) for forecast data.
"""

import logging
from typing import Dict, Literal

logger = logging.getLogger(__name__)


def upsample_forecast(
    hourly_forecast: Dict[int, float],
    target_resolution: int = 15,
    method: Literal['linear', 'constant'] = 'linear',
    source_resolution: int = 60
) -> Dict[int, float]:
    """
    Convert forecast to finer resolution intervals.

    Args:
        hourly_forecast: Dictionary mapping source interval index to energy value (Wh)
        target_resolution: Target resolution in minutes (currently only 15 supported)
        method: Upsampling method:
            - 'linear': Linear interpolation of power (recommended for solar)
            - 'constant': Equal distribution (recommended for prices/consumption)
        source_resolution: Source resolution in minutes (60 or 30)

    Returns:
        Dictionary mapping interval index to energy value (Wh per interval)

    Note:
        - Input values are Wh per source interval (energy)
        - Output values are Wh per target interval (energy)
        - Linear method interpolates power, then converts back to energy
        - Constant method divides energy equally across intervals
    """
    if target_resolution != 15:
        raise ValueError(
            f"Only 15-minute resolution is currently supported, got {target_resolution}")

    if source_resolution not in (30, 60):
        raise ValueError(
            f"Only 30 or 60 minute source resolution is supported, got {source_resolution}")

    if not hourly_forecast:
        logger.warning("Empty hourly_forecast provided to upsample_forecast")
        return {}

    if method == 'linear':
        return _upsample_linear(hourly_forecast, source_resolution)
    if method == 'constant':
        return _upsample_constant(hourly_forecast, source_resolution)
    raise ValueError(f"Unknown upsampling method: {method}")


def _upsample_linear(
    hourly_forecast: Dict[int, float],
    source_resolution: int = 60
) -> Dict[int, float]:
    """
    Convert Wh forecast to 15-minute intervals with linear interpolation.

    Important:
    - Input is Wh per source interval (energy values, 60 or 30 minutes)
    - Output is Wh per 15 minutes
    - Uses linear power interpolation, then converts to energy

    Method:
    1. Calculate average power per source interval (Wh -> W)
    2. Interpolate power linearly between source intervals
    3. Convert interpolated power back to energy (W -> Wh for 15 min)

    Example (60-minute source):
        Hour 0: 1000 Wh -> avg power = 1000 W
        Hour 1: 2000 Wh -> avg power = 2000 W

        15-min intervals (linear power ramp):
        [0]: Power = 1000 W -> Energy = 1000 * 0.25 = 250 Wh
        [1]: Power = 1250 W -> Energy = 1250 * 0.25 = 312.5 Wh
        [2]: Power = 1500 W -> Energy = 1500 * 0.25 = 375 Wh
        [3]: Power = 1750 W -> Energy = 1750 * 0.25 = 437.5 Wh
        [4]: Power = 2000 W -> Energy = 2000 * 0.25 = 500 Wh (next hour begins)

    Example (30-minute source):
        Bucket 0: 500 Wh -> avg power = 1000 W
        Bucket 1: 1000 Wh -> avg power = 2000 W

        15-min intervals (linear power ramp, no plain doubling):
        [0]: Power = 1000 W -> Energy = 1000 * 0.25 = 250 Wh
        [1]: Power = 1500 W -> Energy = 1500 * 0.25 = 375 Wh
        [2]: Power = 2000 W -> Energy = 2000 * 0.25 = 500 Wh (next bucket begins)
    """
    forecast_15min = {}
    max_idx = max(hourly_forecast.keys())

    # Number of 15-min intervals per source interval (60 -> 4, 30 -> 2)
    steps = source_resolution // 15
    # Wh per source interval -> average power in W (60 -> x1, 30 -> x2)
    power_factor = 60 / source_resolution

    for idx in range(max_idx):
        current_wh = hourly_forecast.get(idx, 0)
        next_wh = hourly_forecast.get(idx + 1, 0)

        # Convert Wh to average W (power)
        current_power = current_wh * power_factor
        next_power = next_wh * power_factor

        # Linear power interpolation across the sub-intervals
        for step in range(steps):
            interval_idx = idx * steps + step
            fraction = step / steps

            # Interpolate power linearly
            interpolated_power = current_power + (next_power - current_power) * fraction

            # Convert power to energy for 15 minutes: P[W] * 0.25[h] = E[Wh]
            forecast_15min[interval_idx] = interpolated_power * 0.25

    # Handle the last source interval (no interpolation, constant power)
    if max_idx in hourly_forecast:
        last_power = hourly_forecast[max_idx] * power_factor
        for step in range(steps):
            interval_idx = max_idx * steps + step
            forecast_15min[interval_idx] = last_power * 0.25

    return forecast_15min


def _upsample_constant(
    hourly_forecast: Dict[int, float],
    source_resolution: int = 60
) -> Dict[int, float]:
    """
    Convert forecast to 15-minute intervals with constant distribution.

    Simply divides each source value by the number of 15-minute steps it
    contains (4 for hourly, 2 for 30-minute data). This is appropriate for
    prices or consumption where interpolation doesn't make physical sense.

    Example (60-minute source):
        Hour 0: 1000 Wh -> 250, 250, 250, 250 Wh per 15 min
        Hour 1: 2000 Wh -> 500, 500, 500, 500 Wh per 15 min
    """
    forecast_15min = {}

    steps = source_resolution // 15

    for idx, value in hourly_forecast.items():
        # Distribute equally across the sub-intervals
        step_value = value / steps
        for step in range(steps):
            interval_idx = idx * steps + step
            forecast_15min[interval_idx] = step_value

    return forecast_15min


def downsample_to_hourly(
    data_15min: Dict[int, float],
    source_resolution: int = 15
) -> Dict[int, float]:
    """
    Convert 15- or 30-minute intervals to hourly by summing.

    Args:
        data_15min: Dictionary mapping source interval index to energy value (Wh)
        source_resolution: Source resolution in minutes (15 or 30)

    Returns:
        Dictionary mapping hour index to energy value (Wh per hour)

    Example:
        15-min intervals: {0: 250, 1: 300, 2: 350, 3: 400, 4: 450, ...}
        Hourly: {0: 1300, 1: ..., ...}
                (250 + 300 + 350 + 400 = 1300 Wh for hour 0)

        30-min intervals: {0: 500, 1: 700, 2: 300, 3: 100}
        Hourly: {0: 1200, 1: 400}
    """
    if source_resolution not in (15, 30):
        raise ValueError(
            f"Only 15 or 30 minute source resolution is supported, got {source_resolution}")

    per_hour = 60 // source_resolution

    hourly = {}
    for interval_idx, value in data_15min.items():
        hour = interval_idx // per_hour
        if hour not in hourly:
            hourly[hour] = 0
        hourly[hour] += value
    return hourly


def average_to_hourly(data_15min: Dict[int, float]) -> Dict[int, float]:
    """
    Convert 15-minute intervals to hourly by averaging (for prices).

    Args:
        data_15min: Dictionary mapping 15-min interval index to price value

    Returns:
        Dictionary mapping hour index to average price

    Example:
        15-min prices: {0: 10, 1: 12, 2: 14, 3: 16, 4: 18, ...}
        Hourly avg: {0: 13, 1: ..., ...}
                   ((10 + 12 + 14 + 16) / 4 = 13 for hour 0)
    """
    hourly = {}
    temp_sums = {}
    temp_counts = {}

    for interval_15, value in data_15min.items():
        hour = interval_15 // 4
        if hour not in temp_sums:
            temp_sums[hour] = 0
            temp_counts[hour] = 0
        temp_sums[hour] += value
        temp_counts[hour] += 1

    for hour, sum_value in temp_sums.items():
        hourly[hour] = sum_value / temp_counts[hour]

    return hourly
