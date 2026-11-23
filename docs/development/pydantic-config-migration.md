# Migration plan: Pydantic-based configuration data model

Status: **stage 1 in progress** — the plan is agreed, the first section
(`battery_control` + `battery_control_expert`) is the first pull request. Tracking issue:
[#144 "Move config DataModel to Pydantic"](https://github.com/MaStr/batcontrol/issues/144).
Every pull request of the migration references issue #144.

The plan was first drafted against 0.9.x. This revision is checked against `main` at
0.11.x: Python 3.11-3.15, Pydantic already installed, the two bugs the draft found are
fixed, and the order is changed to **one section first, in warn mode**.

## What this migration is, and is not

It is **not** a code reduction. A full migration replaces a few hundred lines of
imperative validation with an estimated 1,000-1,500 lines of model and normalisation
code plus a regression corpus. The codebase gets larger.

What it buys instead: **roughly 180 scattered, untyped `config.get()` accesses across
the package collapse into declarative schemas**, validated once at startup, with one home
for every default and one place where implausible values are reported. That is
centralisation and typing, not simplification, and issue #144's "would simplify a lot of
the code" should be read that way.

The strongest argument is the class of bug that a typed model makes visible. While the
first draft was written, two such bugs were found, both shipped and both silent:

- `battery_control_expert.charge_rate_multiplier` had no effect
  ([#424](https://github.com/MaStr/batcontrol/issues/424), **closed**). The core now reads
  it from `battery_control_expert` and still accepts the old `battery_control` location
  with a deprecation warning (`core.py`, block after `max_forecast_hours`).
- `inverter.cache_ttl` never reached the MQTT inverter
  ([#425](https://github.com/MaStr/batcontrol/issues/425), **closed**). The factory now
  forwards it (`inverter/inverter.py:207`).

A third class remains open and is the reason the first section is `battery_control`
(see "Stage 1"): its five numeric values are taken via `.get()` without any check, while
the MQTT/API setters for the same values check a range. `always_allow_discharge_limit: 90`
instead of `0.9` starts, and Always-Allow never triggers.

## Why

`load_config()` in `src/batcontrol/setup.py` does `yaml.safe_load()` plus exactly one
check: `if config['pvinstallations']:` (`setup.py:70`) — a *truthiness* check, so an
empty list raises `RuntimeError('No PV Installation found')`. Everything after that
consumes a raw dictionary.

!!! note "Counting"

    `grep -rn "config\.get(\|config\[" --include="*.py" src/ | wc -l` gives 178 on
    `main`; the exact number drifts with the regex. Concentrated in
    `inverter/inverter.py` (47), `core.py` (30), `dynamictariff/dynamictariff.py` (18),
    `inverter/fronius.py` (17), `mqtt_api.py` (15) and
    `forecastconsumption/consumption.py` (13).

Since the first draft, a lot of validation has been added by hand, section by section:
`value_utils.py` now has `parse_optional_ratio`, `parse_positive_number`,
`parse_non_negative_number`, `parse_integer` and `parse_bool_config`, and the core uses
them for most of `battery_control_expert`. That makes the migration smaller, not
unnecessary: the parsers still live at the call sites, the defaults still live in the
`.get(key, default)` calls, and unknown keys (typos) are still silently ignored.

`load_config()` is now covered indirectly (`tests/batcontrol/test_main.py`), but there is
still no corpus of real configurations to test against. The regression corpus below is
the first assurance that a model does not reject what starts today.

Two components already use typed configuration objects: `PeakShavingConfig`
(`logic/logic_interface.py:20`, a mutable dataclass validated in `__post_init__`) and
`GridChargeTargetConfig` (`logic/grid_charge_target.py:15`, `frozen=True`).

## The dependency is already there

The draft's biggest risk for its first pull request was adding `pydantic` (a Rust
extension via `pydantic-core`) to the Alpine images and the piwheels index setup. That
risk is gone: the read-only web layer depends on `fastapi>=0.115` (`pyproject.toml`), and
`web/models.py` already defines its responses as Pydantic models. Every image that
starts batcontrol today already imports `pydantic` 2.x. The migration adds no
dependency and needs no new CI job for it.

`pylint` runs across 3.11-3.15 with `--fail-under 9` (`.github/workflows/pylint.yml`) and
already lints `web/models.py`; no `pylint-pydantic` plugin is needed.

Since the supported range is Python 3.11-3.15, models may use `X | None` at runtime.
The draft's "no `X | Y`, CI includes 3.9" rule no longer applies.

## Home Assistant add-on constraints

`entrypoint_ha.sh` symlinks `/data/options.json` directly as `batcontrol_config.yaml` —
JSON is a YAML subset, so it parses, but it carries every quirk of the Supervisor options
format. The same script has a second branch: a hand-written
`addon_config/batcontrol_config.yaml` is used *instead*, so "running under Home
Assistant" does **not** imply "HA-shaped config".

| # | Quirk | Example | Handled today in |
|---|-------|---------|------------------|
| 1 | Numbers can arrive as strings | `time_resolution_minutes: "60"` (from `list(15\|60)`) | `core.py:178` |
| 2 | Lists arrive as `";"`-separated strings | `history_days: "-7;-14;-21"` | `consumption.py:99` |
| 3 | No third nesting level in the schema | `consumption_forecast.csv.*` flattened to `consumption_forecast.*` | `consumption.py` |
| 4 | **Every key with a default in `options:` is always present, with a placeholder value** | `type: dummy` still ships `address`, `password` | nothing — presence checks only work because the placeholders are *values* |
| 5 | Cleared optional fields become `""`, not absent | `cafile: ""` | `value_utils.get_config_value`, `mqtt_api.configure_tls` |
| 6 | The `pvinstallations` *schema* permits every solar provider's keys in one entry | `url`, `api_token`, `resource_id`, `lat` side by side | `forecastsolar/solar.py` |
| 7 | Some keys do not exist in the add-on at all | `logfile_enabled`, `logfile_path` | defaults in `__main__.py:75-78` |

Two precisions:

- **Quirk 4 is about `options:`, not `schema:`.** Keys that appear only in `schema:` are
  genuinely absent until the user sets them (for `battery_control`: `min_grid_charge_soc`,
  `grid_charge_target_strategy`; for `battery_control_expert`:
  `market_price_refresh_time`). The conclusion holds: `extra="forbid"` is unusable, and
  presence must never be read as "configured".
- **Quirk 5 already has behaviour attached.** `mqtt_api.configure_tls` (shared by MQTT and
  evcc) treats `cafile: ""` as missing and raises, and enforces that `certfile` and
  `keyfile` are set together. An `EmptyStrNone` `BeforeValidator` must keep that meaning.

For the first section the quirks are harmless: the add-on schema types all
`battery_control` values as `float`/`int`, so they arrive as JSON numbers.

### Normalisation layer

`config/normalize.py` runs **before** validation: generic coercions (quirks 1, 2, 3, 5)
as unconditional `BeforeValidator`s — idempotent and harmless for hand-written YAML — and
type-dependent pruning (quirks 4, 6) once the discriminator is known. It is only needed
from the inverter/utility/consumption sections on, so it does not ship in stage 1.

**Hard constraint: `normalize.py` must never add a key it did not receive.** See
"Absence is information" below.

`BATCONTROL_RUNTIME_ENV` exists (`Dockerfile`, add-on Dockerfiles) and is read by
`web/service.py` for the API's runtime info. Reuse it if messages should mention add-on
UI field names — but remember the `addon_config` branch, where the value says "add-on"
while the config is hand-written YAML.

## Absence is information

This is the largest hazard of the migration. Sites across the codebase branch on whether
a key was *written*, not on its value. Once every field exists on a model with a
materialised default, they all change meaning.

The draft's most dangerous example — peak shaving's `mode` versus the three explicit
switches — is gone: `peak_shaving.mode` was removed and `PeakShavingConfig.from_config()`
now rejects it with a migration hint. The switches have plain defaults. The rule stays,
because other sites still branch on presence:

| Site | Formulation | Behaviour |
|------|-------------|-----------|
| `logic/logic.py` `parse_logic_expert_settings` (runs every cycle) | `if attribute in expert` | must stay "only if written" |
| `core.py` `charge_rate_multiplier` (expert vs. deprecated `battery_control` location) | two `Optional[float] = None` | identical — the deprecation warning depends on presence |
| `core.py` `production_offset_percent`, `preserve_min_grid_charge_soc`, `max_forecast_hours` | `if key in expert` | identical with `Optional[...] = None` |
| `consumption.py` `csv:` sub-block | `csv: CsvBlock \| None = None` | identical |
| `inverter/inverter.py` `max_charge_rate` rename, `cache_ttl` forward | `Optional[...] = None` | identical |
| `mqtt_api.py`, `evcc_api.py` credentials and `logger` | `Optional[...] = None` | identical except `username: ""` → "not sent" |
| `dynamictariff.py` "ignored" warnings and zone pairing | `is None` comparisons | identical |

Use `Optional[X] = None` **only** for fields a consumer branches on; everywhere else use
a real default. The exhaustive form is a grep
(`grep -rn "in config\|in ps\|not in " --include="*.py" src/`), re-run per pull request.

### The mechanism that enforces it

As long as models only *validate* and the code keeps reading the dict (stage 1), nothing
is materialised. Once a section's consumers read model values, the bridge back to a dict
is `model_dump(exclude_unset=True)`, never a plain `model_dump()`. A test enforces it: for
every config in the regression corpus, the key set of the dumped section equals the key
set of the input at every nesting level.

## Severity model

| Class | Behaviour | Rationale |
|-------|-----------|-----------|
| Broken structure; required field missing; value out of a range **the code already enforces** | **hard error** | Already fails today, just later and with a worse message. |
| Value of a type that the code later fails on (e.g. a string where arithmetic follows) | **error in the log** (`warn`), hard error in `strict` | Today it starts and fails in every control cycle; `warn` must not stop a config that starts. |
| **Field that nothing validates today** | **no new constraint**; implausible values warn | Where the guard rail is actually at risk. |
| Unknown key (typo) | **warning** | Silently ignored today. |
| Deprecated key (`battery_control.charge_rate_multiplier`, `inverter.max_charge_rate`, `utility.type: energyforecast_96`) | **warning + automatic migration** | Already warns or renames today. |
| Coercion required (`"60"`, `";"`-lists, `""`) | **repair silently** | The Home Assistant normal case. |
| Plausibility (`min_grid_charge_soc > max_charging_from_grid_limit`; inverter `max_soc` below `always_allow_discharge_limit`; `min_pv_charge_rate > max_pv_charge_rate`) | **warning** | Matches the existing warnings in `core.py`. |

**Binding guard rail: every configuration that starts successfully today must still start
successfully, and must produce the same control behaviour.**

The guard rail is fragile in one direction — inventing constraints that feel obviously
right. Traps found while reviewing this plan:

1. **The add-on `schema:` ranges are not a source of truth.** They are stricter than the
   code in places and wrong in others (`pvinstallations[].lon: float(,90)?` rejects
   everything east of 90°E).
2. **Model defaults come from the code's `.get(key, default)` calls, not from either YAML
   file.** Ship a table test mapping every replaced `.get(key, default)` to its model
   field and asserting equality.
3. **`Literal` is case-sensitive; the code is not.** `battery_control.type` is lowercased
   in `logic/logic.py:57`, the utility, inverter, consumption and solar factories do the
   same. `type: awattar_DE` starts today and must keep starting.
4. **`loglevel`** falls back silently (`__main__.py:94`); a `Literal[...]` would
   hard-error.
5. **`battery_control`'s five numeric fields** have no range check today — stage 1 adds
   warnings, not errors.
6. **`pvinstallations: []`** raises in `load_config()` but passes `Batcontrol.__init__()`;
   test fixtures rely on that.
7. **`tls: "true"` flips behaviour silently.** `configure_tls` uses `is not True`, so a
   string leaves TLS off today; lax coercion would switch it on.
8. **Lax coercion repairs what crashes today.** `min_price_difference: "0.05"` passes a
   lax `float` field, but the code reads the raw string and fails with `TypeError` in the
   first calculation. As long as the code reads the dict, a model must flag such values
   (strict numeric types), not silently accept them.

### Escape hatch

The add-on builds and runs the image on the user's own machine. A new hard error at
startup exits the container: the add-on does not start, the user must fix YAML through
the Supervisor UI, and the battery is uncontrolled meanwhile.

So the environment variable **`BATCONTROL_CONFIG_VALIDATION=warn|strict`** defaults to
`warn`:

- `warn`: validation problems are logged (implausible values as warnings, values the code
  will fail on as errors) and startup continues on the existing code path, which raises
  exactly where it raised before.
- `strict`: values the code cannot use stop batcontrol at startup with a message that
  names the key. Plausibility findings stay warnings.

An unknown value falls back to `warn` with a warning. `strict` becomes the default at the
earliest after a release with field experience of `warn`.

### Regression corpus

- `config/batcontrol_config_dummy.yaml` (also the first-run template) must validate with
  no warning and no error.
- The fenced YAML examples of a section in `docs/` must validate with no warning and no
  error. They are fragments, so they validate against the section model, not a whole
  configuration.
- The committed `options:` block of the add-on's `batcontrol-development/config.yaml` is
  what the Supervisor writes for a default install. A vendored copy plus a sync check
  joins the corpus once the add-on-heavy sections (inverter, utility, consumption) are
  migrated.

## Target layout

```
src/batcontrol/config/
  __init__.py        # public entry points
  validation.py      # mode switch (BATCONTROL_CONFIG_VALIDATION), logging, the hook
  battery_control.py # battery_control, battery_control_expert          (stage 1)
  normalize.py       # Home Assistant quirks, one documented block each  (stage 2+)
  common.py          # shared BeforeValidator types                      (stage 2+)
  general.py         # timezone, time_resolution_minutes, logging
  inverter.py        # dummy | fronius_gen24 | fronius-modbus | mqtt, single or list
  utility.py         # awattar_at | awattar_de | tibber | evcc | energyforecast | ...
  solar.py           # pvinstallations per provider (see caveat)
  consumption.py     # csv | homeassistant-api, both nesting forms
  messaging.py       # mqtt, evcc
```

### Caveat: `pvinstallations` has no local discriminator

The provider is selected by the **sibling top-level key `solar_forecast_provider`**, which
`Field(discriminator=...)` cannot express; it needs a root `model_validator(mode="before")`
or `ValidationInfo.context`. Cardinality also differs per provider (evcc requires exactly
one entry, Home Assistant ML reads only the first, forecast.solar iterates all).

## Sequence: one section first

The draft proposed nine pull requests that build the whole infrastructure (corpus,
normalisation, every model) before the application uses any of it. The revised order
proves the pattern on one section, ships it in `warn` mode and only then decides how to
continue.

### Stage 1 — `battery_control` + `battery_control_expert` (warn mode)

- **Hook:** `Batcontrol.__init__` calls the validator right before it reads
  `battery_control`. At that point logging is configured with the user's loglevel and
  logfile, so — unlike validating inside `load_config()` — the messages reach the
  logfile, and the inverter exists, so its `max_soc` is available for the cross-check.
- **No change to the accesses.** The models only validate; the core keeps reading the
  dict exactly as before. Defaults in the models mirror the `.get()` defaults of the core.
- **New limits are warnings only**, using the ranges the MQTT/API setters already apply:

    | Key | Warns when | Setter it mirrors |
    |-----|------------|-------------------|
    | `always_allow_discharge_limit` | outside 0..1 (e.g. `90` instead of `0.9`) | `api_set_always_allow_discharge_limit` |
    | `max_charging_from_grid_limit` | outside 0..1 | `api_set_max_charging_from_grid_limit` |
    | `min_price_difference` | below 0 | `api_set_min_price_difference` |
    | `min_price_difference_rel` | below 0 | `api_set_min_price_difference_rel` |
    | `min_recharge_amount` | below 0 | — |
    | `battery_control_expert.round_price_digits` | below 0 (rounds every price to 0) | — |
    | `battery_control_expert.production_offset_percent` | above 2.0 | `api_set_production_offset` |

- **Type problems** in the five unvalidated numeric fields (a string, an empty value) are
  logged as errors in `warn` and stop startup in `strict`; today they start and fail in
  every control cycle. A boolean is only a warning: it is an `int` subclass and every
  consumer computes with it as 0 or 1 today. Fields that the core already parses at startup keep
  their existing hard errors, the model reports them with the same parser.
- **Unknown keys** in both sections are warned about (typos are silently ignored today).
- **Cross-check:** inverter `max_soc` below `always_allow_discharge_limit` warns, because
  the Always-Allow SoC can never be reached — analogous to the existing warning for
  `min_grid_charge_soc` above `max_charging_from_grid_limit`.
- `time_resolution_minutes` given as a non-numeric string stops startup today with a bare
  `ValueError: invalid literal for int()`; it now uses `parse_integer` and names the key.
- **Tests:** dummy config and the doc examples of the two sections validate without any
  message; one test per limit for its warning; both modes; the env switch.

### Stage 2 — decide after one release

Field reports from stage 1 decide whether the pattern holds. The next sections follow the
same shape, roughly in order of risk: `general` (`timezone`, `time_resolution_minutes`,
logging), `mqtt` + `evcc`, `peak_shaving` (bridge `PeakShavingConfig` without changing its
`__post_init__` semantics), `utility`, `inverter` (single and list form), `solar` and
`consumption`. The normalisation layer and the add-on options corpus arrive with the first
add-on-heavy section. Each section is its own pull request, in `warn` mode first.

Points to settle when a section's consumers start reading the model instead of the dict:

- **`model_copy()` is not a drop-in for `dataclasses.replace()`.** `replace()` re-runs
  `__post_init__`; `model_copy(update=...)` validates nothing. The MQTT setters for peak
  shaving rely on that validation.
- **Do not use `strict=True` to reject booleans.** `strict=True` also rejects `"0.05"`,
  which `PeakShavingConfig.from_config()` converts with `float()` today. Use a
  `BeforeValidator` that rejects only `bool`.
- **`evcc.loadpoint_topic`** must keep accepting a bare string and a list
  (`evcc_api.py:104-107`).
- **Separating configuration from runtime state** (`register_set_callback` sites mutate
  config-derived attributes at runtime) stays out of scope.

### Stage 3 — strict by default, add-on reconciliation

Only after `warn` has run without false positives for a release: `strict` as the default,
and the add-on `schema:` aligned to the same limits (in `MaStr/batcontrol_ha_addon`,
against `batcontrol-development/` first, with a `version:` bump and changelog entry).

### Not in this plan: the schema generator

Generating the add-on `options:`/`schema:` from the models is cut: the Supervisor schema
has no discriminated unions, so the generated schema would be strictly weaker than the
models. A test that validates the vendored add-on `options:` block against the models
catches the drift at a fraction of the cost.

**Documentation is a per-PR deliverable, not a phase.** Every pull request that changes
startup behaviour or a parameter's meaning updates the matching page under `docs/`.

## Known costs

- **The codebase grows.** See the opening section.
- **Test fixtures break once consumers read models.** `tests/batcontrol/test_core.py`
  fixtures use minimal or mocked sections. Remedy: a valid base configuration that
  fixtures override, following `tests/batcontrol/logic/helpers.py` and
  `tests/conftest.py`.
- **More log output for existing users.** Configurations that pass silently today start
  emitting warnings. That is the point; the severity model keeps them warnings so no
  running installation stops at an update.
