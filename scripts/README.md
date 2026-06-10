# Scripts Directory

This directory contains standalone test scripts and utilities for the batcontrol project.

## Purpose

The `scripts` folder is separate from the `tests` folder to avoid interference with the automated unit test suite (pytest). These scripts are meant for:

- Manual testing and debugging
- Integration testing with real APIs
- Development utilities
- Standalone demonstrations

## Available Scripts

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
