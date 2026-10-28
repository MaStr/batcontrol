# REST API

batcontrol can serve a **read-only HTTP API** next to its control loop: the current inverter mode, the battery, the forecasts it decides on, and the decision trace explaining why it chose what it chose. The same process also serves the [dashboard](https://mastr.github.io/batcontrol/features/dashboard/index.md) and the [MCP server](https://mastr.github.io/batcontrol/integrations/mcp-server/index.md), on one port.

Read-only by design

Every endpoint is a `GET`. There is no way to set a mode, a charge rate or a parameter through this API, and a test fails the build if one ever appears. Runtime overrides stay on the [MQTT API](https://mastr.github.io/batcontrol/integrations/mqtt-api/index.md), where the broker authenticates them.

## Enabling it

```
api:
  enabled: true           # off by default
  host: 127.0.0.1         # 0.0.0.0 to reach it from the network
  port: 8080
  dashboard_enabled: true
  docs_enabled: false     # Swagger UI at /docs and the schema at /openapi.json
  mcp_enabled: false
  # auth_token: ""        # bearer token for /api/v1; empty = no authentication
  # mcp_auth_token: ""    # separate token for /mcp; empty = inherits auth_token
```

In Docker, publish the port and bind to `0.0.0.0`:

```
services:
  batcontrol:
    ports:
      - "8080:8080"
```

With the Home Assistant add-on the dashboard is reached through ingress and needs no published port - see the add-on documentation.

## Conventions

These four hold everywhere in the API. Most integration mistakes come from one of them:

|                |                                                                                                                                                                                                |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Timestamps** | epoch **milliseconds**. The MQTT API uses seconds - do not mix them up.                                                                                                                        |
| **Energy**     | **Wh per forecast slot**. Energy series additionally carry `power_w`, the average power over the slot. At 15-minute resolution those differ by a factor of **four**.                           |
| **Prices**     | EUR/kWh, 4 decimals.                                                                                                                                                                           |
| **Slot 0**     | is the interval containing the **current time**. Slots that have passed since the last control cycle are not returned, so `total_count` shrinks until the next cycle publishes a new forecast. |

Every response is wrapped in the same envelope:

```
{ "ok": true, "timestamp": 1792065332218, "data": { } }
```

An error keeps the shape and carries a code instead of data:

```
{ "ok": false, "timestamp": 1792065332218,
  "error": "no_data_yet",
  "detail": "No completed control cycle yet. The first forecast is available after the first evaluation." }
```

| Code               | Status | Meaning                                                                                                     |
| ------------------ | ------ | ----------------------------------------------------------------------------------------------------------- |
| `no_data_yet`      | 503    | batcontrol has not completed its first control cycle                                                        |
| `forecast_expired` | 503    | every slot of the last forecast has passed - control cycles keep failing (e.g. the tariff provider is down) |
| `not_found`        | 404    | unknown forecast series or inverter id, or an empty decision journal                                        |
| `unauthorized`     | 401    | `api.auth_token` is set and the bearer token was missing or wrong                                           |

## Endpoints

| Endpoint                             | Returns                                                                                                                                                                                    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET /api/v1/health`                 | liveness, age of the last cycle, whether data exists yet                                                                                                                                   |
| `GET /api/v1/version`                | version, build, runtime, forecast interval, timezone                                                                                                                                       |
| `GET /api/v1/status`                 | mode, control source, charge rate, overrides, next evaluation                                                                                                                              |
| `GET /api/v1/battery`                | SoC, capacities, reserved energy (raw and capped to the battery), SoC limits                                                                                                               |
| `GET /api/v1/current`                | current price together with the SoC                                                                                                                                                        |
| `GET /api/v1/price/status`           | price statistics for the next 24 hours: current price and its rank, cheapest and most expensive slot, average, spread                                                                      |
| `GET /api/v1/forecast`               | all four forecast series on one slot grid                                                                                                                                                  |
| `GET /api/v1/forecast/{series}`      | `price`, `consumption`, `production` or `net_consumption`                                                                                                                                  |
| `GET /api/v1/solar`                  | solar surplus indicators derived from the forecast (see [forecast metrics](https://mastr.github.io/batcontrol/integrations/forecast-metrics/index.md))                                     |
| `GET /api/v1/decision`               | the latest decision trace and its plain-language explanation                                                                                                                               |
| `GET /api/v1/decision/history`       | recent decisions from the in-memory journal                                                                                                                                                |
| `GET /api/v1/peak-shaving`           | what [peak shaving](https://mastr.github.io/batcontrol/features/peak-shaving/index.md) did in the last cycle: active or suspended by evcc, resulting PV charge limit, outcome of its rules |
| `GET /api/v1/inverters`              | the inverters by id                                                                                                                                                                        |
| `GET /api/v1/inverter/{inverter_id}` | the values of one inverter, as read in the last control cycle                                                                                                                              |
| `GET /api/v1/config`                 | the runtime parameters behind the decisions, and which of them are overridden at runtime                                                                                                   |

Forecast endpoints accept `limit` and `offset` and report `total_count`, `has_more` and `next_offset`, so a long horizon can be paged.

### Price status

`/price/status` covers the **next 24 hours from the current slot**, independent of how far the optimizer looks ahead. Every value is per slot at the configured resolution (`interval_minutes`): `min` is the cheapest slot, so there is no separate "cheapest window". `rank` is the position of the current price in the window, 1 being the cheapest; equal prices share a rank. When the tariff provider's prices end before the 24 hours do, `complete` is `false` and `slot_count` says how many slots the statistics cover.

### Runtime overrides in `/config`

The fields of `/config` always show the value **in effect**. Several of them can change at runtime - via MQTT, through evcc, by the grid charge lock (which sets `max_charging_from_grid_limit` to 0) or by the control loop itself (it lowers `max_charging_from_grid_limit` below `always_allow_discharge_limit`). `overrides` lists every parameter whose effective value differs from the configuration file:

```
"overrides": {
  "peak_shaving.enabled": { "configured": true, "effective": false }
}
```

It is empty when nothing is overridden. A temporary suspension of peak shaving by evcc is not a parameter change and shows in `/peak-shaving` (`suspended_by`) instead.

### Inverters

With several inverters every other endpoint reports them as **one** battery. `/inverters` lists their ids; `/inverter/{id}` returns the values of a single one - the same values the MQTT API publishes under `inverters/<id>/`. Each inverter reads them once per control cycle and keeps them, so a request never queries the inverter. Backend specific values (e.g. `em_mode` and `em_power` of a Fronius GEN24) are in `extra`.

### MQTT and REST

Every value batcontrol publishes on MQTT is also available here. The mapping is listed in the [dashboard specification](https://mastr.github.io/batcontrol/development/dashboard-spec/#mqtt-and-rest).

With `docs_enabled: true` a running instance serves its own interactive documentation at `/docs` (Swagger UI) and the raw schema at `/openapi.json`. Both are **off by default**: an installation does not need to describe its API to every visitor, and the same schema is rendered at the end of this page. Switch it on while you develop against the API. Swagger UI is served from the package rather than from a CDN, so it works on a box without internet access - which is also why there is no ReDoc: one documentation UI that works offline beats two that need the network.

## Examples

```
# What is it doing, and why?
curl -s localhost:8080/api/v1/status | jq '.data.mode_label'
curl -s localhost:8080/api/v1/decision | jq -r '.data.status_text'

# Cheapest slot of the next 24 hours
curl -s localhost:8080/api/v1/price/status | jq '.data.min'

# The next six price slots
curl -s 'localhost:8080/api/v1/forecast/price?limit=6' \
  | jq -r '.data.slots[] | "\(.time_start|tonumber/1000|strftime("%H:%M"))  \(.value) EUR/kWh"'

# With a token
curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/v1/battery
```

## Security

The payload is read-only, but it still describes a household: how full the battery is, what the consumption pattern looks like, when nobody is home.

- The default bind is `127.0.0.1` - reachable only from the same machine.
- `api.auth_token` adds a bearer token on `/api/v1/**`. `/api/v1/health` stays open so container health checks keep working.
- `api.mcp_auth_token` protects `/mcp` separately, so an assistant can be given MCP access without the REST API. Left empty it inherits `auth_token`, so securing the API never leaves MCP open by accident.
- There is no TLS and no rate limiting. For access from outside the local network, put a reverse proxy in front of it.
- **Behind a reverse proxy, set `api.auth_token`.** batcontrol does not check the `Host` header (REST, dashboard and `/mcp` alike) - that would break a proxy that forwards its own host name. The flip side is that without a token a malicious web page could use DNS rebinding to make your browser read the API. A token closes that.
- `GET /api/v1/config` is an explicit allow-list of behavioural parameters built from runtime values. It does not read the configuration file, so inverter passwords, tariff tokens and MQTT credentials are not reachable through it.

## API reference
