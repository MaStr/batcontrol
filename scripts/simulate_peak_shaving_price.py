#!/usr/bin/env python3
"""Day simulation for NextLogic price-based peak shaving.

Simulates a summer day using the **price-based** algorithm only (mode='price').

Scenario:
  - Realistic PV production bell-curve (same as simulate_peak_shaving_day.py)
  - Price curve that dips below PRICE_LIMIT during hours 12-13 (solar surplus
    pushes spot prices negative / very low at midday)
  - Battery starts at ~15% SOC
  - Two traces compared:
      Baseline  : no peak shaving
      PriceShav : mode='price', price_limit applied

The question answered:
  "With a cheap window at 12-13, does the price-based algo reserve capacity
   before the window so the battery can fully absorb cheap-slot PV surplus?"

Usage:
    python scripts/simulate_peak_shaving_price.py
"""
import sys
import os
import datetime

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from batcontrol.logic.next import NextLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
)
from batcontrol.logic.common import CommonLogic

# ---------------------------------------------------------------------------
# Simulation parameters
# ---------------------------------------------------------------------------
INTERVAL_MIN  = 60
INTERVAL_H    = INTERVAL_MIN / 60.0

MAX_CAPACITY  = 10_000   # Wh  (10 kWh battery)
MIN_SOC_WH    = 500      # Wh  (~5 % floor)
MAX_SOC_WH    = MAX_CAPACITY

CONSUMPTION_W = 400      # W   constant house load

# Price threshold: slots at or below this are the "cheap window"
PRICE_LIMIT   = 0.05     # EUR/kWh

# allow_full_battery_after is required by CalculationParameters even if we
# only use mode='price'.  Set it past the production window so it never
# interferes with our price-based scenario.
ALLOW_FULL_AFTER = 23

INITIAL_SOC_WH = 1_500   # Wh  start at ~15%

# ---------------------------------------------------------------------------
# 24-hour PV production profile (W)
# ---------------------------------------------------------------------------
PRODUCTION_PROFILE_W = np.array([
      0,    0,    0,    0,    0,    0,   # 00-05
    120,  600, 1600, 3100, 4600, 5900,   # 06-11
   6600, 6100, 5100, 3600, 2100,  900,   # 12-17
    200,   10,    0,    0,    0,    0,   # 18-23
], dtype=float)

# ---------------------------------------------------------------------------
# 24-hour price profile (EUR/kWh)
# Normal day-ahead price with a midday dip caused by solar oversupply.
# Hours 12 and 13 are below PRICE_LIMIT (cheap window).
# ---------------------------------------------------------------------------
PRICE_PROFILE = np.array([
    0.28, 0.26, 0.25, 0.24, 0.25, 0.27,   # 00-05  night (low demand)
    0.29, 0.31, 0.33, 0.34, 0.30, 0.18,   # 06-11  morning ramp, starts dipping
    0.03, 0.04, 0.12, 0.22, 0.32, 0.38,   # 12-17  cheap dip at 12-13, evening rise
    0.35, 0.31, 0.29, 0.28, 0.27, 0.27,   # 18-23  evening/night
], dtype=float)

assert len(PRODUCTION_PROFILE_W) == 24
assert len(PRICE_PROFILE) == 24

CHEAP_HOURS = [h for h, p in enumerate(PRICE_PROFILE) if p <= PRICE_LIMIT]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_calc_input(current_hour: int, stored_wh: float) -> CalculationInput:
    """Build a CalculationInput starting at current_hour (slot 0 = now)."""
    remaining   = 24 - current_hour
    production  = PRODUCTION_PROFILE_W[current_hour:].copy()
    consumption = np.full(remaining, CONSUMPTION_W, dtype=float)
    prices      = PRICE_PROFILE[current_hour:].copy()
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
    """Advance battery by one hour.

    Returns (actual_charge_w, actual_feed_in_w, new_stored_wh).
    """
    net_surplus_w = production_w - consumption_w

    if net_surplus_w <= 0:
        discharge_w   = min(-net_surplus_w, stored_wh / INTERVAL_H)
        new_stored_wh = stored_wh - discharge_w * INTERVAL_H
        return 0.0, 0.0, max(new_stored_wh, 0.0)

    # PV surplus available
    if charge_limit_w == 0:
        actual_charge_w = 0.0
    elif charge_limit_w > 0:
        actual_charge_w = min(net_surplus_w, float(charge_limit_w))
    else:                          # -1  no limit
        actual_charge_w = net_surplus_w

    # Clamp to remaining free capacity
    max_charge_wh   = MAX_SOC_WH - stored_wh
    actual_charge_wh = min(actual_charge_w * INTERVAL_H, max_charge_wh)
    actual_charge_w  = actual_charge_wh / INTERVAL_H
    actual_feed_in_w = net_surplus_w - actual_charge_w
    new_stored_wh    = min(stored_wh + actual_charge_wh, MAX_SOC_WH)

    return actual_charge_w, actual_feed_in_w, new_stored_wh


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
CommonLogic._instance = None
common = CommonLogic.get_instance(
    charge_rate_multiplier=1.1,
    always_allow_discharge_limit=0.90,
    max_capacity=MAX_CAPACITY,
)

TZ        = datetime.timezone.utc
BASE_DATE = datetime.datetime(2026, 6, 21, 0, 0, 0, tzinfo=TZ)

params_price = CalculationParameters(
    max_charging_from_grid_limit=0.79,
    min_price_difference=0.05,
    min_price_difference_rel=0.2,
    max_capacity=MAX_CAPACITY,
    peak_shaving_enabled=True,
    peak_shaving_allow_full_after=ALLOW_FULL_AFTER,
    peak_shaving_mode='price',
    peak_shaving_price_limit=PRICE_LIMIT,
)

# ---------------------------------------------------------------------------
# Print header info
# ---------------------------------------------------------------------------
print("=" * 105)
print(f"  Price-based peak shaving simulation  |  Battery {MAX_CAPACITY/1000:.0f} kWh  |  "
      f"Consumption {CONSUMPTION_W} W  |  Price limit {PRICE_LIMIT} EUR/kWh")
print(f"  Cheap window hours: {CHEAP_HOURS}  (price <= {PRICE_LIMIT})")
print("=" * 105)
print()

# ---------------------------------------------------------------------------
# Print price and production overview
# ---------------------------------------------------------------------------
print("Hour-by-hour forecast:")
print(f"  {'Hour':>5}  {'Price €/kWh':>11}  {'PV (W)':>8}  {'Surplus (W)':>11}  {'<= limit':>8}")
print("  " + "-" * 52)
for h in range(24):
    price    = PRICE_PROFILE[h]
    prod     = PRODUCTION_PROFILE_W[h]
    surplus  = max(prod - CONSUMPTION_W, 0)
    cheap_mk = "  CHEAP <--" if price <= PRICE_LIMIT else ""
    print(f"  {h:02d}:00  {price:>11.3f}  {prod:>8.0f}  {surplus:>11.0f}{cheap_mk}")
print()

# ---------------------------------------------------------------------------
# Main simulation loop
# ---------------------------------------------------------------------------
logic_shav  = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
logic_shav.set_calculation_parameters(params_price)

soc_base = float(INITIAL_SOC_WH)
soc_shav = float(INITIAL_SOC_WH)

total_feed_in_base = 0.0
total_feed_in_shav = 0.0
total_charged_base = 0.0
total_charged_shav = 0.0
wasted_cheap_base  = 0.0   # feed-in during cheap hours (wasted cheap PV)
wasted_cheap_shav  = 0.0

ROW_FMT = (
    "{hour:02d}:00  {price:>6.3f} EUR  "
    "SOC_base:{soc_base:>6.0f}Wh({soc_base_pct:>3.0f}%)  "
    "SOC_shav:{soc_shav:>6.0f}Wh({soc_shav_pct:>3.0f}%)  "
    "limit:{limit:>6}W  "
    "chg:{chg:>5.0f}W  "
    "fi:{fi:>5.0f}W"
    "{cheap_marker}"
)

print(f"  {'Hour':>5}  {'Price':>7}  "
      f"{'SOC-base':>20}  "
      f"{'SOC-shaving':>20}  "
      f"{'PS-Limit':>10}  "
      f"{'Charge':>9}  "
      f"{'Feed-in':>9}")
print("  " + "-" * 97)

for hour in range(24):
    ts       = BASE_DATE.replace(hour=hour)
    prod_w   = float(PRODUCTION_PROFILE_W[hour])
    price    = float(PRICE_PROFILE[hour])
    is_cheap = price <= PRICE_LIMIT

    # ---- Baseline: no limiting ----
    _, fi_base, soc_base_new = apply_one_hour(prod_w, CONSUMPTION_W, -1, soc_base)
    chg_base = (soc_base_new - soc_base) / INTERVAL_H

    # ---- Price-based shaving ----
    calc_input = build_calc_input(hour, soc_shav)

    # Only engage peak shaving when there is PV production
    if prod_w > 0:
        raw_limit = logic_shav._calculate_peak_shaving_charge_limit_price_based(
            calc_input)
        if raw_limit >= 0:
            limit_w = common.enforce_min_pv_charge_rate(raw_limit)
        else:
            limit_w = -1
    else:
        limit_w = -1

    chg_shav, fi_shav, soc_shav_new = apply_one_hour(prod_w, CONSUMPTION_W,
                                                       limit_w, soc_shav)

    # Accumulate
    total_charged_base += chg_base * INTERVAL_H / 1000
    total_charged_shav += chg_shav * INTERVAL_H / 1000
    total_feed_in_base += fi_base  * INTERVAL_H / 1000
    total_feed_in_shav += fi_shav  * INTERVAL_H / 1000
    if is_cheap:
        wasted_cheap_base += fi_base * INTERVAL_H / 1000
        wasted_cheap_shav += fi_shav * INTERVAL_H / 1000

    limit_str    = f"{limit_w:>5}" if limit_w >= 0 else "  N/A"
    cheap_marker = "  << CHEAP" if is_cheap else ""

    print("  " + ROW_FMT.format(
        hour=hour,
        price=price,
        soc_base=soc_base,
        soc_base_pct=soc_base / MAX_CAPACITY * 100,
        soc_shav=soc_shav,
        soc_shav_pct=soc_shav / MAX_CAPACITY * 100,
        limit=limit_str,
        chg=chg_shav,
        fi=fi_shav,
        cheap_marker=cheap_marker,
    ))

    soc_base = soc_base_new
    soc_shav = soc_shav_new

print("  " + "-" * 97)
print(f"  End of day  "
      f"SOC-base: {soc_base:.0f} Wh ({soc_base/MAX_CAPACITY*100:.0f}%)  "
      f"SOC-shav: {soc_shav:.0f} Wh ({soc_shav/MAX_CAPACITY*100:.0f}%)")
print()
print(f"  Total charged (base):       {total_charged_base:.2f} kWh")
print(f"  Total charged (shaving):    {total_charged_shav:.2f} kWh")
print(f"  Total feed-in (base):       {total_feed_in_base:.2f} kWh")
print(f"  Total feed-in (shaving):    {total_feed_in_shav:.2f} kWh")
print(f"  Feed-in during cheap hours (base):    {wasted_cheap_base:.2f} kWh  "
      f"(PV surplus at cheap prices that could not be stored)")
print(f"  Feed-in during cheap hours (shaving): {wasted_cheap_shav:.2f} kWh")
print("=" * 105)

# ---------------------------------------------------------------------------
# Per-hour debug: show reserve, allowed, raw limit for each production slot
# ---------------------------------------------------------------------------
print()
print("Pre-window reserve calculation trace (price-based, initial SOC):")
print(f"  {'Hour':>5}  {'PV (W)':>8}  {'Price':>7}  "
      f"{'FreeCapWh':>10}  {'ReserveWh':>10}  {'AllowedWh':>10}  "
      f"{'RawLimit W':>11}  {'Applied W':>10}")
print("  " + "-" * 80)

debug_soc = float(INITIAL_SOC_WH)
debug_logic = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
debug_logic.set_calculation_parameters(params_price)

# We need CommonLogic still pointing to the right instance
# (already set above via get_instance)

for hour in range(24):
    ts_d   = BASE_DATE.replace(hour=hour)
    prod_w = float(PRODUCTION_PROFILE_W[hour])
    price  = float(PRICE_PROFILE[hour])

    calc_input_d = build_calc_input(hour, debug_soc)
    free_cap     = calc_input_d.free_capacity

    # Compute reserve metrics manually mirroring the implementation
    params       = debug_logic.calculation_parameters
    prices_d     = calc_input_d.prices
    prod_d       = calc_input_d.production
    cons_d       = calc_input_d.consumption

    # Find production_end in the shifted array
    prod_end = len(prices_d)
    for i, p in enumerate(prod_d):
        if float(p) == 0:
            prod_end = i
            break

    cheap_slots_d = [i for i, p in enumerate(prices_d)
                     if i < prod_end and p is not None and p <= PRICE_LIMIT]
    surplus_in_cheap = sum(
        max(float(prod_d[i]) - float(cons_d[i]), 0) * INTERVAL_H
        for i in cheap_slots_d
        if i < len(prod_d) and i < len(cons_d)
    )
    reserve_wh  = min(surplus_in_cheap, MAX_CAPACITY) if cheap_slots_d else 0.0
    allowed_wh  = free_cap - reserve_wh

    if prod_w > 0:
        raw = debug_logic._calculate_peak_shaving_charge_limit_price_based(
            calc_input_d)
        applied = common.enforce_min_pv_charge_rate(raw) if raw >= 0 else -1
    else:
        raw     = None
        applied = None

    raw_s     = f"{raw:>11}" if raw is not None and raw >= 0 else (
                "          0" if raw == 0 else "        N/A")
    applied_s = f"{applied:>10}" if applied is not None and applied >= 0 else (
                "         0" if applied == 0 else "       N/A")
    cheap_mk  = "  << CHEAP" if price <= PRICE_LIMIT else ""

    print(f"  {hour:02d}:00  {prod_w:>8.0f}  {price:>7.3f}  "
          f"{free_cap:>10.0f}  {reserve_wh:>10.0f}  {allowed_wh:>10.0f}  "
          f"{raw_s}  {applied_s}{cheap_mk}")

    # Advance SOC without limiting (to see reserve shrink as battery fills)
    _, _, debug_soc = apply_one_hour(prod_w, CONSUMPTION_W, -1, debug_soc)

print("  " + "-" * 80)
print()
print("FINDINGS:")
print(f"  Before hours {CHEAP_HOURS}: price-based algo sees upcoming cheap-window")
print(f"  PV surplus and holds back capacity (reserve_wh).  Once free_cap")
print(f"  shrinks below reserve_wh the algorithm blocks charging (raw=0).")
print(f"  Inside cheap window (hours {CHEAP_HOURS}): surplus spread evenly if it")
print(f"  exceeds free capacity; otherwise no limit (-1).")
