# Dashboard Specification

This is the design contract for the batcontrol web dashboard: a single-page Vue 3 application (Vite, SFC, `<script setup>`) served by the built-in read-only HTTP API.

The dashboard is a **reader**. It polls, it renders, it never writes. Everything it needs comes from `/api/v1/...` (see [REST API](https://mastr.github.io/batcontrol/integrations/rest-api/index.md)).

## Data source

| Endpoint                     | Used for                                                              |
| ---------------------------- | --------------------------------------------------------------------- |
| `GET /api/v1/status`         | mode, control source, charge rate, last/next evaluation, online state |
| `GET /api/v1/battery`        | SoC, gross capacity, reserved energy (raw and capped), SoC limits     |
| `GET /api/v1/current`        | current price, price unit, interval width, version                    |
| `GET /api/v1/price/status`   | MIN/MAX/SPREAD over the next 24 h                                     |
| `GET /api/v1/forecast/price` | 24 h price forecast for the sparkline                                 |
| `GET /api/v1/decision`       | decision text, reason, full trace                                     |
| `GET /api/v1/version`        | version for the release link in the header                            |

`GET /api/v1/forecast` returns all four series (price, consumption, production, net_consumption) on one slot grid, so the dashboard can fetch everything in one request instead of four. The dashboard uses it when it needs more than the price.

Polling follows `next_evaluation` from `/api/v1/status` rather than a hard-coded interval, so the header countdown matches the real control cycle.

### What the API computes and what the dashboard computes

**Anything a second client would have to re-implement belongs in the service layer; pure presentation stays in the component.** A Home Assistant card, an MCP assistant or a script should not have to rebuild the 24 h price window, the gross capacity or the reservation cap to get the same numbers the dashboard shows - and two implementations of one rule drift apart.

So the API delivers:

- MIN/MAX/SPREAD/average/rank over the next 24 h (`/price/status`),
- the gross capacity (`gross_energy_capacity`), instead of each client scaling `max_energy_capacity` back up by `max_soc`,
- the reservation capped to the battery (`reserved_energy_capacity_effective`).

Values MQTT needs as well are computed in the core (`runtime_state.py`) and published to both; values only the API needs (the price statistics) are computed in `web/service.py`.

The dashboard keeps what is presentation: the sparkline's hourly means and glyphs, the box states of the SOC grid, the percent widths derived from the API's energies, formatting.

### Response shape

Every endpoint answers in the same envelope:

```
{ "ok": true, "timestamp": 1792065332218, "data": { ... } }
```

An error keeps the envelope's shape but carries a code instead of data:

```
{ "ok": false, "timestamp": 1792065332218,
  "error": "no_data_yet",
  "detail": "No completed control cycle yet. ..." }
```

The codes the dashboard has to handle: `no_data_yet` (503, before the first control cycle), `not_found` (404, e.g. an empty decision journal), and a 422 for an invalid query parameter.

### Forecast slots and paging

A forecast series carries its slots plus `total_count`, `returned_count`, `offset`, `has_more` and `next_offset`. The dashboard omits `limit` and takes the whole horizon - it draws a 24 h sparkline and needs the data anyway. The paging exists for the MCP tools, which default to 24 h to keep an assistant's context small; see the [MCP specification](https://mastr.github.io/batcontrol/development/mcp-spec/index.md).

Each slot is `{slot, time_start, time_end, value, power_w?}`. `value` is EUR/kWh for prices and **Wh per slot** for energy series; `power_w` is the average power over the slot and only appears on energy series. At 15-minute resolution those two numbers differ by a factor of four, so the dashboard must pick deliberately: Wh for energy totals, `power_w` for anything labelled W.

All values arrive **typed**: numbers are numbers, timestamps are epoch milliseconds. The entire mapping from API payload to view model happens in `src/lib/api.js -> normalize()`. Components never read raw JSON.

In development, Vite proxies `/api` to the batcontrol instance (`VITE_BACKEND_URL`, default `http://127.0.0.1:8080`), so the browser sees no CORS or private-network prompts.

## Modes

| mode | Label             | Note                 |
| ---- | ----------------- | -------------------- |
| `-1` | FORCE CHARGE      | highlighted in amber |
| `0`  | AVOID DISCHARGE   |                      |
| `8`  | LIMIT CHARGE RATE |                      |
| `10` | ALLOW DISCHARGE   |                      |

Labels come from the API, not from a table in the frontend.

## Layout

- **Mobile** (< 820 px), stacked: **DECISION** -> **SOC grid** -> **PRICE**.
- **Desktop**: SOC grid on the left, DECISION on the right with PRICE below it.
- **Header**: `BATCONTROL` followed by the version, dimmed like the eval line, linking to `https://github.com/MaStr/batcontrol/releases/tag/<version>` in a new tab (`target="_blank" rel="noopener"`; the tag carries no `v`, the link text shows `v<version>`). Then online state, time of the last evaluation, control source, charge rate in W, and the countdown to the next poll.

## Blocks

### DECISION

- `since HH:MM - X min ago` derived from the decision timestamp, ticking every second.
- The decision text as a terminal line `> ...` with a blinking cursor, below it `[decided_by.reason]`, and right-aligned the button **`>TRACE<`** (inverted on hover/active).
- `>TRACE<` expands the readable trace (`DecisionTrace.vue`, scrollable up to `max-height: 60vh`):
- Header: timestamp, kind, control source, `mode old -> new` (with mode label), `value old -> new`.
- One step per record: `#n decision -> outcome [REASON]`. The decisive one is marked `<- DECISIVE` with a bright left border, followed by `why` and the `inputs` as a key/value list.
- Values are formatted for humans:
  - Slot lists (integer arrays such as `higher_price_slots`) collapse to a clickable `>329 SLOTS<`. Expanding prints one line per slot with its from-to time (`2 Thu 15:00-15:15`). Slot 0 is the interval containing the decision timestamp, each slot is `interval_minutes` long.
  - Single slot values (`*_slot`, `recharge_window_end`) get their time in brackets.
  - `*price*` as EUR, `*energy*` and `demand` as rounded Wh, `charge_rate` and `value` as W, modes with their label.
- All `>...<` buttons share the global `.toggle` class.
- Bottom row: the **mode** label large on the left (with its own `since ...`), the **SoC** in percent large on the right.

### SOC grid (Windows 9x defragmentation look)

- 10x10 boxes for **0-99**, one per percent. **Box 0 sits bottom left**, box 10 is leftmost in the second row from the bottom, box 99 is top right. Axis labels (00, 10 ... 90) run down the left side.
- At 100 % every box is lit and none blinks. All boxes being lit from 99.1 % upwards is intentional: battery balancing takes the step from 99 to 100 % almost immediately anyway.
- Boxes carry a 3D bevel (light top/left, dark bottom/right) inside an inset frame, in green instead of the original blue.

States, combinable:

| State              | Rule                                                                  | Rendering                                                                                                      |
| ------------------ | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `charged`          | `p < ceil(soc)`                                                       | bright glowing green                                                                                           |
| `current`          | box `ceil(soc)`                                                       | blinks (87.0 % -> 87, 87.1 % -> 88)                                                                            |
| `locked`           | `p <= MIN_SOC`, or `MAX_SOC < 100` and `p > MAX_SOC`                  | dot in the centre; may still glow                                                                              |
| `reserved`         | `MIN_SOC < p` and `p - 1 < MIN_SOC + reservedCapped / capacity * 100` | diagonal hatching; bright stripes when charged, dark stripes when empty                                        |
| `always_discharge` | `p > always_allow_discharge_limit_percent`                            | same states in purple (`#c77dff`, dark `#1d0b2b`), by overriding the `--cell-*` variables on `.cell.discharge` |
| `free`             | otherwise                                                             | dark                                                                                                           |

`reservedCapped` is `reserved_energy_capacity_effective` from `/api/v1/battery`: `min(reserved_energy, capacity * (min(MAX_SOC, always_allow_discharge) - MIN_SOC))`, computed by the core (`runtime_state.cap_reserved_energy`). The reservation can exceed the battery, hence the cap; `capacity` is `gross_energy_capacity`. Every box the reservation reaches is hatched, including the blinking current one. Nothing above `always_allow_discharge` is ever hatched - discharging is always permitted there.

Legend below the grid, with exactly these names: charged, free, locked (\<= MIN / > MAX), reserved (bright and dark variant), always_discharge, current.

Below that, as text: RESERVED (raw value, plus `-> max. x kWh up to always_allow_discharge %` when capped), USABLE (`stored_usable / capacity * (MAX - MIN)`), STORED, LIMITS, ALWAYS ALLOW DISCHARGE (`above 84 %`).

### PRICE

- Current price large, in EUR/kWh, from `/api/v1/current`.
- Sparkline over the next 24 h from hourly means, drawn with the block characters `_,.-~=#` equivalents (`U+2581` to `U+2588`), axis labels now / +12h / +24h.
- MIN 24h and MAX 24h each with their time and **SPREAD 24h = max - min**, all from `/api/v1/price/status`, and **GRID_CHARGE delta = `min_dynamic_price_difference`**.

## Look: green-screen terminal

- Background near-black green `#030a05`, phosphor green `#33ff66` with a `text-shadow` glow, dimmed `#1f9e43`. Amber `#ffcc33` for FORCE CHARGE, red for errors.
- Fonts: IBM Plex Mono for text, VT323 for the large numbers. Both are **bundled with the application**, never loaded from a CDN: batcontrol runs in homes without internet egress and behind Home Assistant ingress, so an external font request would simply fail and leave the UI in a fallback face. Both are licensed under the SIL Open Font License 1.1; their license texts ship next to the font files and are served at `licenses/` (see [THIRD-PARTY-LICENSES.md](https://github.com/MaStr/batcontrol/blob/main/THIRD-PARTY-LICENSES.md)). The CSS declares an explicit local fallback stack so a missing font file degrades instead of breaking the layout.
- CRT scanlines and a vignette as a `body::after` overlay.
- Panels have a 1 px green border with the title sitting in the top border (`-- SOC --`).
- **UI is English throughout.** Decimal point, 24-hour clock (`en-GB`), relative times as `5 min ago`. Animations only while `prefers-reduced-motion` permits them.
- The defrag bevel applies **only** to the grid. Everything else stays flat terminal.

## Error and offline behaviour

- Keep the last known data visible but dimmed, with a clear banner naming the problem.
- Before the first control cycle has completed the API answers 503; the dashboard says so instead of rendering zeros.
- With `api.auth_token` configured, the dashboard asks for the token once on a 401, keeps it in `sessionStorage`, and re-asks when it stops working. It never needs the separate `api.mcp_auth_token`, which protects only `/mcp`.
- `GET /api/v1/health` is never authenticated, so a container health check works without the token. The dashboard itself takes `online` from `/status`.

## MQTT and REST

Every value batcontrol publishes on MQTT has a counterpart in the REST API (and through it in MCP). Ratios that MQTT publishes twice - as `0..1` and as percent - appear once in each REST place: as percent in `/battery`, as ratio in `/config`. Timestamps are epoch seconds on MQTT and epoch milliseconds in REST.

| MQTT topic                                                                                              | REST                                                                                                        |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `status`                                                                                                | `/health` (`stale`), `/status` (`online`)                                                                   |
| `mode`                                                                                                  | `/status` `mode`                                                                                            |
| `charge_rate`                                                                                           | `/status` `charge_rate`                                                                                     |
| `limit_battery_charge_rate`                                                                             | `/status` `limit_battery_charge_rate`                                                                       |
| `control_source`                                                                                        | `/status` `control_source`                                                                                  |
| `api_override_active`                                                                                   | `/status` `api_override_active`                                                                             |
| `discharge_blocked`                                                                                     | `/status` `discharge_blocked`                                                                               |
| `grid_charge_locked`                                                                                    | `/status` `grid_charge_locked`                                                                              |
| `evaluation_intervall`                                                                                  | `/status` `evaluation_interval_seconds`                                                                     |
| `last_evaluation`                                                                                       | `/status` `last_evaluation`                                                                                 |
| `decision`, `decision/attributes`                                                                       | `/decision` `status_text`, `event`                                                                          |
| `SOC`                                                                                                   | `/battery` `soc`                                                                                            |
| `max_energy_capacity`                                                                                   | `/battery` `max_energy_capacity`                                                                            |
| `gross_energy_capacity`                                                                                 | `/battery` `gross_energy_capacity`                                                                          |
| `stored_energy_capacity`                                                                                | `/battery` `stored_energy_capacity`                                                                         |
| `stored_usable_energy_capacity`                                                                         | `/battery` `stored_usable_energy_capacity`                                                                  |
| `reserved_energy_capacity`                                                                              | `/battery` `reserved_energy_capacity`                                                                       |
| `reserved_energy_capacity_effective`                                                                    | `/battery` `reserved_energy_capacity_effective`                                                             |
| `always_allow_discharge_limit_capacity`                                                                 | `/battery` `always_allow_discharge_limit_capacity`                                                          |
| `always_allow_discharge_limit`, `..._percent`                                                           | `/config` `battery_control.always_allow_discharge_limit`, `/battery` `always_allow_discharge_limit_percent` |
| `max_charging_from_grid_limit`, `..._percent`                                                           | `/config` `battery_control.max_charging_from_grid_limit`, `/battery` `max_charging_from_grid_limit_percent` |
| `min_grid_charge_soc`, `..._percent`                                                                    | `/config` `battery_control.min_grid_charge_soc`                                                             |
| `effective_min_grid_charge_soc`, `..._percent`                                                          | `/battery` `effective_min_grid_charge_soc_percent`                                                          |
| `min_price_difference`                                                                                  | `/config` `battery_control.min_price_difference`                                                            |
| `min_price_difference_rel`                                                                              | `/config` `battery_control.min_price_difference_rel`                                                        |
| `min_dynamic_price_difference`                                                                          | `/current` and `/price/status` `min_dynamic_price_difference`                                               |
| `production_offset`                                                                                     | `/config` `battery_control.production_offset_percent`                                                       |
| `solar_active`                                                                                          | `/solar` `solar_active`                                                                                     |
| `solar_surplus_wh`                                                                                      | `/solar` `solar_surplus_wh`                                                                                 |
| `pv_start_battery_wh`                                                                                   | `/solar` `pv_start_battery_wh`                                                                              |
| `forecast_min_battery_wh`                                                                               | `/solar` `forecast_min_battery_wh`                                                                          |
| `peak_shaving/enabled`                                                                                  | `/config` `peak_shaving.enabled`, `/peak-shaving` `enabled`                                                 |
| `peak_shaving/allow_full_battery_after`                                                                 | `/config` `peak_shaving.allow_full_battery_after`                                                           |
| `peak_shaving/price_limit`                                                                              | `/config` `peak_shaving.price_limit` (null instead of MQTT's -1)                                            |
| `peak_shaving/charge_limit`                                                                             | `/peak-shaving` `charge_limit_w`                                                                            |
| `FCST/prices`, `FCST/production`, `FCST/consumption`, `FCST/net_consumption`                            | `/forecast`, `/forecast/{series}`                                                                           |
| `inverters/<id>/SOC`, `stored_energy`, `stored_usable_energy`, `free_capacity`, `max_capacity`          | `/inverter/{id}`                                                                                            |
| `inverters/<id>/capacity`, `min_soc`, `max_soc`, `max_grid_charge_rate`, `max_pv_charge_rate` (Fronius) | `/inverter/{id}`                                                                                            |
| `inverters/<id>/em_mode`, `em_power` (Fronius)                                                          | `/inverter/{id}` `extra`                                                                                    |

Not mapped on purpose: the Home Assistant discovery messages (`homeassistant/...`) are MQTT plumbing, not values, and the `*/set` topics are inputs - the REST API is read-only.

## Out of scope

The dashboard is a reader, like the API it is built on. It has no controls that change batcontrol's behaviour - no mode buttons, no charge-rate slider, no config form. Overrides stay on the MQTT API, where Home Assistant can gate them behind its own automations and permissions. A screen that only displays cannot misfire at peak price.

## Open ideas

- `max_charging_from_grid_limit_percent` as an additional marker in the grid.
- A compact history view over the decision journal.
