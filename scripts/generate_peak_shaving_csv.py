#!/usr/bin/env python3
"""Generate the peak shaving scenario datasets for the documentation.

Reads one example day (PV, consumption, price) plus the scenario parameters
from ``scripts/data/`` and simulates it for every combination of the three
peak shaving rules. Writes one CSV per configuration into
``docs/assets/data/peak_shaving/``, which the documentation renders as
interactive charts (see ``docs/assets/js/peak-shaving-charts.js``).

Input (committed):
    scripts/data/peak_shaving_example_day.csv   time series
    scripts/data/peak_shaving_example_day.yaml  battery / rule parameters

Output (generated, not committed):
    docs/assets/data/peak_shaving/<configuration>.csv
    docs/assets/data/peak_shaving/summary.csv

The output is produced during the documentation build, so it is gitignored.
Run this once before ``mkdocs serve`` to preview the charts locally.

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
    python scripts/generate_peak_shaving_csv.py [--day CSV] [--params YAML]
                                                [--out DIR]
"""
import argparse
import csv
import datetime
import os
import sys

import numpy as np
import yaml

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

HERE = os.path.dirname(__file__)
DEFAULT_DAY = os.path.join(HERE, 'data', 'peak_shaving_example_day.csv')
DEFAULT_PARAMS = os.path.join(HERE, 'data', 'peak_shaving_example_day.yaml')
DEFAULT_OUT = os.path.join(HERE, '..', 'docs', 'assets', 'data', 'peak_shaving')

CSV_COLUMNS = [
    'time', 'hour', 'pv_w', 'surplus_w', 'price',
    'limit_w', 'charge_w', 'to_grid_w', 'curtailed_w',
    'soc_pct', 'soc_baseline_pct',
    'rule_time_w', 'rule_price_w', 'rule_floor_w', 'rule_solar_cap_w',
]


# pylint: disable=too-many-instance-attributes,too-few-public-methods
class Scenario:
    """The example day and its parameters, loaded from the input files."""

    def __init__(self, day_path, params_path):
        with open(params_path, encoding='utf-8') as handle:
            params = yaml.safe_load(handle)

        self.interval_minutes = int(params['interval_minutes'])
        self.interval_h = self.interval_minutes / 60.0

        battery = params['battery']
        self.max_capacity_wh = float(battery['max_capacity_wh'])
        self.start_soc = float(battery['start_soc'])
        self.always_allow_discharge_limit = float(
            battery['always_allow_discharge_limit'])

        shaving = params['peak_shaving']
        self.allow_full_battery_after = int(shaving['allow_full_battery_after'])
        self.price_limit = float(shaving['price_limit'])
        self.feed_in_limit_w = float(shaving['feed_in_limit_w'])
        self.feed_in_limit_headroom = float(shaving['feed_in_limit_headroom'])

        self.configurations = params['configurations']

        times, pv_w, consumption_w, price = [], [], [], []
        with open(day_path, newline='', encoding='utf-8') as handle:
            for row in csv.DictReader(handle):
                times.append(row['time'])
                pv_w.append(float(row['pv_w']))
                consumption_w.append(float(row['consumption_w']))
                price.append(float(row['price']))

        if not times:
            raise ValueError(f'No rows in {day_path}')

        self.times = times
        self.pv_w = np.array(pv_w, dtype=float)
        self.consumption_w = np.array(consumption_w, dtype=float)
        self.price = np.array(price, dtype=float)
        self.slots = len(times)
        self.hours = np.arange(self.slots) * self.interval_h

    def make_logic(self, time_active, price_active, solar_cap_active, enabled):
        """Build a NextLogic instance with the requested rule switches."""
        logic = NextLogic(timezone=datetime.timezone.utc,
                          interval_minutes=self.interval_minutes)
        logic.set_calculation_parameters(CalculationParameters(
            max_charging_from_grid_limit=0.79,
            min_price_difference=0.05,
            min_price_difference_rel=0.2,
            max_capacity=self.max_capacity_wh,
            peak_shaving=PeakShavingConfig(
                enabled=enabled,
                allow_full_battery_after=self.allow_full_battery_after,
                time_active=time_active,
                price_active=price_active,
                solar_cap_active=solar_cap_active,
                price_limit=self.price_limit,
                feed_in_limit_w=self.feed_in_limit_w,
                feed_in_limit_headroom=self.feed_in_limit_headroom,
            ),
        ))
        return logic


# The simulation deliberately calls the two post-processing steps directly so
# the charts show exactly what the shipped rules do.
# pylint: disable=protected-access,too-many-arguments,too-many-positional-arguments
def _rule_values(scenario, logic, calc_input, timestamp, stored_wh, common):
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
            peak_shaving.feed_in_limit_w, scenario.interval_h,
            calc_input.free_capacity, scenario.max_capacity_wh,
            headroom=peak_shaving.feed_in_limit_headroom,
            slot0_hours=scenario.interval_h)

    return time_w, price_w, floor_w, solar_cap_w


# pylint: disable=too-many-locals
def simulate(scenario, config, baseline_soc=None):
    """Run the example day for one configuration and return (rows, summary)."""
    time_active = bool(config['time_active'])
    price_active = bool(config['price_active'])
    solar_cap_active = bool(config['solar_cap_active'])

    enabled = time_active or price_active or solar_cap_active
    logic = scenario.make_logic(time_active, price_active, solar_cap_active,
                                enabled)

    common = CommonLogic.get_instance()
    common.max_capacity = scenario.max_capacity_wh
    common.always_allow_discharge_limit = scenario.always_allow_discharge_limit

    interval_h = scenario.interval_h
    production_wh = scenario.pv_w * interval_h
    consumption_wh = scenario.consumption_w * interval_h

    stored_wh = scenario.max_capacity_wh * scenario.start_soc
    midnight = datetime.datetime(2025, 6, 21, tzinfo=datetime.timezone.utc)

    rows = []
    charged_wh = exported_wh = curtailed_wh = 0.0
    full_at = ''

    for i in range(scenario.slots):
        free_wh = scenario.max_capacity_wh - stored_wh
        timestamp = midnight + datetime.timedelta(
            minutes=scenario.interval_minutes * i)

        calc_input = CalculationInput(
            production=production_wh[i:],
            consumption=consumption_wh[i:],
            prices=scenario.price[i:],
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
            scenario, logic, calc_input, timestamp, stored_wh, common)

        surplus_w = max(0.0, scenario.pv_w[i] - scenario.consumption_w[i])
        allowed_w = surplus_w if limit_w < 0 else min(surplus_w, float(limit_w))
        charge_wh_slot = min(allowed_w * interval_h, free_wh)
        charge_w = charge_wh_slot / interval_h

        to_grid_w = surplus_w - charge_w
        curtailed_w = max(0.0, to_grid_w - scenario.feed_in_limit_w)
        exported_w = to_grid_w - curtailed_w

        stored_wh += charge_wh_slot
        charged_wh += charge_wh_slot
        exported_wh += exported_w * interval_h
        curtailed_wh += curtailed_w * interval_h

        soc_pct = stored_wh / scenario.max_capacity_wh * 100.0
        if not full_at and soc_pct >= 99.9:
            full_at = scenario.times[i]

        rows.append({
            'time': scenario.times[i],
            'hour': round(float(scenario.hours[i]), 3),
            'pv_w': round(float(scenario.pv_w[i]), 1),
            'surplus_w': round(surplus_w, 1),
            'price': round(float(scenario.price[i]), 4),
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
        'end_soc_pct': round(stored_wh / scenario.max_capacity_wh * 100.0, 1),
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


def main():
    """Generate all scenario CSVs from the committed input files."""
    parser = argparse.ArgumentParser(
        description='Generate the peak shaving scenario datasets.')
    parser.add_argument('--day', default=DEFAULT_DAY,
                        help='input CSV with the example day time series')
    parser.add_argument('--params', default=DEFAULT_PARAMS,
                        help='input YAML with battery and rule parameters')
    parser.add_argument('--out', default=DEFAULT_OUT,
                        help='output directory for the generated CSV files')
    args = parser.parse_args()

    scenario = Scenario(args.day, args.params)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Input : {os.path.relpath(args.day)} "
          f"({scenario.slots} slots, {scenario.interval_minutes} min)")
    print(f"        {os.path.relpath(args.params)}")
    print(f"Output: {out_dir}\n")

    # Baseline first: its SoC curve is the reference line in every chart.
    baseline_config = scenario.configurations[0]
    baseline_rows, baseline_summary = simulate(scenario, baseline_config)
    baseline_soc = [row['soc_pct'] for row in baseline_rows]

    summaries = []
    for config in scenario.configurations:
        if config is baseline_config:
            rows, summary = baseline_rows, baseline_summary
            for row in rows:
                row['soc_baseline_pct'] = row['soc_pct']
        else:
            rows, summary = simulate(scenario, config, baseline_soc=baseline_soc)

        write_csv(os.path.join(out_dir, f"{config['name']}.csv"), rows,
                  CSV_COLUMNS)

        summaries.append({
            'config': config['name'],
            'label': config['label'],
            'time_active': int(bool(config['time_active'])),
            'price_active': int(bool(config['price_active'])),
            'solar_cap_active': int(bool(config['solar_cap_active'])),
            **summary,
        })
        print(f"  {config['label']:<26} end SoC {summary['end_soc_pct']:>5.1f} %   "
              f"charged {summary['charged_kwh']:>5.2f} kWh   "
              f"curtailed {summary['curtailed_kwh']:>4.2f} kWh   "
              f"full at {summary['full_at']}")

    write_csv(os.path.join(out_dir, 'summary.csv'), summaries,
              list(summaries[0].keys()))

    print(f"\nWrote {len(scenario.configurations) + 1} CSV files.")


if __name__ == '__main__':
    main()
