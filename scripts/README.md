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
- Contains the candidate algorithm (`compute_solar_limit`, `merge_limits`)
  intended to move to `src/batcontrol/logic/solar_limit.py`

See `docs/development/solar-limit-evaluation.md` for results and design.

### plot_solar_limit_day.py

Generates the figures for `docs/development/solar-limit-evaluation.md` into
`docs/assets/` (clipping concept, algorithm behaviour on the reference day,
headroom explainer). Imports profiles and the candidate algorithm from
`simulate_solar_limit_day.py`.

**Usage:**
```bash
uv pip install matplotlib  # not part of the project dependencies
python scripts/plot_solar_limit_day.py
```

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
