# CLAUDE.md — batcontrol

Primary contribution guidelines: [`.github/copilot-instructions.md`](.github/copilot-instructions.md)

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
```

## Architecture

- **Logic types:** `default` (price-based) and `next` (price-based + peak shaving), selected via `battery_control.type` in config.
- **Factory pattern:** inverter, tariff, and forecast providers all use `*_interface.py` base classes with a factory in `<module>.py`.
- **Expert tuning:** `battery_control_expert` config block sets attributes directly on logic instances.
- **MQTT API:** publishes state and accepts runtime overrides (min/max SoC, charge rate) via retained topics.
- **Interval resolution:** 15-minute internally — see `interval_utils.py` and `docs/15-min-transform.md`.

## CLI

```
python -m batcontrol [--config PATH] [--one-shot]
```

- `--one-shot` — fetch data, run the control loop once, then exit. Useful for testing. Not `--once`.

## Known Pitfalls

- ASCII-only in source code — no umlauts, special chars, emoji, even in log messages. Does not apply to documentation in `docs/`.
- Peak shaving config is nested inside calculation parameters (not top-level).
- `§14a EnWG` dynamic network fees live in `dynamictariff/network_fees.py`.
- `resilient_wrapper.py` wraps inverter calls — test with the wrapper, not the raw backend.
- Never commit anything from `tmp/`.
