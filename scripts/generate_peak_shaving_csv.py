#!/usr/bin/env python3
"""Generate the peak shaving scenario datasets for the documentation.

Simulates one example day for every combination of the three peak shaving
rules and writes one CSV per configuration into
``docs/assets/data/peak_shaving/``. The documentation renders those CSVs as
interactive charts (see ``docs/assets/js/peak-shaving-charts.js``), so this
script produces data only -- no images.

The simulation drives the REAL implementation: it builds a ``CalculationInput``
per slot and calls ``NextLogic._apply_peak_shaving`` and
``NextLogic._apply_solar_limit``, the same two post-processing steps
``calculate_inverter_mode`` runs. Nothing about the rules is re-implemented
here, so the charts cannot drift away from the shipped behaviour.

Scope note: the upstream discharge decision is stipulated
(``allow_discharge=True``, ``charge_from_grid=False``) so the charts isolate
the peak shaving behaviour. In a real run a high evening price can withhold
the battery for expensive hours, which skips the time and price rules
entirely -- see docs/features/peak-shaving.md for that gating.

Usage:
    python scripts/generate_peak_shaving_csv.py [--out DIR]

Requires numpy and the batcontrol package (both satisfied by an editable
install of the project). No plotting dependencies.
"""
import argparse
import csv
import datetime
import os
import sys

import numpy as np

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# pylint: disable=wrong-import-position
from batcontrol.logic import solar_limit
from batcontrol.logic.common import CommonLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
    InverterControlSettings,
    PeakShavingConfig,
)
from batcontrol.logic.next import NextLogic

# --------------------------------------------------------------------------- #
#  Example day
# --------------------------------------------------------------------------- #

INTERVAL_MINUTES = 15
SLOTS_PER_DAY = 96
INTERVAL_H = INTERVAL_MINUTES / 60.0

MAX_CAPACITY_WH = 10000.0          # 10 kWh battery
START_SOC = 0.20                   # 20 % at midnight
FEED_IN_LIMIT_W = 4000.0           # 60 % of a 6.7 kWp plant
FEED_IN_HEADROOM = 1.0
PRICE_LIMIT = 0.05                 # EUR/kWh, "cheap" threshold
ALLOW_FULL_BATTERY_AFTER = 14      # target hour
ALWAYS_ALLOW_DISCHARGE_LIMIT = 0.90

PV_PEAK_W = 5000.0                 # clear summer day, peak at 13:00
PV_PEAK_HOUR = 13.0
PV_WIDTH_H = 2.6
HOUSE_LOAD_W = 400.0

PRICE_BASE = 0.28                  # EUR/kWh
PRICE_CHEAP = 0.03                 # 11:00 - 14:00
PRICE_EVENING = 0.42               # 17:00 - 20:00

# name, label, time_active, price_active, solar_cap_active
CONFIGURATIONS = [
    ('baseline', 'No peak shaving', False, False, False),
    ('time', 'Time based only', True, False, False),
    ('price', 'Price based only', False, True, False),
    ('solar', 'Solar cap only', False, False, True),
    ('time_price', 'Time + Price', True, True, False),
    ('time_solar', 'Time + Solar cap', True, False, True),
    ('price_solar', 'Price + Solar cap', False, True, True),
    ('all', 'All three rules', True, True, True),
]

CSV_COLUMNS = [
    'time', 'hour', 'pv_w', 'surplus_w', 'price',
    'limit_w', 'charge_w', 'to_grid_w', 'curtailed_w',
    'soc_pct', 'soc_baseline_pct',
    'rule_time_w', 'rule_price_w', 'rule_floor_w', 'rule_solar_cap_w',
]


def build_day():
    """Return (hours, pv_w, consumption_w, price) arrays for the example day."""
    hours = np.arange(SLOTS_PER_DAY) * INTERVAL_H

    pv_w = PV_PEAK_W * np.exp(
        -((hours - PV_PEAK_HOUR) ** 2) / (2 * PV_WIDTH_H ** 2))
    pv_w = np.where((hours >= 7.0) & (hours <= 19.0), pv_w, 0.0)
    # Cut the numerical tail so the production window has a clean start/end
    pv_w = np.where(pv_w < 60.0, 0.0, pv_w)

    consumption_w = np.full(SLOTS_PER_DAY, HOUSE_LOAD_W)

    price = np.full(SLOTS_PER_DAY, PRICE_BASE)
    price[(hours >= 11.0) & (hours < 14.0)] = PRICE_CHEAP
    price[(hours >= 17.0) & (hours < 20.0)] = PRICE_EVENING

    return hours, pv_w, consumption_w, price


def make_logic(time_active, price_active, solar_cap_active, enabled=True):
    """Build a NextLogic instance with the requested rule switches."""
    logic = NextLogic(timezone=datetime.timezone.utc,
                      interval_minutes=INTERVAL_MINUTES)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=0.79,
        min_price_difference=0.05,
        min_price_difference_rel=0.2,
        max_capacity=MAX_CAPACITY_WH,
        peak_shaving=PeakShavingConfig(
            enabled=enabled,
            allow_full_battery_after=ALLOW_FULL_BATTERY_AFTER,
            time_active=time_active,
            price_active=price_active,
            solar_cap_active=solar_cap_active,
            price_limit=PRICE_LIMIT,
            feed_in_limit_w=FEED_IN_LIMIT_W,
            feed_in_limit_headroom=FEED_IN_HEADROOM,
        ),
    ))
    return logic


# The simulation deliberately calls the two post-processing steps directly so
# the charts show exactly what the shipped rules do.
# pylint: disable=protected-access
def _rule_values(logic, calc_input, timestamp, stored_wh, common):
    """Return the raw per-rule outputs for charting (-1 means 'not active')."""
    peak_shaving = logic.calculation_parameters.peak_shaving
    time_w, price_w, floor_w, solar_cap_w = -1, -1, 0, -1

    if not peak_shaving.enabled or calc_input.production[0] <= 0:
        return time_w, price_w, floor_w, solar_cap_w

    past_target = timestamp.hour >= peak_shaving.allow_full_battery_after
    high_soc = common.is_discharge_always_allowed_capacity(stored_wh)

    if not past_target and not high_soc:
        if peak_shaving.time_active:
            time_w = logic._calculate_peak_shaving_charge_limit(
                calc_input, timestamp)
        if peak_shaving.price_active and peak_shaving.price_limit is not None:
            price_w = logic._calculate_peak_shaving_charge_limit_price_based(
                calc_input)

    if peak_shaving.solar_cap_active and peak_shaving.feed_in_limit_w > 0:
        # Mirrors _apply_solar_limit's own call
        floor_w, solar_cap_w = solar_limit.compute_solar_limit(
            calc_input.production, calc_input.consumption,
            peak_shaving.feed_in_limit_w, INTERVAL_H,
            calc_input.free_capacity, MAX_CAPACITY_WH,
            headroom=peak_shaving.feed_in_limit_headroom,
            slot0_hours=INTERVAL_H)

    return time_w, price_w, floor_w, solar_cap_w


# pylint: disable=too-many-locals
def simulate(config, day, baseline_soc=None):
    """Run one example day and return (rows, summary)."""
    _name, _label, time_active, price_active, solar_cap_active = config
    hours, pv_w, consumption_w, price = day

    enabled = time_active or price_active or solar_cap_active
    logic = make_logic(time_active, price_active, solar_cap_active,
                       enabled=enabled)

    common = CommonLogic.get_instance()
    common.max_capacity = MAX_CAPACITY_WH
    common.always_allow_discharge_limit = ALWAYS_ALLOW_DISCHARGE_LIMIT

    production_wh = pv_w * INTERVAL_H
    consumption_wh = consumption_w * INTERVAL_H

    stored_wh = MAX_CAPACITY_WH * START_SOC
    midnight = datetime.datetime(2025, 6, 21, tzinfo=datetime.timezone.utc)

    rows = []
    charged_wh = exported_wh = curtailed_wh = 0.0
    full_at = ''

    for i in range(SLOTS_PER_DAY):
        free_wh = MAX_CAPACITY_WH - stored_wh
        timestamp = midnight + datetime.timedelta(minutes=INTERVAL_MINUTES * i)

        calc_input = CalculationInput(
            production=production_wh[i:],
            consumption=consumption_wh[i:],
            prices=price[i:],
            stored_energy=stored_wh,
            stored_usable_energy=stored_wh * 0.95,
            free_capacity=free_wh,
        )

        # Same two post-processing steps calculate_inverter_mode runs, with the
        # upstream discharge decision stipulated (see module docstring).
        settings = InverterControlSettings(
            allow_discharge=True, charge_from_grid=False,
            charge_rate=0, limit_battery_charge_rate=-1)
        if logic.calculation_parameters.peak_shaving.enabled:
            settings = logic._apply_peak_shaving(settings, calc_input, timestamp)
            settings = logic._apply_solar_limit(settings, calc_input, timestamp)
        limit_w = settings.limit_battery_charge_rate

        time_w, price_w, floor_w, solar_cap_w = _rule_values(
            logic, calc_input, timestamp, stored_wh, common)

        surplus_w = max(0.0, pv_w[i] - consumption_w[i])
        allowed_w = surplus_w if limit_w < 0 else min(surplus_w, float(limit_w))
        charge_wh_slot = min(allowed_w * INTERVAL_H, free_wh)
        charge_w = charge_wh_slot / INTERVAL_H

        to_grid_w = surplus_w - charge_w
        curtailed_w = max(0.0, to_grid_w - FEED_IN_LIMIT_W)
        exported_w = to_grid_w - curtailed_w

        stored_wh += charge_wh_slot
        charged_wh += charge_wh_slot
        exported_wh += exported_w * INTERVAL_H
        curtailed_wh += curtailed_w * INTERVAL_H

        soc_pct = stored_wh / MAX_CAPACITY_WH * 100.0
        if not full_at and soc_pct >= 99.9:
            full_at = timestamp.strftime('%H:%M')

        rows.append({
            'time': timestamp.strftime('%H:%M'),
            'hour': round(float(hours[i]), 3),
            'pv_w': round(float(pv_w[i]), 1),
            'surplus_w': round(surplus_w, 1),
            'price': round(float(price[i]), 4),
            'limit_w': '' if limit_w < 0 else int(limit_w),
            'charge_w': round(charge_w, 1),
            'to_grid_w': round(to_grid_w, 1),
            'curtailed_w': round(curtailed_w, 1),
            'soc_pct': round(soc_pct, 2),
            'soc_baseline_pct': ('' if baseline_soc is None
                                 else round(baseline_soc[i], 2)),
            'rule_time_w': '' if time_w < 0 else int(time_w),
            'rule_price_w': '' if price_w < 0 else int(price_w),
            'rule_floor_w': int(floor_w) if floor_w else '',
            'rule_solar_cap_w': '' if solar_cap_w < 0 else int(solar_cap_w),
        })

    summary = {
        'end_soc_pct': round(stored_wh / MAX_CAPACITY_WH * 100.0, 1),
        'charged_kwh': round(charged_wh / 1000.0, 2),
        'exported_kwh': round(exported_wh / 1000.0, 2),
        'curtailed_kwh': round(curtailed_wh / 1000.0, 2),
        'full_at': full_at or '-',
    }
    return rows, summary


def write_csv(path, rows, columns):
    """Write rows to a CSV file with a stable column order."""
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# pylint: disable=too-many-locals
def main():
    """Generate all scenario CSVs."""
    default_out = os.path.join(
        os.path.dirname(__file__), '..', 'docs', 'assets', 'data',
        'peak_shaving')
    parser = argparse.ArgumentParser(description=__doc__.split('\n', maxsplit=1)[0])
    parser.add_argument('--out', default=default_out,
                        help='output directory for the CSV files')
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    day = build_day()

    # Baseline first: its SoC curve is the reference line in every chart.
    baseline_rows, baseline_summary = simulate(CONFIGURATIONS[0], day)
    baseline_soc = [row['soc_pct'] for row in baseline_rows]

    summaries = []
    for config in CONFIGURATIONS:
        name, label, time_a, price_a, solar_a = config
        if name == 'baseline':
            rows, summary = baseline_rows, baseline_summary
            for row in rows:
                row['soc_baseline_pct'] = row['soc_pct']
        else:
            rows, summary = simulate(config, day, baseline_soc=baseline_soc)

        write_csv(os.path.join(out_dir, f'{name}.csv'), rows, CSV_COLUMNS)

        summaries.append({
            'config': name,
            'label': label,
            'time_active': int(time_a),
            'price_active': int(price_a),
            'solar_cap_active': int(solar_a),
            **summary,
        })
        print(f"  {label:<26} end SoC {summary['end_soc_pct']:>5.1f} %   "
              f"charged {summary['charged_kwh']:>5.2f} kWh   "
              f"curtailed {summary['curtailed_kwh']:>4.2f} kWh   "
              f"full at {summary['full_at']}")

    write_csv(os.path.join(out_dir, 'summary.csv'), summaries,
              list(summaries[0].keys()))

    print(f"\nWrote {len(CONFIGURATIONS) + 1} CSV files to {out_dir}")


if __name__ == '__main__':
    main()
