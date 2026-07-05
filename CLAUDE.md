# CLAUDE.md — batcontrol

batcontrol charges a home battery when grid prices are cheap and preserves it for expensive
hours, based on dynamic tariffs, solar forecast, and consumption forecast. Python 3.9-3.13
(primary target: 3.11). Full contribution guidelines:
[`.github/copilot-instructions.md`](.github/copilot-instructions.md)

## Commands

```bash
./run_tests.sh                                  # full suite + coverage (creates .venv via uv)
uv venv --python 3.13 --allow-existing          # setup only
uv pip install -e '.[test]'
uv run pytest tests/ -k <name>                  # single test / subset
uv run pylint src/batcontrol                    # target score >= 9.0 (10 if achievable)
uv run autopep8 --in-place <file>               # PEP8 formatting
```

## Module Map

```
src/batcontrol/
  core.py                 # Main orchestrator
  logic/                  # Battery control decisions (default, next)
  inverter/               # Backends: Fronius HTTP, Fronius Modbus, MQTT, Dummy
  dynamictariff/          # Tariff providers: Awattar, Tibber, evcc, EnergyForecast, NetworkFees
  forecastsolar/          # Solar forecast: FCSolar, SolarPrognose, evcc, HA-ML
  forecastconsumption/    # Consumption forecast: CSV, HomeAssistant
  fetcher/                # HTTP caching helper
  scheduler.py            # Main loop
  mqtt_api.py             # State publishing + runtime config overrides
  evcc_api.py             # evcc integration
config/batcontrol_config_dummy.yaml   # Reference config — every parameter documented here
tests/                    # pytest suite (mirrors package layout)
docs/                     # MkDocs user docs -> https://mastr.github.io/batcontrol/
scripts/                  # Standalone verification/helper scripts (committed)
tmp/                      # Throwaway experiments — NEVER committed
```

## Architecture

- **Logic types:** `default` (price-based) and `next` (price-based + peak shaving), selected via
  `battery_control.type` in config.
- **Factory pattern:** inverter, tariff, and forecast providers all use `*_interface.py` base
  classes with a factory in `<module>.py`. New providers: implement the interface, register in
  the factory, add config keys.
- **Expert tuning:** `battery_control_expert` config block sets attributes directly on logic
  instances.
- **MQTT API:** publishes state and accepts runtime overrides (min/max SoC, charge rate) via
  retained topics.
- **Interval resolution:** 15-minute internally — see `interval_utils.py` and
  `docs/15-min-transform.md`.

## Change Checklist

1. New/changed config parameter -> add it to `config/batcontrol_config_dummy.yaml` with an
   explanatory comment.
2. New functionality -> add pytest in `tests/`; bug fix -> add a regression test for the bug.
3. User-facing behavior -> update or add a page under `docs/` and register new pages in
   `mkdocs.yml`.
4. Run `./run_tests.sh` and pylint before committing.
5. Config parameters must also be mirrored into the Home Assistant add-on repo
   (`MaStr/batcontrol_ha_addon`: `options:` + `schema:` in the add-on `config.yaml`). That repo
   ships a `port-batcontrol-change` skill which automates the steps.

## Known Pitfalls

- ASCII-only in source code — no umlauts, special chars, emoji, even in log messages. Does not
  apply to documentation in `docs/`.
- Peak shaving config is nested inside calculation parameters (not top-level).
- `§14a EnWG` dynamic network fees live in `dynamictariff/network_fees.py`.
- `resilient_wrapper.py` wraps inverter calls — test with the wrapper, not the raw backend.
- Never commit anything from `tmp/`.
- The HA add-on Dockerfiles in `MaStr/batcontrol_ha_addon` copy `entrypoint_ha.sh`,
  `config/load_profile_default.csv`, and the `config/` folder from this repo by path — renaming
  or moving these files breaks the add-on build.
- Branch names: `copilot/feature-name` or `copilot/bugfix-name` (unless the harness assigns one).
