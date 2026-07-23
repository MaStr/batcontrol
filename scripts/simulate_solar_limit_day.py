#!/usr/bin/env python3
"""Day simulation for solar feed-in limit clip absorption (Solarspitzengesetz).

German law (in force since 2025-02-25) limits uncontrolled PV plants (no
iMSys + Steuerbox) to feeding in at most 60% of their installed power at the
grid connection point. The inverter curtails everything above the limit --
that energy is LOST unless it is self-consumed or charged into the battery.

This script evaluates a proposed peak-shaving extension ("solar cap rule"):
  - BEFORE the predicted clipping window: cap PV->battery charging so battery
    capacity is reserved for energy that would otherwise be curtailed
    (exportable energy must not displace clip energy 1:1).
  - DURING clipping slots: enforce a charge FLOOR so the battery absorbs at
    least the power above the feed-in limit. Important: the existing
    time/price peak-shaving caps can otherwise CAUSE curtailment losses.

Rule switches simulated (proposed config design):
    time_active      - existing counter-linear ramp until allow_full_battery_after
    solar_cap_active - new rule evaluated here (reservation cap + floor)
    (price_active exists in the code base but is orthogonal; not simulated)

Priority between rules:
    final_limit = max(solar_floor, min(all active caps))
    -1 = no cap. The floor overrides every cap because a cap below the floor
    burns energy (curtailment); caps only optimize economics.

The "reference algorithm" section below is the standalone copy this
evaluation was run with; the authoritative production implementation now
lives in src/batcontrol/logic/solar_limit.py (ported from here, with the
surplus-headroom and headroom-floor variants baked in).

Usage:
    python scripts/simulate_solar_limit_day.py
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
    PeakShavingConfig,
)
from batcontrol.logic.common import CommonLogic

# ---------------------------------------------------------------------------
# Global simulation parameters
# ---------------------------------------------------------------------------
FEED_IN_LIMIT_W  = 6_000   # W = 60% of a 10 kWp plant
CONSUMPTION_W    = 400     # W constant house load (unless scenario overrides)
ALLOW_FULL_AFTER = 14      # target hour for the legacy time rule
INITIAL_SOC_PCT  = 0.15

TZ        = datetime.timezone.utc
BASE_DATE = datetime.datetime(2026, 6, 21, 0, 0, 0, tzinfo=TZ)

# 10 kWp south-facing, clear summer day (W per hour). Peak ~8.9 kW.
PROFILE_SOUTH_W = np.array([
       0,    0,    0,    0,    0,   50,   # 00-05
     300, 1200, 2800, 4700, 6300, 7600,   # 06-11
    8900, 8800, 7800, 6300, 4600, 2700,   # 12-17
    1100,  300,   30,    0,    0,    0,   # 18-23
], dtype=float)

# 10 kWp east-west, flatter curve, peak below the feed-in limit.
PROFILE_EAST_WEST_W = np.array([
       0,    0,    0,    0,    0,  100,   # 00-05
     700, 1800, 3000, 4100, 4900, 5400,   # 06-11
    5600, 5600, 5400, 4900, 4100, 3000,   # 12-17
    1800,  700,  100,    0,    0,    0,   # 18-23
], dtype=float)


# ---------------------------------------------------------------------------
# Reference algorithm (production version: src/batcontrol/logic/solar_limit.py)
# ---------------------------------------------------------------------------
def compute_solar_limit(production_wh, consumption_wh, feed_in_limit_w,
                        interval_h, free_capacity_wh, max_capacity_wh,
                        headroom=1.0, slot0_hours=None, headroom_on='clip',
                        floor_source='raw'):
    """Compute the solar-cap rule output for the current slot.

    Args:
        production_wh:   forecast PV energy per slot (Wh), index 0 = now.
        consumption_wh:  forecast consumption per slot (Wh).
        feed_in_limit_w: grid feed-in power limit in W. <= 0 = rule inactive
                         (neutral value, e.g. 0).
        interval_h:      slot length in hours (0.25 or 1.0).
        free_capacity_wh: battery free capacity (Wh).
        max_capacity_wh: battery max capacity (Wh).
        headroom:        safety factor >= 1.0 for RESERVATION sizing only
                         (forecasts understate clipping). Never applied to
                         the floor.
        slot0_hours:     remaining hours in the current slot (partial slot).
                         Defaults to interval_h.
        headroom_on:     'clip'    - multiply predicted clip energy (weak
                                     against underestimated production: slots
                                     forecast below the limit stay invisible)
                         'surplus' - multiply predicted surplus BEFORE the
                                     clip computation (reconstructs an
                                     underestimated production curve and also
                                     finds clip slots the raw forecast misses)
        floor_source:    'raw'      - floor from the raw forecast clip
                         'headroom' - floor from the headroom-adjusted clip.
                                      With a greedy-charging inverter the
                                      floor only RAISES the allowed cap, so
                                      this permits (never forces) absorbing
                                      more than the raw forecast predicts;
                                      cost: when the forecast is correct,
                                      some exportable surplus is charged
                                      instead of fed in (no energy loss).

    Returns:
        (floor_w, cap_w):
            floor_w: minimum charge rate (W) the battery must sustain NOW to
                     absorb power above the feed-in limit. 0 = no floor.
            cap_w:   charge rate cap (W) to reserve capacity for the clip
                     window. -1 = no cap, 0 = block charging.
    """
    if feed_in_limit_w is None or feed_in_limit_w <= 0:
        return 0, -1
    if slot0_hours is None:
        slot0_hours = interval_h

    n = min(len(production_wh), len(consumption_wh))
    # Production window ends at the first slot with zero production
    # (same convention as the price-based peak shaving rule).
    prod_end = n
    for i in range(n):
        if float(production_wh[i]) == 0:
            prod_end = i
            break
    if prod_end == 0:
        return 0, -1

    slot_h = np.full(prod_end, interval_h, dtype=float)
    slot_h[0] = slot0_hours

    surplus_wh = np.clip(
        np.asarray(production_wh[:prod_end], dtype=float)
        - np.asarray(consumption_wh[:prod_end], dtype=float),
        0, None)
    feed_allow_wh = feed_in_limit_w * slot_h
    clip_raw_wh = np.clip(surplus_wh - feed_allow_wh, 0, None)
    # Headroom only inflates the reservation; a slot can never clip more
    # than its (headroom-adjusted) surplus. The floor always uses the raw
    # clip so we never force absorbing exportable energy.
    if headroom_on == 'surplus':
        surplus_hr_wh = surplus_wh * headroom
        clip_wh = np.minimum(surplus_hr_wh,
                             np.clip(surplus_hr_wh - feed_allow_wh, 0, None))
    else:
        clip_wh = np.minimum(surplus_wh, clip_raw_wh * headroom)

    clip_slots = np.nonzero(clip_wh > 0)[0]
    if len(clip_slots) == 0:
        return 0, -1

    first_clip = int(clip_slots[0])

    # -- Case A: before the clip window -> reservation cap ---------------- #
    if first_clip > 0:
        total_clip_wh = min(float(np.sum(clip_wh)), max_capacity_wh)
        allowed_wh = free_capacity_wh - total_clip_wh
        if allowed_wh <= 0:
            return 0, 0  # block PV charging, keep all capacity for the clip
        hours_before = slot0_hours + (first_clip - 1) * interval_h
        return 0, int(allowed_wh / hours_before)

    # -- Case B: inside a clip slot -> floor + capacity-preserving cap ---- #
    # Default floor from the RAW clip (no headroom): never lift the cap
    # beyond what the raw forecast predicts as curtailed.
    if floor_source == 'headroom':
        floor_w = clip_wh[0] / slot0_hours
    else:
        floor_w = clip_raw_wh[0] / slot0_hours
    total_surplus_wh = float(np.sum(surplus_wh))
    if total_surplus_wh <= free_capacity_wh:
        return int(floor_w), -1  # everything fits, no cap needed

    remaining_clip_wh = float(np.sum(clip_wh))
    extra_wh = max(0.0, free_capacity_wh - remaining_clip_wh)
    remaining_prod_h = float(np.sum(slot_h))
    # When clip energy alone exceeds free capacity (extra == 0) the cap
    # equals the floor: the battery absorbs ONLY otherwise-curtailed energy,
    # exportable surplus goes to the grid instead of displacing clip energy.
    cap_w = int(floor_w + extra_wh / remaining_prod_h)
    return int(floor_w), cap_w


def merge_limits(floor_w, caps):
    """Merge rule outputs: final = max(floor, min(active caps)).

    caps entries: -1 = rule emits no cap. Returns -1 (no limit), 0 (block)
    or a positive W value. An unlimited cap always satisfies the floor
    because the inverter charges PV surplus greedily.
    """
    active = [c for c in caps if c is not None and c >= 0]
    if not active:
        return -1
    return max(int(floor_w), min(active))


# ---------------------------------------------------------------------------
# Battery / feed-in model
# ---------------------------------------------------------------------------
def apply_slot(prod_w, cons_w, limit_w, stored_wh, capacity_wh,
               feed_in_limit_w, interval_h):
    """Advance the battery by one slot under a feed-in power limit.

    The inverter charges PV surplus greedily up to limit_w (-1 = unlimited),
    exports the rest up to feed_in_limit_w and curtails everything above.

    Returns (charge_w, feed_in_w, curtailed_w, new_stored_wh).
    """
    surplus_w = prod_w - cons_w
    if surplus_w <= 0:
        discharge_w = min(-surplus_w, stored_wh / interval_h)
        return 0.0, 0.0, 0.0, max(stored_wh - discharge_w * interval_h, 0.0)

    if limit_w == 0:
        want_w = 0.0
    elif limit_w > 0:
        want_w = min(surplus_w, float(limit_w))
    else:
        want_w = surplus_w

    charge_wh = min(want_w * interval_h, capacity_wh - stored_wh)
    charge_w = charge_wh / interval_h
    rest_w = surplus_w - charge_w
    feed_in_w = min(rest_w, feed_in_limit_w)
    curtailed_w = rest_w - feed_in_w
    return charge_w, feed_in_w, curtailed_w, stored_wh + charge_wh


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------
def run_day(prod_actual_w, cons_actual_w, capacity_wh,
            time_active=False, solar_cap_active=False,
            forecast_prod_w=None, forecast_cons_w=None,
            feed_in_limit_w=FEED_IN_LIMIT_W, headroom=1.0,
            headroom_on='clip', floor_source='raw',
            interval_min=60, allow_full_after=ALLOW_FULL_AFTER,
            initial_soc_wh=None, collect_rows=False):
    """Simulate one day and return metrics (and per-slot rows on request)."""
    interval_h = interval_min / 60.0
    n_slots = len(prod_actual_w)
    if forecast_prod_w is None:
        forecast_prod_w = prod_actual_w
    if forecast_cons_w is None:
        forecast_cons_w = cons_actual_w
    if initial_soc_wh is None:
        initial_soc_wh = INITIAL_SOC_PCT * capacity_wh

    # CommonLogic is a singleton keyed to battery capacity -> reset per run.
    CommonLogic._instance = None
    common = CommonLogic.get_instance(
        charge_rate_multiplier=1.1,
        always_allow_discharge_limit=0.90,
        max_capacity=capacity_wh,
    )
    logic = NextLogic(timezone=TZ, interval_minutes=interval_min)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=0.79,
        min_price_difference=0.05,
        min_price_difference_rel=0.2,
        max_capacity=capacity_wh,
        peak_shaving=PeakShavingConfig(
            enabled=True, mode='time',
            allow_full_battery_after=allow_full_after,
        ),
    ))

    stored_wh = float(initial_soc_wh)
    totals = {'charged_wh': 0.0, 'feed_in_wh': 0.0, 'curtailed_wh': 0.0}
    rows = []

    for s in range(n_slots):
        minutes = s * interval_min
        ts = BASE_DATE + datetime.timedelta(minutes=minutes)
        prod_w = float(prod_actual_w[s])
        cons_w = float(cons_actual_w[s])

        fc_prod_wh = np.asarray(forecast_prod_w[s:], dtype=float) * interval_h
        fc_cons_wh = np.asarray(forecast_cons_w[s:], dtype=float) * interval_h
        free_cap = capacity_wh - stored_wh

        floor_w, solar_cap_w = 0, -1
        if solar_cap_active:
            floor_w, solar_cap_w = compute_solar_limit(
                fc_prod_wh, fc_cons_wh, feed_in_limit_w, interval_h,
                free_cap, capacity_wh, headroom=headroom,
                headroom_on=headroom_on, floor_source=floor_source)

        time_cap_w = -1
        if time_active and fc_prod_wh[0] > 0:
            # Mirror the relevant _apply_peak_shaving skip: unlimited in the
            # always_allow_discharge region (high SoC).
            if not common.is_discharge_always_allowed_capacity(stored_wh):
                calc_input = CalculationInput(
                    production=fc_prod_wh,
                    consumption=fc_cons_wh,
                    prices={},
                    stored_energy=stored_wh,
                    stored_usable_energy=max(stored_wh - 0.05 * capacity_wh, 0),
                    free_capacity=free_cap,
                )
                time_cap_w = logic._calculate_peak_shaving_charge_limit(
                    calc_input, ts)

        final_w = merge_limits(floor_w, [time_cap_w, solar_cap_w])
        if final_w > 0:
            final_w = common.enforce_min_pv_charge_rate(final_w)

        charge_w, feed_w, curt_w, stored_wh_new = apply_slot(
            prod_w, cons_w, final_w, stored_wh, capacity_wh,
            feed_in_limit_w, interval_h)

        totals['charged_wh'] += charge_w * interval_h
        totals['feed_in_wh'] += feed_w * interval_h
        totals['curtailed_wh'] += curt_w * interval_h

        if collect_rows:
            rows.append({
                'ts': ts, 'prod_w': prod_w, 'cons_w': cons_w,
                'floor_w': int(floor_w), 'time_cap_w': time_cap_w,
                'solar_cap_w': solar_cap_w, 'final_w': final_w,
                'charge_w': charge_w, 'feed_w': feed_w, 'curt_w': curt_w,
                'soc_pct': stored_wh / capacity_wh * 100,
            })
        stored_wh = stored_wh_new

    totals['end_soc_pct'] = stored_wh / capacity_wh * 100
    totals['rows'] = rows
    return totals


def clip_potential_wh(prod_w, cons_w, feed_in_limit_w, interval_h):
    """Energy above the feed-in limit if no battery absorbed anything (Wh)."""
    surplus = np.clip(np.asarray(prod_w) - np.asarray(cons_w), 0, None)
    return float(np.sum(np.clip(surplus - feed_in_limit_w, 0, None))) * interval_h


def fmt_cap(v):
    return '   -' if v < 0 else f'{v:>4d}'


def print_rows(rows):
    print(f"  {'Time':>5}  {'PV W':>5}  {'Floor':>5}  {'TimeCap':>7}  "
          f"{'SolarCap':>8}  {'Final':>6}  {'Chg W':>5}  {'Feed W':>6}  "
          f"{'Curt W':>6}  {'SoC%':>5}")
    print('  ' + '-' * 78)
    for r in rows:
        if r['prod_w'] <= 0 and r['ts'].hour not in (4, 21):
            continue  # keep output compact: skip most night slots
        final = 'unlim' if r['final_w'] < 0 else str(r['final_w'])
        print(f"  {r['ts'].strftime('%H:%M')}  {r['prod_w']:>5.0f}  "
              f"{r['floor_w']:>5d}  {fmt_cap(r['time_cap_w']):>7}  "
              f"{fmt_cap(r['solar_cap_w']):>8}  {final:>6}  "
              f"{r['charge_w']:>5.0f}  {r['feed_w']:>6.0f}  "
              f"{r['curt_w']:>6.0f}  {r['soc_pct']:>5.1f}")
    print('  ' + '-' * 78)


def print_summary(title, results, potential_wh):
    print(f"  {title}")
    print(f"  {'Trace':<34} {'Charged':>9} {'Feed-in':>9} {'Curtailed':>10} "
          f"{'EndSoC':>7} {'ClipRecov':>10}")
    print('  ' + '-' * 84)
    for name, res in results:
        recov = ''
        if potential_wh > 0:
            recov_pct = (potential_wh - res['curtailed_wh']) / potential_wh * 100
            recov = f'{recov_pct:>9.1f}%'
        print(f"  {name:<34} {res['charged_wh']/1000:>7.2f}kWh "
              f"{res['feed_in_wh']/1000:>7.2f}kWh {res['curtailed_wh']/1000:>8.2f}kWh "
              f"{res['end_soc_pct']:>6.1f}% {recov:>10}")
    print('  ' + '-' * 84)
    print(f"  Clip potential (no battery absorption): {potential_wh/1000:.2f} kWh")
    print()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def scenario_reference():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cap = 10_000
    potential = clip_potential_wh(PROFILE_SOUTH_W, cons, FEED_IN_LIMIT_W, 1.0)

    base = run_day(PROFILE_SOUTH_W, cons, cap)
    legacy = run_day(PROFILE_SOUTH_W, cons, cap, time_active=True)
    solar = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                    collect_rows=True)
    both = run_day(PROFILE_SOUTH_W, cons, cap, time_active=True,
                   solar_cap_active=True, collect_rows=True)

    print('=' * 88)
    print('  SCENARIO 1 -- Reference: 10 kWp south, clear day, limit 6000 W, '
          '10 kWh battery, 400 W load')
    print('=' * 88)
    print_summary('Full-day comparison:', [
        ('baseline (all rules off)', base),
        ('time_active only (legacy)', legacy),
        ('solar_cap_active only', solar),
        ('time_active + solar_cap_active', both),
    ], potential)
    print('  Slot detail, solar_cap_active only:')
    print_rows(solar['rows'])
    print()
    print('  Slot detail, time_active + solar_cap_active '
          '(floor overrides time cap in clip slots):')
    print_rows(both['rows'])
    print()
    return {'baseline': base, 'legacy': legacy, 'solar': solar, 'both': both,
            'potential': potential}


def scenario_east_west():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cap = 10_000
    potential = clip_potential_wh(PROFILE_EAST_WEST_W, cons, FEED_IN_LIMIT_W, 1.0)
    base = run_day(PROFILE_EAST_WEST_W, cons, cap)
    solar = run_day(PROFILE_EAST_WEST_W, cons, cap, solar_cap_active=True)
    print('=' * 88)
    print('  SCENARIO 2 -- East-west 10 kWp (peak 5.6 kW < limit): rule must '
          'stay inert')
    print('=' * 88)
    print_summary('No clipping expected; solar rule must not change anything:', [
        ('baseline', base),
        ('solar_cap_active only', solar),
    ], potential)
    identical = abs(base['curtailed_wh'] - solar['curtailed_wh']) < 1e-6 and \
        abs(base['end_soc_pct'] - solar['end_soc_pct']) < 1e-6
    print(f"  Check: solar trace identical to baseline: {identical}")
    print()


def scenario_small_battery():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cap = 5_000
    potential = clip_potential_wh(PROFILE_SOUTH_W, cons, FEED_IN_LIMIT_W, 1.0)
    base = run_day(PROFILE_SOUTH_W, cons, cap)
    solar = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                    collect_rows=True)
    print('=' * 88)
    print('  SCENARIO 3 -- Small battery 5 kWh: clip energy exceeds free '
          'capacity (scarcity)')
    print('=' * 88)
    # Theoretical max recovery = free capacity when the clip window starts
    # (overnight house load drains the battery below the day-start SoC).
    first_clip_row = next(r for r in solar['rows'] if r['floor_w'] > 0)
    free_at_window = cap * (1 - first_clip_row['soc_pct'] / 100)
    print_summary(
        f'Free capacity at window start: {free_at_window/1000:.2f} kWh '
        f'< clip potential {potential/1000:.2f} kWh:', [
            ('baseline', base),
            ('solar_cap_active only', solar),
        ], potential)
    recovered = potential - solar['curtailed_wh']
    print(f"  Recovered clip energy: {recovered/1000:.2f} kWh "
          f"(theoretical max = free capacity at window start = "
          f"{free_at_window/1000:.2f} kWh)")
    print('  Slot detail (cap == floor inside window once capacity is scarce):')
    print_rows(solar['rows'])
    print()


def scenario_forecast_error():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cap = 10_000
    forecast = PROFILE_SOUTH_W * 0.85  # forecast 15% below actual
    potential = clip_potential_wh(PROFILE_SOUTH_W, cons, FEED_IN_LIMIT_W, 1.0)
    base = run_day(PROFILE_SOUTH_W, cons, cap)
    h10 = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                  forecast_prod_w=forecast, headroom=1.0)
    h12 = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                  forecast_prod_w=forecast, headroom=1.2)
    h15 = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                  forecast_prod_w=forecast, headroom=1.5)
    perfect = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True)
    print('=' * 88)
    print('  SCENARIO 4 -- Forecast error: forecast = 85% of actual '
          '(underestimates clipping)')
    print('=' * 88)
    print_summary('Effect of feed_in_limit_headroom on the reservation:', [
        ('baseline', base),
        ('solar, headroom 1.0', h10),
        ('solar, headroom 1.2', h12),
        ('solar, headroom 1.5', h15),
        ('solar, perfect forecast (ref)', perfect),
    ], potential)


def scenario_forecast_error_125():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cap = 10_000
    forecast = PROFILE_SOUTH_W / 1.25  # actual = 125% of forecast
    potential = clip_potential_wh(PROFILE_SOUTH_W, cons, FEED_IN_LIMIT_W, 1.0)
    fc_potential = clip_potential_wh(forecast, cons, FEED_IN_LIMIT_W, 1.0)
    base = run_day(PROFILE_SOUTH_W, cons, cap)
    clip_hr = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                      forecast_prod_w=forecast, headroom=1.25,
                      headroom_on='clip')
    surp_hr = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                      forecast_prod_w=forecast, headroom=1.25,
                      headroom_on='surplus')
    moderate = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                       forecast_prod_w=forecast, headroom=1.1,
                       headroom_on='surplus', floor_source='headroom')
    combined = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                       forecast_prod_w=forecast, headroom=1.25,
                       headroom_on='surplus', floor_source='headroom')
    perfect = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True)
    # Regression: what do the same settings cost when the forecast is
    # already correct? The inflated floor lets the battery absorb
    # exportable energy inside the window, displacing clip energy 1:1
    # (the day is capacity-scarce: total surplus >> free capacity).
    perfect_mod = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                          headroom=1.1, headroom_on='surplus',
                          floor_source='headroom')
    perfect_aggr = run_day(PROFILE_SOUTH_W, cons, cap, solar_cap_active=True,
                           headroom=1.25, headroom_on='surplus',
                           floor_source='headroom')
    print('=' * 88)
    print('  SCENARIO 4b -- Severe forecast error: actual = 125% of forecast')
    print('=' * 88)
    print(f'  Forecast sees only {fc_potential/1000:.2f} kWh clip potential '
          f'(actual: {potential/1000:.2f} kWh) and')
    print('  misses entire clip slots -- multiplying the predicted CLIP '
          'energy cannot fix that.')
    print('  All mitigations below are forecast-only (batcontrol has no '
          'live production measurement).')
    print()
    print_summary('Mitigation comparison (headroom target + floor source):', [
        ('baseline', base),
        ('solar, headroom 1.25 on clip', clip_hr),
        ('solar, headroom 1.25 on surplus', surp_hr),
        ('solar, surplus 1.1 + hr floor', moderate),
        ('solar, surplus 1.25 + hr floor', combined),
        ('solar, perfect forecast (ref)', perfect),
        ('solar, perfect fc + surplus 1.1', perfect_mod),
        ('solar, perfect fc + surplus 1.25', perfect_aggr),
    ], potential)


def scenario_consumption_spike():
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    cons[12:14] = 2_400  # cooking 12:00-14:00
    cap = 10_000
    potential = clip_potential_wh(PROFILE_SOUTH_W, cons, FEED_IN_LIMIT_W, 1.0)
    base = run_day(PROFILE_SOUTH_W, cons, cap)
    legacy = run_day(PROFILE_SOUTH_W, cons, cap, time_active=True)
    both = run_day(PROFILE_SOUTH_W, cons, cap, time_active=True,
                   solar_cap_active=True)
    print('=' * 88)
    print('  SCENARIO 5 -- Midday consumption spike (2.4 kW, 12-14h) reduces '
          'clipping')
    print('=' * 88)
    print_summary('Self-consumption already absorbs part of the peak:', [
        ('baseline', base),
        ('time_active only (legacy)', legacy),
        ('time_active + solar_cap_active', both),
    ], potential)


def scenario_15min():
    # Linear power interpolation of the hourly profile to 15-min slots.
    hours = np.arange(24)
    slots = np.arange(0, 24, 0.25)
    prod15 = np.interp(slots, hours, PROFILE_SOUTH_W)
    cons15 = np.full(len(slots), CONSUMPTION_W, dtype=float)
    cap = 10_000
    potential = clip_potential_wh(prod15, cons15, FEED_IN_LIMIT_W, 0.25)
    base = run_day(prod15, cons15, cap, interval_min=15)
    solar = run_day(prod15, cons15, cap, solar_cap_active=True,
                    interval_min=15)
    print('=' * 88)
    print('  SCENARIO 6 -- 15-minute interval resolution '
          '(same reference day, interpolated)')
    print('=' * 88)
    print_summary('Consistency check vs. hourly resolution:', [
        ('baseline (15 min)', base),
        ('solar_cap_active (15 min)', solar),
    ], potential)


if __name__ == '__main__':
    scenario_reference()
    scenario_east_west()
    scenario_small_battery()
    scenario_forecast_error()
    scenario_forecast_error_125()
    scenario_consumption_spike()
    scenario_15min()
