# Scripts Directory

This directory contains standalone test scripts and utilities for the batcontrol project.

## Purpose

The `scripts` folder is separate from the `tests` folder to avoid interference with the automated unit test suite (pytest). These scripts are meant for:

- Manual testing and debugging
- Integration testing with real APIs
- Development utilities
- Standalone demonstrations

## Available Scripts

### simulate_solar_limit_day.py

Day simulation for the proposed solar feed-in limit rule (Solarspitzengesetz,
60% feed-in cap for uncontrolled PV plants). Evaluates the "solar_cap" peak
shaving rule: reserve battery capacity before the predicted clipping window
and enforce a charge floor during it so the battery absorbs energy the
inverter would otherwise curtail.

**Usage:**
```bash
python scripts/simulate_solar_limit_day.py
```

**Features:**
- Six scenarios: reference summer day, east-west profile, small battery,
  forecast error with headroom sweep, midday consumption spike, 15-min interval
- Compares baseline, legacy time-based peak shaving, and the new rule
- Prints curtailed/feed-in energy, end SoC and clip-recovery percentage
- Contains a standalone reference copy of the algorithm (`compute_solar_limit`,
  `merge_limits`); the authoritative production implementation lives in
  `src/batcontrol/logic/solar_limit.py`

See `docs/development/solar-limit-evaluation.md` for results and design.

### plot_solar_limit_day.py

Generates the figures for `docs/development/solar-limit-evaluation.md` into
`docs/assets/` (clipping concept, algorithm behaviour on the reference day,
headroom explainer). Imports profiles and the reference algorithm from
`simulate_solar_limit_day.py`.

**Usage:**
```bash
uv pip install matplotlib  # not part of the project dependencies
python scripts/plot_solar_limit_day.py
```

### generate_peak_shaving_csv.py

Generates the datasets behind the interactive charts on
`docs/features/peak-shaving-scenarios.md`. Reads one example day plus the
scenario parameters, simulates it for every combination of the three peak
shaving rules (time / price / solar_cap, plus a no-peak-shaving baseline) and
writes one CSV per configuration.

**Input** (committed, edit these to change the scenario):

| File | Content |
|------|---------|
| `scripts/data/peak_shaving_example_day.csv` | time series: `time`, `pv_w`, `consumption_w`, `price` |
| `scripts/data/peak_shaving_example_day.yaml` | battery, rule parameters, list of configurations |

**Output** (generated, gitignored): `docs/assets/data/peak_shaving/<name>.csv`
plus `summary.csv`.

**Usage:**
```bash
python scripts/generate_peak_shaving_csv.py [--day CSV] [--params YAML] [--out DIR]
```

The `Deploy Documentation` workflow runs this before `mkdocs build`, so the
published site is always regenerated from the committed input. Run it once
locally before `mkdocs serve`, otherwise the charts report missing data.

The simulation drives the shipped implementation -- it builds a
`CalculationInput` per slot and calls `NextLogic._apply_peak_shaving` and
`NextLogic._apply_solar_limit`, the same two post-processing steps
`calculate_inverter_mode` runs -- so the published charts cannot drift away
from the actual behaviour. No plotting dependencies: the charts are rendered
in the browser by `docs/assets/js/peak-shaving-charts.js` (Chart.js, vendored
under `docs/assets/js/vendor/`).

**Scope:** the upstream discharge decision is stipulated
(`allow_discharge=True`, `charge_from_grid=False`) so the charts isolate the
peak shaving post-processing. A real run can skip the time and price rules
entirely when the battery is withheld for expensive hours.

### verify_pv_surplus_charge_limit.py

Step-by-step desk check of the PV surplus -> charge limit conversion in the
solar feed-in limit rule (`solar_cap`). The rule has to bridge two units: the
forecast arrays hold **energy per slot (Wh)**, while both the configured
feed-in limit and the resulting battery charge limit are **power values (W)**.

The script prints the per-slot Wh arithmetic next to the equivalent power
values so the conversion can be verified by hand. Every number is produced by
the real `src/batcontrol/logic/solar_limit.py` implementation -- unlike
`simulate_solar_limit_day.py`, this script does not carry its own copy of the
algorithm. Cross-checks are enforced with `assert`, so the script fails loudly
if the production code ever stops matching the expected arithmetic.

**Usage:**
```bash
python scripts/verify_pv_surplus_charge_limit.py
```

**Reference scenario:** feed-in limit 4000 W, PV 5000 W, house load 400 W
-> expected floor `(5000 - 400) - 4000 = 600 W`.

**Scenarios:**
- `B1` inside the clip slot -- floor equals the excess over the feed-in limit
- `B2` mid-slot (12:07:30) -- every Wh value halves, the floor stays 600 W
  because surplus and allowance both scale with the slot length
- `B3` scarce free capacity -- cap collapses onto the floor
- `B4` headroom 1.1 applied to the forecast surplus
- `A` clip window still ahead -- reservation cap instead of a floor
- resolution -- one physical PV curve sampled at 15 vs 60 minutes, showing how
  coarse slots average away a sharp peak
- merge -- how the floor interacts with the time/price peak shaving caps

### test_evcc.py

Standalone test script for the evcc dynamic tariff module.

**Usage:**
```bash
# From project root
python scripts/test_evcc.py <url>

# Examples
python scripts/test_evcc.py http://evcc.local/api/tariff/grid
```

**Features:**
- Tests the evcc API integration
- Shows both raw API data and processed prices
- Provides detailed error information for debugging
- Displays hourly prices with proper formatting

**Requirements:**
- Run from the project root directory
- Virtual environment should be activated or use full Python path
- pytz package must be installed

## Running Scripts

All scripts should be run from the project root directory:

```bash
# With virtual environment activated
python scripts/test_evcc.py <arguments>

# Or with full path to virtual environment Python
/path/to/venv/bin/python scripts/test_evcc.py <arguments>
```

## Adding New Scripts

When adding new standalone scripts:

1. Place them in this `scripts` directory
2. Include a shebang line: `#!/usr/bin/env python3`
3. Add proper documentation in the docstring
4. Update this README with usage information
5. Use relative imports and path manipulation to import project modules
