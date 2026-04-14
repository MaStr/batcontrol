#!/usr/bin/env python3
"""Day simulation for NextLogic peak shaving.

Simulates a summer day with a realistic PV production bell-curve.
Runs through each hour slot, calls the peak shaving logic with the
current battery state and remaining forecast, then applies the
resulting charge rate limit to evolve battery SOC.

Two modes are compared side-by-side:
  - Baseline : no peak shaving (battery charges as fast as PV allows)
  - Peak Shaving (time mode, target = end of production ~19:00)

This script answers the question:
  "What happens when the target hour is set to the end of production?"

Usage:
    python scripts/simulate_peak_shaving_day.py
"""
import sys
import os
import datetime

import numpy as np

# Allow running from project root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from batcontrol.logic.next import NextLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
    InverterControlSettings,
)
from batcontrol.logic.common import CommonLogic

# ---------------------------------------------------------------------------
# Simulation parameters
# ---------------------------------------------------------------------------
INTERVAL_MIN   = 60          # 1-hour slots
INTERVAL_H     = INTERVAL_MIN / 60.0

MAX_CAPACITY   = 10_000      # Wh  (10 kWh battery)
MIN_SOC_WH     = 500         # Wh  (~5 % reserve kept for self consumption)
MAX_SOC_WH     = MAX_CAPACITY  # charge to 100 %

CONSUMPTION_W  = 400         # W   constant house consumption

# Target hour: 17 
# Adjust this value to explore behaviour with an earlier target (e.g. 14).
TARGET_HOUR    = 17

PRICE_LIMIT    = 0.05        # EUR/kWh - used only for combined/price modes
FLAT_PRICE     = 0.30        # EUR/kWh - flat tariff for the whole day

# Initial battery state (morning, mostly empty)
INITIAL_SOC_WH = 1_500       # Wh

# ---------------------------------------------------------------------------
# 24-hour PV production profile (W) - typical German summer day
# Entries are average W for each hour 0..23.
# Production ends near 0 at hour 19 (~10 W), full darkness after that.
# ---------------------------------------------------------------------------
PRODUCTION_PROFILE_W = np.array([
      0,    0,    0,    0,    0,    0,   # 00-05  night
    120,  600, 1600, 3100, 4600, 5900,   # 06-11  morning / ramp up
   6600, 6100, 5100, 3600, 2100,  900,   # 12-17  midday / ramp down
    200,   10,    0,    0,    0,    0,   # 18-23  dusk / night
], dtype=float)

assert len(PRODUCTION_PROFILE_W) == 24, "Profile must have 24 entries"

# ---------------------------------------------------------------------------
# Production end: first hour where production == 0 *after* sunrise
# (production window for price-based check mirrors the code fix)
# ---------------------------------------------------------------------------
def find_production_end(production_w: np.ndarray) -> int:
    """Return the index of the first zero-production slot after sun has risen."""
    sunrise_found = False
    for i, p in enumerate(production_w):
        if p > 0:
            sunrise_found = True
        if sunrise_found and p == 0:
            return i
    return len(production_w)


PRODUCTION_END_SLOT = find_production_end(PRODUCTION_PROFILE_W)
print(f"Production window: slots 0..{PRODUCTION_END_SLOT - 1}  "
      f"(ends at {PRODUCTION_END_SLOT:02d}:00)")
print(f"Target hour      : {TARGET_HOUR:02d}:00")
print()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_calc_input(current_hour: int, stored_wh: float) -> CalculationInput:
    """Build a CalculationInput for the given hour.

    The arrays are *shifted* so that index 0 = current hour, mirroring what
    the real batcontrol system does when it passes the hour-ahead forecast.
    """
    remaining = 24 - current_hour
    production  = PRODUCTION_PROFILE_W[current_hour:].copy()
    consumption = np.full(remaining, CONSUMPTION_W, dtype=float)
    prices      = np.full(remaining, FLAT_PRICE,    dtype=float)
    free_cap    = float(MAX_SOC_WH - stored_wh)
    usable      = float(max(stored_wh - MIN_SOC_WH, 0))
    return CalculationInput(
        production=production,
        consumption=consumption,
        prices=prices,
        stored_energy=float(stored_wh),
        stored_usable_energy=usable,
        free_capacity=free_cap,
    )


def apply_one_hour(production_w: float, consumption_w: float,
                   charge_limit_w: int, stored_wh: float) -> tuple:
    """Simulate one hour of battery operation.

    Returns:
        actual_charge_w   : W actually charged into battery this hour
        actual_feed_in_w  : W fed into grid this hour (excess after limit)
        new_stored_wh     : updated battery energy in Wh
    """
    net_surplus_w = production_w - consumption_w  # positive = PV excess

    if net_surplus_w <= 0:
        # Consuming from battery or grid – peak shaving does not apply
        actual_charge_w = 0.0
        actual_feed_in_w = 0.0
        discharge_w = min(-net_surplus_w, stored_wh / INTERVAL_H)
        new_stored_wh = stored_wh - discharge_w * INTERVAL_H
    else:
        # PV surplus available
        if charge_limit_w == 0:
            # Charging blocked entirely
            actual_charge_w = 0.0
            actual_feed_in_w = net_surplus_w
        elif charge_limit_w > 0:
            # Limit applied
            actual_charge_w = min(net_surplus_w, float(charge_limit_w))
            actual_feed_in_w = net_surplus_w - actual_charge_w
        else:
            # No limit (-1): charge as fast as PV allows
            actual_charge_w = net_surplus_w
            actual_feed_in_w = 0.0

        # Clamp to remaining free capacity
        free_cap_wh = MAX_SOC_WH - stored_wh
        max_charge_this_hour_wh = free_cap_wh
        actual_charge_wh = min(actual_charge_w * INTERVAL_H, max_charge_this_hour_wh)
        actual_charge_w  = actual_charge_wh / INTERVAL_H
        actual_feed_in_w = net_surplus_w - actual_charge_w
        new_stored_wh    = stored_wh + actual_charge_wh

    new_stored_wh = max(min(new_stored_wh, MAX_SOC_WH), 0.0)
    return actual_charge_w, actual_feed_in_w, new_stored_wh


# ---------------------------------------------------------------------------
# Setup logic instances
# ---------------------------------------------------------------------------
# Reset singleton for clean setup
CommonLogic._instance = None
common = CommonLogic.get_instance(
    charge_rate_multiplier=1.1,
    always_allow_discharge_limit=0.90,
    max_capacity=MAX_CAPACITY,
)

TZ = datetime.timezone.utc
BASE_DATE = datetime.datetime(2026, 6, 21, 0, 0, 0, tzinfo=TZ)  # summer solstice

# Shared parameters for the shaving scenario
params_shaving = CalculationParameters(
    max_charging_from_grid_limit=0.79,
    min_price_difference=0.05,
    min_price_difference_rel=0.2,
    max_capacity=MAX_CAPACITY,
    peak_shaving_enabled=True,
    peak_shaving_allow_full_after=TARGET_HOUR,
    peak_shaving_mode='time',   # pure time-based ramp
)

# ---------------------------------------------------------------------------
# Run simulations
# ---------------------------------------------------------------------------
ROW_FMT = (
    "{hour:02d}:00  "
    "PV:{prod:>5.0f}W  "
    "SOC_base:{soc_base:>6.0f}Wh({soc_base_pct:>3.0f}%)  "
    "SOC_shav:{soc_shav:>6.0f}Wh({soc_shav_pct:>3.0f}%)  "
    "limit:{limit:>6}W  "
    "charge:{chg:>5.0f}W  "
    "feed-in:{fi:>5.0f}W"
)

print("=" * 100)
print(f"  Day simulation  |  Battery {MAX_CAPACITY/1000:.0f} kWh  |  "
      f"Consumption {CONSUMPTION_W} W  |  Target SOC=100% at {TARGET_HOUR:02d}:00")
print("=" * 100)
print(
    "Hour   Production   SOC-base            SOC-shaving         "
    "PeakShav-Limit  PeakShav-Charge  PeakShav-FeedIn"
)
print("-" * 100)

# Start both at the same SOC
soc_base = INITIAL_SOC_WH
soc_shav = INITIAL_SOC_WH

total_feed_in_base = 0.0
total_feed_in_shav = 0.0
total_charged_base = 0.0
total_charged_shav = 0.0

# Build a fresh logic per iteration (it carries state via CommonLogic singleton)
logic_shav = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
logic_shav.set_calculation_parameters(params_shaving)

for hour in range(24):
    ts = BASE_DATE.replace(hour=hour)
    prod_w = float(PRODUCTION_PROFILE_W[hour])
    cons_w = float(CONSUMPTION_W)

    # ---------- Baseline: no limiting ----------
    _, fi_base, soc_base_new = apply_one_hour(prod_w, cons_w, -1, soc_base)
    chg_base = (soc_base_new - soc_base) / INTERVAL_H if soc_base_new > soc_base else 0.0

    # ---------- Peak shaving scenario ----------
    calc_input = build_calc_input(hour, soc_shav)

    # Compute charge limit (time-based ramp).
    # Skip peak shaving guard conditions by calling the private method directly
    # (mirrors what _apply_peak_shaving does internally).
    if prod_w > 0 and hour < TARGET_HOUR:
        raw_limit = logic_shav._calculate_peak_shaving_charge_limit(calc_input, ts)
        if raw_limit >= 0:
            limit_w = common.enforce_min_pv_charge_rate(raw_limit)
        else:
            limit_w = -1  # no limiting needed
    else:
        limit_w = -1   # outside active window (night or past target)

    chg_shav, fi_shav, soc_shav_new = apply_one_hour(prod_w, cons_w, limit_w, soc_shav)

    # Accumulate totals
    total_feed_in_base += fi_base * INTERVAL_H / 1000   # kWh
    total_feed_in_shav += fi_shav * INTERVAL_H / 1000
    total_charged_base += chg_base * INTERVAL_H / 1000
    total_charged_shav += chg_shav * INTERVAL_H / 1000

    limit_str = f"{limit_w:>5}" if limit_w >= 0 else "  N/A"

    print(ROW_FMT.format(
        hour=hour,
        prod=prod_w,
        soc_base=soc_base,
        soc_base_pct=soc_base / MAX_CAPACITY * 100,
        soc_shav=soc_shav,
        soc_shav_pct=soc_shav / MAX_CAPACITY * 100,
        limit=limit_str,
        chg=chg_shav,
        fi=fi_shav,
    ))

    soc_base = soc_base_new
    soc_shav = soc_shav_new

print("-" * 100)
print(f"  End of day  SOC-base: {soc_base:.0f} Wh ({soc_base/MAX_CAPACITY*100:.0f}%)  "
      f"SOC-shav: {soc_shav:.0f} Wh ({soc_shav/MAX_CAPACITY*100:.0f}%)")
print()
print(f"  Total charged (base):    {total_charged_base:.2f} kWh")
print(f"  Total charged (shaving): {total_charged_shav:.2f} kWh")
print(f"  Total feed-in (base):    {total_feed_in_base:.2f} kWh")
print(f"  Total feed-in (shaving): {total_feed_in_shav:.2f} kWh")
print("=" * 100)
print()

# ---------------------------------------------------------------------------
# Additional: show the ramp profile for the first slot with surplus
# ---------------------------------------------------------------------------
print("Counter-linear ramp profile from sunrise to target hour")
print(f"(free_capacity at start: {MAX_SOC_WH - INITIAL_SOC_WH:.0f} Wh)")
print()
print(f"{'Slot':>5}  {'Time':>6}  {'PV (W)':>8}  {'n remain':>9}  {'raw limit (W)':>14}  {'applied (W)':>12}")
print("-" * 65)

logic_ramp = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
logic_ramp.set_calculation_parameters(params_shaving)
ramp_soc = INITIAL_SOC_WH

for hour in range(24):
    ts = BASE_DATE.replace(hour=hour)
    prod_w = float(PRODUCTION_PROFILE_W[hour])
    if prod_w <= 0 and hour > 12:
        break  # past production window

    calc_input = build_calc_input(hour, ramp_soc)
    n_remain = max(TARGET_HOUR - hour, 0)
    if prod_w > 0 and hour < TARGET_HOUR:
        raw = logic_ramp._calculate_peak_shaving_charge_limit(calc_input, ts)
        applied = common.enforce_min_pv_charge_rate(raw) if raw >= 0 else -1
    else:
        raw = -1
        applied = -1

    raw_s     = f"{raw:>14}" if raw >= 0 else "            N/A"
    applied_s = f"{applied:>12}" if applied >= 0 else "          N/A"

    print(f"{hour:>5}  {hour:02d}:00  {prod_w:>8.0f}  {n_remain:>9}  {raw_s}  {applied_s}")

    # Evolve SOC for ramp display (unthrottled so free capacity shrinks)
    _, _, ramp_soc = apply_one_hour(prod_w, float(CONSUMPTION_W), -1, ramp_soc)

print()
print("Note: 'N/A' = peak shaving inactive for this slot (night, past target, or no limit needed).")
print("      MIN_CHARGE_RATE = 500 W is applied when raw limit > 0 but below minimum.")

# ---------------------------------------------------------------------------
# Comparison: target 14 vs target end-of-production
# ---------------------------------------------------------------------------
def run_scenario(target_hour: int, initial_soc_wh: float) -> dict:
    """Run a full day simulation and return key metrics."""
    CommonLogic._instance = None
    common_local = CommonLogic.get_instance(
        charge_rate_multiplier=1.1,
        always_allow_discharge_limit=0.90,
        max_capacity=MAX_CAPACITY,
    )
    params = CalculationParameters(
        max_charging_from_grid_limit=0.79,
        min_price_difference=0.05,
        min_price_difference_rel=0.2,
        max_capacity=MAX_CAPACITY,
        peak_shaving_enabled=True,
        peak_shaving_allow_full_after=target_hour,
        peak_shaving_mode='time',
    )
    logic = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
    logic.set_calculation_parameters(params)

    soc = float(initial_soc_wh)
    total_charged = 0.0
    total_feed_in = 0.0
    soc_at_target = None
    first_full_hour = None

    for hour in range(24):
        ts_local = BASE_DATE.replace(hour=hour)
        prod_w = float(PRODUCTION_PROFILE_W[hour])
        calc_input = build_calc_input(hour, soc)

        if prod_w > 0 and hour < target_hour:
            raw = logic._calculate_peak_shaving_charge_limit(calc_input, ts_local)
            lim = common_local.enforce_min_pv_charge_rate(raw) if raw >= 0 else -1
        else:
            lim = -1

        chg_w, fi_w, soc_new = apply_one_hour(prod_w, float(CONSUMPTION_W), lim, soc)
        total_charged += chg_w * INTERVAL_H / 1000
        total_feed_in  += fi_w  * INTERVAL_H / 1000

        if hour == target_hour:
            soc_at_target = soc  # SOC at the START of target hour

        if first_full_hour is None and soc_new >= MAX_SOC_WH:
            first_full_hour = hour

        soc = soc_new

    return {
        'target_hour' : target_hour,
        'soc_at_target': soc_at_target,
        'soc_end_of_day': soc,
        'first_full_hour': first_full_hour,
        'total_charged_kwh': total_charged,
        'total_feed_in_kwh': total_feed_in,
    }


# Reset the singleton before comparison runs
print()
print("=" * 70)
print("  Scenario comparison  (initial SOC: "
      f"{INITIAL_SOC_WH/MAX_CAPACITY*100:.0f}%)")
print("=" * 70)
print(f"  {'Target':>14}  {'SOC@target':>12}  {'First 100%':>12}  "
      f"{'Charged':>9}  {'Feed-in':>9}")
print("-" * 70)

for t in (14, TARGET_HOUR):
    r = run_scenario(t, INITIAL_SOC_WH)
    soc_t   = r['soc_at_target']
    full_h  = r['first_full_hour']
    full_s  = f"{full_h:02d}:00" if full_h is not None else "never"
    soc_pct = f"{soc_t/MAX_CAPACITY*100:.0f}%" if soc_t is not None else "-"
    print(f"  {t:02d}:00{' (end of prod)' if t == TARGET_HOUR else '              ':14}  "
          f"{soc_pct:>12}  {full_s:>12}  "
          f"{r['total_charged_kwh']:>8.2f}k  "
          f"{r['total_feed_in_kwh']:>8.2f}k")

print("=" * 70)
print()
