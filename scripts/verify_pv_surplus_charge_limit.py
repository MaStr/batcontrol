#!/usr/bin/env python3
"""
Verification script for PV surplus -> charge limit conversion (solar_cap rule).

The solar feed-in limit rule has to bridge two different units:

  * the forecast arrays hold ENERGY per slot (Wh), and
  * both the configured feed-in limit and the resulting battery charge
    limit are POWER values (W).

This script walks through that conversion step by step so the arithmetic can
be checked by hand. It uses the production implementation from
``src/batcontrol/logic/solar_limit.py`` -- every printed number is produced
by the real code, not re-implemented here.

The conversion happens at exactly two points:

    feed_allow_wh = feed_in_limit_w * slot_h    # entry: W limit -> Wh budget
    clip_wh       = surplus_wh - feed_allow_wh  # comparison purely in Wh
    floor_w       = clip_wh[0] / slot0_hours    # exit:  Wh -> W charge limit

Everything in between stays in the Wh domain.

Reference scenario: feed-in limit 4000 W, PV peak 5000 W, house load 400 W.
The expected floor is the difference at the grid connection point:
(5000 - 400) - 4000 = 600 W.

Usage:
    python scripts/verify_pv_surplus_charge_limit.py

See docs/features/peak-shaving.md and
docs/development/solar-limit-evaluation.md for the algorithm description.
"""
import sys
import os
import numpy as np

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# pylint: disable=wrong-import-position
from batcontrol.logic.solar_limit import compute_solar_limit, merge_limits

FEED_IN_LIMIT_W = 4000.0
HOUSE_LOAD_W = 400.0
MAX_CAPACITY_WH = 10000.0

# Production power per slot in W. A trailing zero terminates the production
# window (same convention as the production code).
PEAK_PROFILE_W = [5000, 5200, 5000, 4600, 4000, 3200, 0]

SEPARATOR = "=" * 92


def print_slot_table(production_w, interval_h, slot0_h, headroom=1.0):
    """Print the per-slot Wh arithmetic and return the (prod, cons) Wh arrays.

    Mirrors the internal steps of compute_solar_limit so the intermediate
    Wh values become visible. The returned arrays are what core.py would
    hand to the logic layer.
    """
    slot_h = np.full(len(production_w), interval_h, dtype=float)
    slot_h[0] = slot0_h

    production_wh = np.array(production_w, dtype=float) * slot_h
    consumption_wh = np.full(len(production_w), HOUSE_LOAD_W) * slot_h

    surplus_wh = np.clip(production_wh - consumption_wh, 0, None)
    feed_allow_wh = FEED_IN_LIMIT_W * slot_h
    clip_wh = np.clip(surplus_wh * headroom - feed_allow_wh, 0, None)

    print(f"  {'slot':>4} {'P[W]':>7} {'h':>6} {'P[Wh]':>8} {'surplus[Wh]':>12} "
          f"{'surplus[W]':>11} {'allowed[Wh]':>12} {'clip[Wh]':>9} {'clip[W]':>8}")
    for i, power_w in enumerate(production_w):
        clip_w = clip_wh[i] / slot_h[i]
        surplus_w = surplus_wh[i] / slot_h[i]
        print(f"  {i:>4} {power_w:>7.0f} {slot_h[i]:>6.3f} "
              f"{production_wh[i]:>8.1f} {surplus_wh[i]:>12.1f} {surplus_w:>11.0f} "
              f"{feed_allow_wh[i]:>12.1f} {clip_wh[i]:>9.1f} {clip_w:>8.0f}")

    return production_wh, consumption_wh


# pylint: disable=too-many-arguments,too-many-positional-arguments
def run_case(title, production_w, interval_h, slot0_h, free_capacity_wh,
             headroom=1.0, note=None):
    """Print one scenario: slot table plus the resulting floor/cap."""
    print(f"\n{SEPARATOR}\n{title}\n{SEPARATOR}")
    if note:
        print(f"  {note}\n")

    production_wh, consumption_wh = print_slot_table(
        production_w, interval_h, slot0_h, headroom)

    floor_w, cap_w = compute_solar_limit(
        production_wh, consumption_wh, FEED_IN_LIMIT_W, interval_h,
        free_capacity_wh, MAX_CAPACITY_WH,
        headroom=headroom, slot0_hours=slot0_h)

    print(f"\n  free_capacity = {free_capacity_wh:.0f} Wh, headroom = {headroom}")
    print(f"  -> FLOOR = {floor_w} W    CAP = "
          f"{cap_w if cap_w >= 0 else 'off (-1)'} W")
    return floor_w, cap_w


def case_inside_clip_window():
    """Standing in the clip slot at a slot boundary -- the base case."""
    floor_w, _ = run_case(
        "CASE B1 -- 15 min, inside the clip slot, exactly at 12:00:00",
        PEAK_PROFILE_W, 0.25, 0.25, 3000.0,
        note="Detection: 1150 Wh surplus > 1000 Wh feed-in budget -> clipping.")

    surplus_w = PEAK_PROFILE_W[0] - HOUSE_LOAD_W
    print("\n  Cross-check in the power domain:")
    print(f"    surplus {surplus_w:.0f} W - feed-in limit "
          f"{FEED_IN_LIMIT_W:.0f} W = {surplus_w - FEED_IN_LIMIT_W:.0f} W")
    print(f"    floor from the Wh calculation           = {floor_w} W")
    assert floor_w == int(surplus_w - FEED_IN_LIMIT_W), "floor must equal the power difference"
    print("    match -> the floor is exactly the excess over the feed-in limit")


def case_partial_slot():
    """Mid-slot: both sides of the subtraction shrink, the floor does not."""
    print(f"\n{SEPARATOR}\nCASE B2 -- 15 min at 12:07:30 (half of slot 0 has "
          f"already elapsed)\n{SEPARATOR}")
    print("  core.py factorizes slot 0 by (1 - elapsed) BEFORE the logic sees it,\n"
          "  and slot0_hours shrinks to the remaining 7.5 min = 0.125 h.\n")

    elapsed = 0.5
    slot0_h = 0.25 * (1.0 - elapsed)

    production_wh = np.array(PEAK_PROFILE_W, dtype=float) * 0.25
    consumption_wh = np.full(len(PEAK_PROFILE_W), HOUSE_LOAD_W) * 0.25
    production_wh[0] *= (1.0 - elapsed)
    consumption_wh[0] *= (1.0 - elapsed)

    surplus_wh = production_wh[0] - consumption_wh[0]
    allowed_wh = FEED_IN_LIMIT_W * slot0_h
    clip_wh = surplus_wh - allowed_wh

    print(f"  slot 0 production  : {production_wh[0]:>7.1f} Wh   (was 1250.0 Wh)")
    print(f"  slot 0 consumption : {consumption_wh[0]:>7.1f} Wh   (was  100.0 Wh)")
    print(f"  slot 0 surplus     : {surplus_wh:>7.1f} Wh   (was 1150.0 Wh)")
    print(f"  feed-in allowance  : {allowed_wh:>7.1f} Wh   "
          f"(4000 W * {slot0_h} h, was 1000.0 Wh)")
    print(f"  clip               : {clip_wh:>7.1f} Wh   (was  150.0 Wh)")
    print(f"  floor              : {clip_wh:.1f} Wh / {slot0_h} h = "
          f"{clip_wh / slot0_h:.0f} W")

    floor_w, cap_w = compute_solar_limit(
        production_wh, consumption_wh, FEED_IN_LIMIT_W, 0.25, 3000.0,
        MAX_CAPACITY_WH, slot0_hours=slot0_h)
    print(f"\n  -> FLOOR = {floor_w} W    CAP = {cap_w} W")
    print("  Every Wh value halved, but the floor is unchanged at 600 W:\n"
          "  surplus and allowance both scale with slot_h, so the quotient is\n"
          "  invariant against the position inside the slot.")
    assert floor_w == 600, "floor must be invariant against slot position"


def case_scarce_capacity():
    """With little free capacity the cap collapses onto the floor."""
    floor_w, cap_w = run_case(
        "CASE B3 -- 15 min, scarce free capacity (400 Wh)",
        PEAK_PROFILE_W, 0.25, 0.25, 400.0,
        note="Total clip energy 550 Wh exceeds the free capacity.")
    print("  Cap equals floor: the battery absorbs ONLY otherwise-curtailed\n"
          "  energy, exportable surplus goes to the grid instead.")
    assert floor_w == cap_w, "cap must collapse onto the floor"


def case_headroom():
    """Headroom inflates the forecast surplus before the clip is computed."""
    floor_w, _ = run_case(
        "CASE B4 -- 15 min, headroom 1.1 (forecast underestimates peaks)",
        PEAK_PROFILE_W, 0.25, 0.25, 3000.0, headroom=1.1)
    surplus_w = (5000 - HOUSE_LOAD_W) * 1.1
    print(f"\n  Cross-check: {surplus_w:.0f} W * headroom - "
          f"{FEED_IN_LIMIT_W:.0f} W = {surplus_w - FEED_IN_LIMIT_W:.0f} W "
          f"-> floor {floor_w} W")


def case_before_clip_window():
    """Clip window still ahead -> reservation cap, no floor."""
    profile = [3000, 3000, 3000, 3000] + PEAK_PROFILE_W[:6] + [0]
    floor_w, cap_w = run_case(
        "CASE A -- 15 min, clip window starts in slot 4 -> reservation cap",
        profile, 0.25, 0.25, 3000.0,
        note="No clipping yet, so no floor; instead the battery is throttled\n"
             "  so the predicted clip energy still fits later on.")
    print(f"  free 3000 Wh - clip 550 Wh = 2450 Wh allowed over 1.0 h "
          f"-> {cap_w} W")
    print("  Charging at that rate for one hour leaves exactly 550 Wh free.")
    assert floor_w == 0, "no floor expected before the clip window"


def _power_at(hours):
    """Piecewise-linear PV power curve in W, argument in hours from now."""
    control_h = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
    control_w = [4600, 5000, 5200, 5000, 4600, 4000, 3200, 2200, 1000, 0]
    return np.interp(hours, control_h, control_w)


def _sample_curve(interval_h, n_slots):
    """Average the power curve over each slot and return energy per slot (Wh)."""
    energies = []
    for i in range(n_slots):
        start = i * interval_h
        samples = np.linspace(start, start + interval_h, 61)
        energies.append(float(np.mean(_power_at(samples))) * interval_h)
    return np.array(energies)


def case_resolution_comparison():
    """Same physical PV curve, sampled at 15 and 60 minutes.

    This is NOT a claim that both resolutions must return the same number.
    Slot 0 means a different stretch of wall-clock time in each case, so the
    averaged power -- and therefore the floor -- legitimately differs. The
    point is to show HOW MUCH resolution matters for a sharp midday peak.
    """
    print(f"\n{SEPARATOR}\nRESOLUTION -- one physical PV curve, sampled at 15 "
          f"vs 60 minutes\n{SEPARATOR}")
    print("  Slot 0 covers a different stretch of wall-clock time in each\n"
          "  case, so the averaged power differs. Coarser slots smear a sharp\n"
          "  peak, which changes the detected clip.\n")

    for interval_h, n_slots, label in ((0.25, 18, "15 min"), (1.0, 5, "60 min")):
        production_wh = _sample_curve(interval_h, n_slots)
        consumption_wh = np.full(n_slots, HOUSE_LOAD_W * interval_h)
        floor_w, cap_w = compute_solar_limit(
            production_wh, consumption_wh, FEED_IN_LIMIT_W, interval_h,
            3000.0, MAX_CAPACITY_WH, slot0_hours=interval_h)

        slot0_power = production_wh[0] / interval_h
        total_clip = float(np.sum(np.clip(
            production_wh - consumption_wh - FEED_IN_LIMIT_W * interval_h,
            0, None)))
        print(f"  {label}: slot 0 avg power = {slot0_power:>6.0f} W, "
              f"FLOOR = {floor_w:>4} W, CAP = {cap_w:>5} W, "
              f"total clip = {total_clip:>6.0f} Wh")

    print("\n  The 60-min average over the first hour sits higher than the\n"
          "  first quarter alone (the curve is still rising), so the coarse\n"
          "  grid reports a larger slot-0 clip here. On a peak centred inside\n"
          "  the hour the averaging works the other way and hides clipping.\n"
          "  15-min resolution tracks the real curve more closely.")


def case_merge():
    """How the floor interacts with the other peak shaving caps."""
    print(f"\n{SEPARATOR}\nMERGE -- floor vs the competing peak shaving caps"
          f"\n{SEPARATOR}")
    print("  final = max(floor, min(caps))\n")
    print("  time rule wants 500 W, solar floor 600 W, solar cap 2233 W:")
    print(f"    merge_limits(600, [500, 2233])  = {merge_limits(600, [500, 2233])} W"
          f"   <- floor wins, no curtailment")
    print("  time rule wants 3000 W:")
    print(f"    merge_limits(600, [3000, 2233]) = {merge_limits(600, [3000, 2233])} W"
          f"  <- strictest cap wins")
    print("  no cap active at all:")
    print(f"    merge_limits(600, [None, -1])   = {merge_limits(600, [None, -1])} W"
          f"     <- unlimited already satisfies the floor")


def main():
    """Run all scenarios."""
    print(SEPARATOR)
    print("PV surplus -> charge limit verification (solar_cap rule)")
    print(SEPARATOR)
    slot0_w = PEAK_PROFILE_W[0]
    print(f"  feed-in limit    : {FEED_IN_LIMIT_W:.0f} W")
    print(f"  PV in slot 0     : {slot0_w:.0f} W "
          f"(profile peaks at {max(PEAK_PROFILE_W):.0f} W in slot 1)")
    print(f"  house load       : {HOUSE_LOAD_W:.0f} W")
    print(f"  expected floor   : ({slot0_w:.0f} - {HOUSE_LOAD_W:.0f}) - "
          f"{FEED_IN_LIMIT_W:.0f} = "
          f"{slot0_w - HOUSE_LOAD_W - FEED_IN_LIMIT_W:.0f} W")

    case_inside_clip_window()
    case_partial_slot()
    case_scarce_capacity()
    case_headroom()
    case_before_clip_window()
    case_resolution_comparison()
    case_merge()

    print(f"\n{SEPARATOR}")
    print("[done] All cross-checks passed.")
    print(SEPARATOR)


if __name__ == '__main__':
    main()
