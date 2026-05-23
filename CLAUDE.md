# CLAUDE.md — batcontrol

This file documents project conventions for Claude Code sessions.
All guidelines from [`.github/copilot-instructions.md`](.github/copilot-instructions.md) apply and are the primary reference. This file extends them with Claude-specific context.

## Quick Reference

- **Run tests:** `./run_tests.sh` (uses `uv`, Python 3.13)
- **Lint target:** pylint score 9.0–9.5 (aim for 10)
- **Style:** PEP8 via `autopep8`, ASCII-only in all files
- **Python versions:** 3.9–3.13, build primary against 3.11

## Project Structure

```
src/batcontrol/           # Application source
  core.py                 # Main orchestrator
  logic/                  # Battery control decision logic (default, next)
  inverter/               # Inverter backends (Fronius HTTP, Fronius Modbus, MQTT, Dummy)
  dynamictariff/          # Tariff providers (Awattar, Tibber, EVCC, EnergyForecast, NetworkFees)
  forecastsolar/          # Solar forecast providers (FCSolar, SolarPrognose, EVCC, HA-ML)
  forecastconsumption/    # Consumption forecast (CSV, HomeAssistant)
  fetcher/                # HTTP caching helper
  scheduler.py            # Main loop scheduler
  mqtt_api.py             # MQTT state publishing
  evcc_api.py             # EVCC integration

tests/                    # pytest test suite (mirrors src/ structure)
config/                   # Config templates — add new parameters to batcontrol_config_dummy.yaml
scripts/                  # Stand-alone verification scripts (committed)
tmp/                      # Local scratch scripts — NEVER committed
docs/                     # Technical docs, wiki pages prefixed WIKI_
```

## Architecture Notes

- **Logic types:** `default` (price-based) and `next` (price-based + peak shaving). Selected via `battery_control.type` in config.
- **Inverter backends:** factory pattern in `inverter/inverter.py`. Add new backends by subclassing `InverterInterface`.
- **Tariff/Forecast providers:** same factory pattern with `*_interface.py` base classes.
- **Expert tuning:** `battery_control_expert` config block sets attributes on logic instances (`soften_price_difference_on_charging`, `charge_rate_multiplier`, etc.).
- **MQTT API:** publishes state and accepts runtime config overrides (min/max SoC, charge rate) via retained topics.

## Development Workflow

1. Activate venv: `uv venv --python 3.11 --allow-existing && uv pip install -e '.[test]'`
2. Work on a `copilot/feature-name` or `copilot/bugfix-name` branch (Claude Code uses `claude/` prefix by convention here)
3. Write pytest tests in `tests/` mirroring the module path
4. Put exploratory/verification scripts in `scripts/` or `tmp/` (never commit `tmp/`)
5. Run `./run_tests.sh` before committing
6. Add new config parameters to `config/batcontrol_config_dummy.yaml`
7. Add wiki-worthy docs to `docs/WIKI_<topic>.md`

## Testing Conventions

- Test files: `tests/batcontrol/<module>/test_<file>.py`
- Use `pytest-mock` (`mocker` fixture) for mocking, not `unittest.mock` directly
- Logic tests use helpers in `tests/batcontrol/logic/helpers.py` and `conftest.py`
- New features need tests; bug fixes need a regression test

## CI / Workflows

| Workflow | Trigger | What it checks |
|---|---|---|
| `pylint.yml` | push/PR | Lint score |
| `pytest.yml` | push/PR | Full test suite |
| `docker-image.yml` | push/PR | Docker build |
| `bump_version.yml` | manual | Version bump |
| `prepare-release.yml` | manual | Release prep |

## Version & Release

- Version in `src/batcontrol/__pkginfo__.py`, managed by `bump2version`
- Dev suffix: `0.8.1dev` → release: `0.8.1`
- Current: `0.8.1dev`

## Known Patterns & Pitfalls

- All log messages and code must be **ASCII only** — no umlauts, special chars, or emoji
- Interval data uses 15-minute resolution internally; see `interval_utils.py` and `docs/15-min-transform.md`
- Peak shaving config is nested inside calculation parameters (see `logic/next.py`)
- `§14a EnWG` dynamic network fees are handled in `dynamictariff/network_fees.py`
- The `relaxed_caching` fetcher caches API responses to avoid hammering external services
- `resilient_wrapper.py` wraps inverter calls with retry/fallback logic
