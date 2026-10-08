# CLAUDE.md — batcontrol

batcontrol charges a home battery when grid prices are cheap and preserves it for expensive
hours, based on dynamic tariffs, solar forecast, and consumption forecast. Python 3.11-3.14
(primary target: 3.14). Full contribution guidelines:
[`.github/copilot-instructions.md`](.github/copilot-instructions.md)

## Commands

```bash
./run_tests.sh                                  # full suite + coverage (creates .venv via uv)
uv venv --python 3.14 --allow-existing          # setup only
uv pip install -e '.[test]' pylint autopep8     # lint tools are not part of the test extras
uv run pytest tests/ -k <name>                  # single test / subset
uv run pylint src/batcontrol                    # target score >= 9.0 (10 if achievable)
uv run autopep8 --in-place <file>               # PEP8 formatting
```

## Module Map

```
src/batcontrol/
  core.py                 # Main orchestrator
  logic/                  # Battery control decisions (default; `next` is an alias)
  inverter/               # Backends: Fronius HTTP, Fronius Modbus, MQTT, Dummy
  dynamictariff/          # Tariff providers: Awattar, Tibber, evcc, EnergyForecast, NetworkFees
  forecastsolar/          # Solar forecast: FCSolar, evcc, HA-ML, Solcast
  forecastconsumption/    # Consumption forecast: CSV, HomeAssistant
  fetcher/                # HTTP caching helper
  scheduler.py            # Main loop
  mqtt_api.py             # State publishing + runtime config overrides
  evcc_api.py             # evcc integration
config/batcontrol_config_dummy.yaml   # Every parameter, one-line comments; also the
                                      #   first-run template copied by entrypoint.sh
tests/                    # pytest suite (mirrors package layout)
docs/                     # MkDocs user docs -> https://mastr.github.io/batcontrol/
scripts/                  # Standalone verification/helper scripts (committed)
tmp/                      # Throwaway experiments — NEVER committed
```

## Architecture

- **Logic types:** `default` holds the full logic (price-based + peak shaving). `next` is kept
  only as a backwards-compatible alias subclass of `DefaultLogic` and adds no behaviour;
  selected via `battery_control.type` in config.
- **Factory pattern:** inverter, tariff, and forecast providers all use `*_interface.py` base
  classes with a factory in `<module>.py`. New providers: implement the interface, register in
  the factory, add config keys.
- **Expert tuning:** `battery_control_expert` config block sets attributes directly on logic
  instances.
- **MQTT API:** publishes state and accepts runtime overrides (min/max SoC, charge rate) via
  retained topics.
- **Interval resolution:** 15-minute internally — see `interval_utils.py` and
  `docs/development/15-min-transform.md`.
- **Decision trace:** every control-cycle decision (discharge rule, grid recharge, peak
  shaving, solar limit, external overrides like evcc/API/grid-charge-lock) is recorded as a
  `DecisionRecord` in a `DecisionTrace` (`logic/decision_trace.py`), collected into an
  in-memory `DecisionJournal` (`decision_journal.py`) that notifies listeners on a mode or
  value change. The MQTT "Decision" sensor (`mqtt_api.py`) is the first listener. See
  `docs/development/decision-trace.md` and `docs/features/decision-sensor.md`.

## Change Checklist

1. New config parameter -> **ask the maintainer first**, do not add one on your own. Every
   parameter in `config/batcontrol_config_dummy.yaml` is permanent surface: the file is also
   the first-run template that `entrypoint.sh` copies for new Docker users, and each key has
   to be carried, mirrored into the HA add-on (step 5) and supported forever. Propose the
   parameter with its intended default and wait for a decision; a sensible hardcoded default
   or an existing key is usually the better answer than a new knob.
   Once approved, or when changing an existing parameter:
   - add/update it in `config/batcontrol_config_dummy.yaml` with a **one-line** comment
     (`# what it does, unit, Default: x`) - that file is a parameter list, not a manual, so
     the long explanation belongs in `docs/`, never there.
   - **document it under `docs/` - this is mandatory, not optional.** Put it in the matching
     page (`docs/configuration/*` for config sections, `docs/features/*` for behaviour) and
     register new pages in `mkdocs.yml`. A parameter that exists only in the YAML counts as
     undocumented and is not done.
2. New functionality -> add pytest in `tests/`; bug fix -> add a regression test for the bug.
3. User-facing behavior -> update or add a page under `docs/` and register new pages in
   `mkdocs.yml`.
4. Run `./run_tests.sh` and pylint before committing.
5. Config parameters must also be mirrored into the Home Assistant add-on repo
   (`MaStr/batcontrol_ha_addon`: `options:` + `schema:` in the add-on `config.yaml`). That repo
   ships a `port-batcontrol-change` skill which automates the steps.
6. New/changed control-flow branch that decides or overrides the inverter mode (in `core.py`
   or `logic/default.py`) -> add/extend a `DecisionRecord` (see
   `logic/decision_records.py` for the shared discharge/grid-recharge builders, `core.py`'s
   `__override_scope`/`__record_clamp` for overrides outside the logic) and a plain-language
   entry in `_REASON_EXPLANATIONS` (`logic/decision_trace.py`). Update the reason-code table in
   `docs/development/decision-trace.md`. Skipping this makes the change invisible to the
   Decision sensor and to the journal's `add_listener()` endpoint — no error is raised, so this
   is easy to miss in review.

## Releasing

Use the `release` skill (`.claude/skills/release/SKILL.md`). Short version: versions are
managed by bump-my-version (`pyproject.toml` + `src/batcontrol/__pkginfo__.py`, never edit by
hand); the `Prepare Release` workflow drops the `dev` suffix, builds the wheel, and creates a
draft GitHub release + version-bump PR; the pushed tag triggers the Docker build; afterwards
`main` is bumped to the next `dev` version and the release is promoted into the HA add-on repo
(`release-addon` skill there).

## CLI

```
python -m batcontrol [--config PATH] [--one-shot]
```

- `--one-shot` — fetch data, run the control loop once, then exit. Useful for testing. Not `--once`.

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
- `Reason` codes (`logic/decision_trace.py`) are a stable, externally consumed contract (HA
  "Decision" sensor, decision journal listeners) — add new ones rather than renaming existing
  ones. A reason without an entry in `_REASON_EXPLANATIONS` still works (falls back to a
  readable version of the code itself) but should get one.
