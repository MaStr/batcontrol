#!/usr/bin/env python3
"""Constant-price day simulation with peak shaving target 14:00.

Answers the question:
    "Was passiert, wenn der Strompreis über den Tag konstant ist?
     Wie reagiert batcontrol, und wie verhaelt sich Peak-Shaving mit Zielzeit 14:00?"

The script invokes the full public LogicInterface.calculate() path for each
hour (not only the private peak shaving helper), so we see the complete
decision chain:
  - allow_discharge
  - charge_from_grid (Netzladen)
  - charge_rate (W)
  - limit_battery_charge_rate (PV-Ladelimit aus Peak-Shaving)

Two logic implementations are compared on the same synthetic day:
  1) DefaultLogic              -> preis-basierte Logik ohne Peak Shaving
  2) NextLogic  (mode='time')  -> gleiche Logik + Peak-Shaving-Ramp auf 14:00

Run:
    python scripts/simulate_constant_price_target_14.py
"""
from __future__ import annotations

import datetime
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from batcontrol.logic.common import CommonLogic
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
)
from batcontrol.logic.next import NextLogic

# ---------------------------------------------------------------------------
# Szenario
# ---------------------------------------------------------------------------
INTERVAL_MIN = 60
INTERVAL_H   = INTERVAL_MIN / 60.0

MAX_CAPACITY = 10_000          # Wh  (10 kWh Batterie)
MIN_SOC_WH   = 500             # Wh  Reserve
MAX_SOC_WH   = MAX_CAPACITY

CONSUMPTION_W  = 400
INITIAL_SOC_WH = 1_500         # ~15 % SOC morgens

TARGET_HOUR    = 14            # <-- Zielzeit fuer Peak-Shaving
FLAT_PRICE     = 0.30          # EUR/kWh - konstant ueber den ganzen Tag

# Typische deutsche Sommer-PV-Kurve (W, stuendlich)
PRODUCTION_PROFILE_W = np.array([
      0,    0,    0,    0,    0,    0,   # 00-05 Nacht
    120,  600, 1600, 3100, 4600, 5900,   # 06-11 Anstieg
   6600, 6100, 5100, 3600, 2100,  900,   # 12-17 Abstieg
    200,   10,    0,    0,    0,    0,   # 18-23 Daemmerung / Nacht
], dtype=float)

TZ = datetime.timezone.utc
BASE_DATE = datetime.datetime(2026, 6, 21, 0, 0, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------
def build_calc_input(hour: int, stored_wh: float) -> CalculationInput:
    """CalculationInput fuer den angegebenen Stunden-Slot bauen.

    Arrays sind auf den aktuellen Slot verschoben (Index 0 = aktuelle Stunde).
    Preise sind ueber den gesamten Tag konstant.
    """
    remaining = 24 - hour
    production  = PRODUCTION_PROFILE_W[hour:].copy()
    consumption = np.full(remaining, CONSUMPTION_W, dtype=float)
    prices      = np.full(remaining, FLAT_PRICE,    dtype=float)
    return CalculationInput(
        production=production,
        consumption=consumption,
        prices=prices,
        stored_energy=float(stored_wh),
        stored_usable_energy=float(max(stored_wh - MIN_SOC_WH, 0)),
        free_capacity=float(MAX_SOC_WH - stored_wh),
    )


def apply_one_hour(prod_w: float, cons_w: float,
                   settings,
                   stored_wh: float) -> tuple[float, float, float, float]:
    """Eine Stunde Batteriebetrieb simulieren.

    Beruecksichtigt die vollstaendigen Settings:
      - charge_from_grid / charge_rate   -> Netzladen
      - limit_battery_charge_rate        -> PV-Ladelimit (Peak Shaving)
      - allow_discharge                  -> Entladen erlaubt

    Returns: (charge_w, feed_in_w, grid_draw_w, new_stored_wh)
    """
    charge_limit_w = settings.limit_battery_charge_rate
    charge_from_grid = settings.charge_from_grid
    grid_charge_rate = settings.charge_rate
    allow_discharge = settings.allow_discharge

    net_surplus_w = prod_w - cons_w  # > 0 = PV-Ueberschuss

    charge_w = 0.0
    feed_in_w = 0.0
    grid_draw_w = 0.0
    new_stored_wh = stored_wh

    free_cap_wh = MAX_SOC_WH - stored_wh

    # Fall 1: Netzladen aktiv (hat Vorrang)
    if charge_from_grid and grid_charge_rate > 0:
        wanted_wh = grid_charge_rate * INTERVAL_H
        charge_wh = min(wanted_wh, free_cap_wh)
        charge_w = charge_wh / INTERVAL_H
        new_stored_wh = stored_wh + charge_wh
        # Haus-Verbrauch laeuft ueber Netz (Batterie ist "blockiert" beim Laden)
        grid_draw_w = cons_w + max(0.0, charge_w - prod_w)
        feed_in_w = max(0.0, prod_w - max(0.0, charge_w - 0.0))  # nicht praezise, Vereinfachung
        return charge_w, feed_in_w, grid_draw_w, new_stored_wh

    # Fall 2: PV-Ueberschuss verfuegbar
    if net_surplus_w > 0:
        if charge_limit_w == 0:
            charge_w = 0.0
        elif charge_limit_w > 0:
            charge_w = min(net_surplus_w, float(charge_limit_w))
        else:  # -1 = unbegrenzt
            charge_w = net_surplus_w

        charge_wh = min(charge_w * INTERVAL_H, free_cap_wh)
        charge_w = charge_wh / INTERVAL_H
        feed_in_w = net_surplus_w - charge_w
        new_stored_wh = stored_wh + charge_wh
        return charge_w, feed_in_w, 0.0, new_stored_wh

    # Fall 3: Defizit (net_surplus_w <= 0)  -> Entladen oder Netzbezug
    deficit_w = -net_surplus_w
    if allow_discharge:
        usable_wh = max(stored_wh - MIN_SOC_WH, 0)
        discharge_w = min(deficit_w, usable_wh / INTERVAL_H)
        new_stored_wh = stored_wh - discharge_w * INTERVAL_H
        grid_draw_w = deficit_w - discharge_w
    else:
        grid_draw_w = deficit_w

    return 0.0, 0.0, grid_draw_w, new_stored_wh


def run_day(logic, label: str) -> dict:
    """Einen ganzen Tag mit der angegebenen Logik simulieren."""
    soc = float(INITIAL_SOC_WH)
    totals = {
        'charged_kwh': 0.0,
        'feed_in_kwh': 0.0,
        'grid_draw_kwh': 0.0,
    }

    print()
    print("=" * 108)
    print(f"  {label}  (konstanter Preis {FLAT_PRICE:.2f} EUR/kWh, Zielzeit {TARGET_HOUR:02d}:00)")
    print("=" * 108)
    print(
        f"{'Zeit':>5}  {'PV':>5}  {'SOC':>6}  {'SOC%':>4}  "
        f"{'discharge':>9}  {'grid_chg':>8}  {'chg_rate':>8}  {'pv_limit':>8}  "
        f"{'->charge':>8}  {'feed-in':>7}  {'grid':>6}"
    )
    print("-" * 108)

    for hour in range(24):
        ts = BASE_DATE.replace(hour=hour)
        prod_w = float(PRODUCTION_PROFILE_W[hour])
        ci = build_calc_input(hour, soc)

        logic.calculate(ci, ts)
        settings = logic.get_inverter_control_settings()

        chg_w, fi_w, grid_w, soc_new = apply_one_hour(prod_w, CONSUMPTION_W, settings, soc)

        totals['charged_kwh']   += chg_w   * INTERVAL_H / 1000
        totals['feed_in_kwh']   += fi_w    * INTERVAL_H / 1000
        totals['grid_draw_kwh'] += grid_w  * INTERVAL_H / 1000

        pv_lim = settings.limit_battery_charge_rate
        pv_lim_s = f"{pv_lim:>8d}" if pv_lim >= 0 else "    none"
        grid_chg_s = "YES" if settings.charge_from_grid else " no"
        dis_s = "YES" if settings.allow_discharge else " no"

        print(
            f"{hour:02d}:00  {prod_w:>5.0f}  {soc:>6.0f}  "
            f"{soc/MAX_CAPACITY*100:>3.0f}%  "
            f"{dis_s:>9}  {grid_chg_s:>8}  {settings.charge_rate:>8.0f}  {pv_lim_s}  "
            f"{chg_w:>8.0f}  {fi_w:>7.0f}  {grid_w:>6.0f}"
        )
        soc = soc_new

    print("-" * 108)
    print(
        f"  End-SOC: {soc:.0f} Wh ({soc/MAX_CAPACITY*100:.0f} %)   "
        f"geladen {totals['charged_kwh']:.2f} kWh   "
        f"Einspeisung {totals['feed_in_kwh']:.2f} kWh   "
        f"Netzbezug {totals['grid_draw_kwh']:.2f} kWh"
    )
    return totals


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
CommonLogic._instance = None
CommonLogic.get_instance(
    charge_rate_multiplier=1.1,
    always_allow_discharge_limit=0.90,
    max_capacity=MAX_CAPACITY,
)

params_default = CalculationParameters(
    max_charging_from_grid_limit=0.79,
    min_price_difference=0.05,
    min_price_difference_rel=0.2,
    max_capacity=MAX_CAPACITY,
    peak_shaving_enabled=False,
)

params_next = CalculationParameters(
    max_charging_from_grid_limit=0.79,
    min_price_difference=0.05,
    min_price_difference_rel=0.2,
    max_capacity=MAX_CAPACITY,
    peak_shaving_enabled=True,
    peak_shaving_allow_full_after=TARGET_HOUR,
    peak_shaving_mode='time',
)

# ---------------------------------------------------------------------------
# 1) DefaultLogic
# ---------------------------------------------------------------------------
default_logic = DefaultLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
default_logic.set_calculation_parameters(params_default)
totals_default = run_day(default_logic, "DefaultLogic OHNE Peak Shaving")

# ---------------------------------------------------------------------------
# 2) NextLogic mit Peak Shaving (time, Ziel 14:00)
# ---------------------------------------------------------------------------
next_logic = NextLogic(timezone=TZ, interval_minutes=INTERVAL_MIN)
next_logic.set_calculation_parameters(params_next)
totals_next = run_day(next_logic, "NextLogic MIT Peak Shaving (time, Ziel 14:00)")

# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("  Vergleich")
print("=" * 72)
print(f"{'Szenario':<42}  {'Laden':>8}  {'Feed-in':>8}  {'Netz':>6}")
print("-" * 72)
print(
    f"{'DefaultLogic (ohne Peak Shaving)':<42}  "
    f"{totals_default['charged_kwh']:>6.2f}kWh  "
    f"{totals_default['feed_in_kwh']:>6.2f}kWh  "
    f"{totals_default['grid_draw_kwh']:>4.2f}kWh"
)
print(
    f"{'NextLogic + PeakShaving time/14:00':<42}  "
    f"{totals_next['charged_kwh']:>6.2f}kWh  "
    f"{totals_next['feed_in_kwh']:>6.2f}kWh  "
    f"{totals_next['grid_draw_kwh']:>4.2f}kWh"
)
print("=" * 72)
print()
print("Beobachtungen bei konstantem Preis:")
print("  * DefaultLogic findet keine 'high price'-Slots -> kein Netzladen.")
print("  * Entladen bleibt erlaubt (keine reservierte Energie fuer teure Slots).")
print("  * Preisgesteuerte Peak-Shaving-Modi (price/combined) werden nicht aktiv.")
print("  * Time-Modus ist preisunabhaengig: der Counter-Linear-Ramp fuellt die")
print(f"    Batterie bis {TARGET_HOUR:02d}:00 auf 100 %; ab dann keine Drosselung mehr.")
