#!/usr/bin/env python3
"""Generate the figures for docs/development/solar-limit-evaluation.md.

Renders three PNGs into docs/assets/ visualizing the solar feed-in limit
(Solarspitzengesetz) evaluation:

  solar_limit_clipping.png  - the problem: energy above the feed-in limit
                              is curtailed unless the battery absorbs it
  solar_limit_algorithm.png - reservation cap (case A) and charge floor
                              (case B) on the reference day, SoC comparison
  solar_limit_headroom.png  - what 'headroom' means: reconstructing an
                              underestimated forecast

Requires matplotlib (not part of the project dependencies):
    uv pip install matplotlib
    python scripts/plot_solar_limit_day.py
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(__file__))

from simulate_solar_limit_day import (
    PROFILE_SOUTH_W,
    CONSUMPTION_W,
    FEED_IN_LIMIT_W,
    run_day,
)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'assets')

# Palette (validated, light mode)
C_SURFACE = '#fcfcfb'
C_PROD = '#2a78d6'      # PV surplus (actual)
C_PROD_FC = '#86b6ef'   # PV surplus (forecast, lighter step of the same hue)
C_PROD_HR = '#1c5cab'   # PV surplus (headroom-corrected, darker step)
C_BASE = '#eb6834'      # baseline trace
C_SOLAR = '#1baf7a'     # solar_cap rule trace
C_LOST = '#e34948'      # curtailed energy
C_INK = '#0b0b0b'
C_INK2 = '#52514e'
C_MUTED = '#898781'
C_GRID = '#e1e0d9'
C_AXIS = '#c3c2b7'

HOURS = np.arange(24)
SURPLUS_W = np.clip(PROFILE_SOUTH_W - CONSUMPTION_W, 0, None)

# Fine grid so curves and fill regions follow the limit-line crossings
# instead of jumping at whole-hour points.
XF = np.linspace(0, 23, 24 * 20 + 1)
SURPLUS_F = np.interp(XF, HOURS, SURPLUS_W)


def style_axis(ax, ylabel=None):
    ax.set_facecolor(C_SURFACE)
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(C_AXIS)
    ax.grid(axis='y', color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=C_MUTED, labelsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=C_INK2, fontsize=10)
    ax.margins(x=0)


def hour_axis(ax):
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f'{h:02d}:00' for h in range(0, 25, 3)])
    ax.set_xlim(0, 23)


def limit_line(ax, x0=0, x1=23):
    ax.hlines(FEED_IN_LIMIT_W, x0, x1, color=C_INK, linewidth=1.4,
              linestyle=(0, (6, 3)))


def new_figure(height):
    fig = plt.figure(figsize=(9, height), dpi=150)
    fig.patch.set_facecolor(C_SURFACE)
    return fig


def fig_clipping():
    """Figure 1: the clipping problem."""
    fig = new_figure(4.6)
    ax = fig.add_subplot(111)
    style_axis(ax, 'Power (W)')
    hour_axis(ax)

    ax.plot(XF, SURPLUS_F, color=C_PROD, linewidth=2, solid_capstyle='round')
    limit_line(ax)
    ax.fill_between(XF, np.minimum(SURPLUS_F, FEED_IN_LIMIT_W), 0,
                    color=C_PROD, alpha=0.12, linewidth=0)
    ax.fill_between(XF, SURPLUS_F, FEED_IN_LIMIT_W,
                    where=SURPLUS_F > FEED_IN_LIMIT_W,
                    color=C_LOST, alpha=0.45, linewidth=0)

    ax.annotate('clip: curtailed without a battery\n(7.5 kWh on this day)',
                xy=(13.4, 7300), xytext=(17.4, 8600), color=C_LOST,
                fontsize=10, ha='center', fontweight='bold',
                arrowprops=dict(arrowstyle='-', color=C_LOST, linewidth=1))
    ax.text(22.6, 6180, 'feed-in limit 6000 W\n(60% of 10 kWp)', color=C_INK,
            fontsize=9, ha='right', va='bottom')
    ax.text(8.1, 3050, 'PV surplus\n(production - consumption)', color=C_PROD,
            fontsize=10, ha='center', fontweight='bold')
    ax.text(17.4, 1600, 'exportable\n(below the limit)', color=C_PROD,
            fontsize=9, ha='center', alpha=0.9)

    ax.set_ylim(0, 9600)
    ax.set_title('The 60% rule: power above the feed-in limit is lost',
                 color=C_INK, fontsize=12, loc='left', pad=12)
    fig.tight_layout()
    fig.savefig(os.path.join(ASSETS_DIR, 'solar_limit_clipping.png'),
                facecolor=C_SURFACE, bbox_inches='tight')
    plt.close(fig)


def fig_algorithm():
    """Figure 2: reservation cap + floor on the reference day, SoC compare."""
    cons = np.full(24, CONSUMPTION_W, dtype=float)
    base = run_day(PROFILE_SOUTH_W, cons, 10_000, collect_rows=True)
    solar = run_day(PROFILE_SOUTH_W, cons, 10_000, solar_cap_active=True,
                    collect_rows=True)

    charge = np.array([r['charge_w'] for r in solar['rows']])
    soc_solar = np.array([r['soc_pct'] for r in solar['rows']])
    soc_base = np.array([r['soc_pct'] for r in base['rows']])

    fig = new_figure(7.2)
    ax1 = fig.add_subplot(211)
    ax2 = fig.add_subplot(212, sharex=ax1)

    # --- top: power view -------------------------------------------------
    style_axis(ax1, 'Power (W)')
    ax1.plot(XF, SURPLUS_F, color=C_PROD, linewidth=2,
             solid_capstyle='round')
    limit_line(ax1)
    ax1.fill_between(XF, SURPLUS_F, FEED_IN_LIMIT_W,
                     where=SURPLUS_F > FEED_IN_LIMIT_W,
                     color=C_LOST, alpha=0.18, linewidth=0)
    ax1.step(HOURS, charge, where='post', color=C_SOLAR, linewidth=2)
    ax1.fill_between(HOURS, charge, 0, step='post', color=C_SOLAR,
                     alpha=0.15, linewidth=0)

    ax1.annotate('case A: reservation cap\n(spread the non-reserved capacity)',
                 xy=(8.5, 660), xytext=(4.0, 3100), color=C_SOLAR, fontsize=9,
                 ha='center', fontweight='bold',
                 arrowprops=dict(arrowstyle='-', color=C_SOLAR, linewidth=1))
    ax1.annotate('case B: floor = power above the limit\n'
                 '(battery absorbs the would-be clip)',
                 xy=(12.5, 2550), xytext=(16.6, 4300), color=C_SOLAR,
                 fontsize=9, ha='center', fontweight='bold',
                 arrowprops=dict(arrowstyle='-', color=C_SOLAR, linewidth=1))
    ax1.text(9.2, 6900, 'PV surplus', color=C_PROD, fontsize=10,
             fontweight='bold', ha='center')
    ax1.text(22.6, 6180, 'feed-in limit', color=C_INK, fontsize=9, ha='right',
             va='bottom')
    ax1.text(12.5, 800, 'battery charge', color=C_SOLAR, fontsize=9,
             ha='center', fontweight='bold')
    ax1.set_ylim(0, 9600)
    ax1.tick_params(labelbottom=False)
    ax1.set_title('solar_cap rule on the reference day '
                  '(10 kWp / 6 kW limit / 10 kWh battery)',
                  color=C_INK, fontsize=12, loc='left', pad=12)

    # --- bottom: SoC view -------------------------------------------------
    style_axis(ax2, 'State of charge (%)')
    hour_axis(ax2)
    ax2.plot(HOURS, soc_base, color=C_BASE, linewidth=2,
             solid_capstyle='round')
    ax2.plot(HOURS, soc_solar, color=C_SOLAR, linewidth=2,
             solid_capstyle='round')
    ax2.set_ylim(0, 108)

    ax2.annotate('baseline: full at 11:00,\neverything above 6 kW is lost',
                 xy=(11, 99), xytext=(6.2, 72), color=C_BASE, fontsize=9,
                 ha='center', fontweight='bold',
                 arrowprops=dict(arrowstyle='-', color=C_BASE, linewidth=1))
    ax2.annotate('solar_cap: capacity reserved,\nfilled with clip energy '
                 'instead',
                 xy=(13, 62), xytext=(17.8, 40), color=C_SOLAR, fontsize=9,
                 ha='center', fontweight='bold',
                 arrowprops=dict(arrowstyle='-', color=C_SOLAR, linewidth=1))

    fig.tight_layout()
    fig.savefig(os.path.join(ASSETS_DIR, 'solar_limit_algorithm.png'),
                facecolor=C_SURFACE, bbox_inches='tight')
    plt.close(fig)


def fig_headroom():
    """Figure 3: headroom reconstructs an underestimated forecast."""
    forecast_f = SURPLUS_F / 1.25
    corrected_f = forecast_f * 1.25  # == SURPLUS_F: that is the point

    fig = new_figure(4.6)
    ax = fig.add_subplot(111)
    style_axis(ax, 'Power (W)')
    ax.set_xticks(range(8, 19, 2))
    ax.set_xticklabels([f'{h:02d}:00' for h in range(8, 19, 2)])
    ax.set_xlim(8, 18)

    ax.fill_between(XF, SURPLUS_F, FEED_IN_LIMIT_W,
                    where=SURPLUS_F > FEED_IN_LIMIT_W,
                    color=C_LOST, alpha=0.30, linewidth=0)
    ax.fill_between(XF, forecast_f, FEED_IN_LIMIT_W,
                    where=forecast_f > FEED_IN_LIMIT_W,
                    color=C_PROD_FC, alpha=0.55, linewidth=0)

    ax.plot(XF, SURPLUS_F, color=C_PROD, linewidth=2, solid_capstyle='round')
    ax.plot(XF, forecast_f, color=C_PROD_FC, linewidth=2,
            linestyle=(0, (4, 3)))
    # The corrected curve coincides with the actual one -- draw it as a
    # dotted dark line ON TOP so the reconstruction is visible.
    ax.plot(XF, corrected_f, color=C_PROD_HR, linewidth=2.4,
            linestyle=(0, (1, 3)), dash_capstyle='round')
    limit_line(ax, 8, 18)

    ax.annotate('forecast x headroom (dotted):\nreconstructs the actual '
                'surplus',
                xy=(10.3, 6450), xytext=(9.7, 8600), color=C_PROD_HR,
                fontsize=9, ha='center', fontweight='bold',
                arrowprops=dict(arrowstyle='-', color=C_PROD_HR, linewidth=1))
    ax.text(13.0, 8850, 'actual surplus', color=C_PROD, fontsize=10,
            ha='center', fontweight='bold')
    ax.text(15.9, 4600, 'forecast\n(15-25% too low)', color='#4b76ad',
            fontsize=9, ha='center', fontweight='bold')
    ax.text(17.9, 6120, 'feed-in limit', color=C_INK, fontsize=9, ha='right',
            va='bottom')
    ax.annotate('actual clip:\nfloor must allow this',
                xy=(14.0, 6700), xytext=(15.8, 8300), color=C_LOST,
                fontsize=9, ha='center', fontweight='bold',
                arrowprops=dict(arrowstyle='-', color=C_LOST, linewidth=1))
    ax.annotate('clip visible to the raw forecast:\nreservation + floor far '
                'too small',
                xy=(12.4, 6400), xytext=(10.2, 2600), color='#4b76ad',
                fontsize=9, ha='center', fontweight='bold',
                arrowprops=dict(arrowstyle='-', color='#4b76ad', linewidth=1))

    ax.set_ylim(0, 9800)
    ax.set_title("What 'headroom' does: scale the forecast surplus before "
                 'computing the clip', color=C_INK, fontsize=12, loc='left',
                 pad=12)
    fig.tight_layout()
    fig.savefig(os.path.join(ASSETS_DIR, 'solar_limit_headroom.png'),
                facecolor=C_SURFACE, bbox_inches='tight')
    plt.close(fig)


if __name__ == '__main__':
    os.makedirs(ASSETS_DIR, exist_ok=True)
    fig_clipping()
    fig_algorithm()
    fig_headroom()
    print(f'Figures written to {os.path.abspath(ASSETS_DIR)}')
