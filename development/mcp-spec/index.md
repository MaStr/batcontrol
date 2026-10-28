# MCP Server Specification

Design contract for batcontrol's MCP (Model Context Protocol) server. It lets an assistant - Claude Desktop, Claude Code, a Home Assistant voice agent - answer questions about the battery in natural language: *"why is the battery not discharging right now?"*, *"when is electricity cheapest today?"*, *"has it been flapping between modes?"*

Implementation: `src/batcontrol/web/mcp.py`. Tests: `tests/batcontrol/web/test_mcp.py`.

## Scope

**Read-only, and that is a design decision, not a stage of completion.** There is no tool that sets a mode, a charge rate or a parameter. The reasoning:

- An assistant that can steer a battery can cost real money through a misread forecast or a hallucinated unit. The blast radius of a wrong read is a wrong sentence; the blast radius of a wrong write is a battery charged from the grid at peak price.
- Runtime overrides already exist on the MQTT API, where the broker authenticates them and Home Assistant can gate them behind its own automations and user permissions.
- A read-only surface needs no confirmation flow, no dry-run, no undo, and no audit trail.

A test enforces this: every tool name must start with `get` or `list` (`test_no_tool_can_change_anything`). Relaxing it is a maintainer decision, not a refactor.

## Transport

**HTTP only**, served at `/mcp` by the same application as the REST API, on the same port.

There is deliberately **no stdio transport**, even though it is the usual default for MCP servers. With stdio the client spawns the server process, and a freshly spawned batcontrol would either

- open a second connection to the inverter - two controllers on one battery - or
- run without a control loop, so every tool could only answer `available: false`.

The only useful MCP server is the one attached to the batcontrol that is already running, and that one is reachable over HTTP.

The MCP route is attached to the FastAPI router directly rather than mounted as a sub-app: a mounted app with an inner `/` route answers `/mcp` with a `307` redirect to `/mcp/`, and an exact route spares every client that extra round trip. Because attaching routes does not run the mounted app's lifespan, the FastAPI lifespan enters the MCP session manager's lifespan explicitly.

### Host header validation

None. The MCP SDK would validate the `Host` header on a loopback bind (DNS-rebinding protection), but that breaks every reverse proxy that forwards its own host name, so it is switched off explicitly - like for the REST API. `api.mcp_auth_token` (or the inherited `api.auth_token`) is the protection.

### Authentication

Two independent tokens, so the MCP token can be handed to an assistant without giving away the REST API:

| Config key           | Protects                      | Empty means                       |
| -------------------- | ----------------------------- | --------------------------------- |
| `api.auth_token`     | `/api/v1/**` except `/health` | no authentication on the REST API |
| `api.mcp_auth_token` | `/mcp`                        | inherits `api.auth_token`         |

The fallback is deliberate: securing the API must never silently leave MCP wide open. When both are empty, neither surface is authenticated - bind to loopback or put a proxy in front.

`/mcp` is a raw ASGI route, so FastAPI's dependency-based auth does not reach it; a small ASGI wrapper (`bearer_guard`) does the check with `secrets.compare_digest`. Neither token ever appears in a log line or a response.

## Tool contract

### Naming

`batcontrol_<action>_<resource>`, snake_case, action first. The service prefix is not redundant with the server name: a client that aggregates several MCP servers shows the tools in one flat list, and `get_status` from an unknown server is a worse prompt than `batcontrol_get_status`.

### The tools

| Tool                                      | Answers                                                                   | Paged |
| ----------------------------------------- | ------------------------------------------------------------------------- | ----- |
| `batcontrol_get_status`                   | Which mode is active, who set it, when the next evaluation is due         | -     |
| `batcontrol_get_battery`                  | SoC, capacities, reserved energy (raw and capped), SoC limits             | -     |
| `batcontrol_get_current`                  | Current price together with SoC, in one call                              | -     |
| `batcontrol_get_price_status`             | Cheapest/most expensive slot, rank, average and spread over the next 24 h | -     |
| `batcontrol_get_price_forecast`           | Price per slot in EUR/kWh                                                 | yes   |
| `batcontrol_get_solar_forecast`           | Expected PV production in Wh per slot                                     | yes   |
| `batcontrol_get_consumption_forecast`     | Expected consumption in Wh per slot                                       | yes   |
| `batcontrol_get_net_consumption_forecast` | Consumption minus production; negative = surplus                          | yes   |
| `batcontrol_get_solar`                    | Solar surplus indicators derived from the forecast                        | -     |
| `batcontrol_get_peak_shaving`             | Whether peak shaving ran, evcc suspension, resulting PV limit             | -     |
| `batcontrol_list_inverters`               | The inverters by id                                                       | -     |
| `batcontrol_get_inverter`                 | The values of one inverter                                                | -     |
| `batcontrol_get_decision`                 | Why the current mode was chosen, with the decisive rule                   | -     |
| `batcontrol_list_decisions`               | Recent decisions from the journal                                         | yes   |
| `batcontrol_get_config`                   | The thresholds a decision has to be read against                          | -     |
| `batcontrol_get_version`                  | Version, runtime, forecast interval, timezone                             | -     |

Every tool is annotated `read_only_hint: true`, `destructive_hint: false`, `idempotent_hint: true`, `open_world_hint: false`. Annotations are hints, not guarantees - the guarantee is that no write tool exists.

### Descriptions are the interface

A tool docstring is LLM-facing documentation, and it must state:

1. **The unit.** `Wh per slot` and `W` differ by a factor of four at 15-minute resolution. A model that has to guess answers confidently and wrongly.
1. **The sign convention.** Negative net consumption is surplus solar energy.
1. **The time base.** Timestamps are epoch milliseconds; forecast slot 0 is the interval containing the moment of the call. Slots that passed since the last control cycle are dropped.
1. **What the tool does not know.** The solar forecast is a forecast, not a measurement. The consumption forecast is a load profile, so it knows nothing about an appliance being switched on. The decision journal is in memory and empty after a restart.

Point 4 earns its place: without it a model will cheerfully report a forecast as a measurement.

### Responses

Every tool returns the same envelope, so each one has an output schema and clients get structured content instead of prose:

```
{ "available": true, "reason": null, "data": { ... } }
```

`data` reuses the REST API's Pydantic models - one definition for both transports, so units and field names cannot drift apart.

### Paging

The forecast horizon can be several hundred slots, and a model pays for every token it is handed. Paged tools therefore take `limit` and `offset` and report `total_count`, `returned_count`, `has_more` and `next_offset`.

`limit` defaults to **24 hours expressed in slots** - 24 at 60-minute, 96 at 15-minute resolution - resolved from the configuration at call time, which is why the schema default is `null` rather than a number. A model that needs more pages for it.

Ranges are enforced by the input schema, so an out-of-range value comes back as a validation error the model can read and correct, rather than being silently clamped.

### Errors

Failures are reported **inside the result**, never as a protocol error:

| Situation                           | Result                                                                                |
| ----------------------------------- | ------------------------------------------------------------------------------------- |
| No control cycle completed yet      | `available: false`, reason names the condition and says to retry after the next cycle |
| Nothing in the decision journal yet | `available: false` with that reason                                                   |
| Unexpected exception                | `available: false`, generic message pointing at the batcontrol log                    |

The last row matters: the full exception goes to the log, not into the answer. An MCP response is not the place to leak an internal detail - an exception string can contain a broker URL with credentials in it.

## Client configuration

```
{
  "mcpServers": {
    "batcontrol": {
      "type": "http",
      "url": "http://batcontrol.lan:8080/mcp",
      "headers": { "Authorization": "Bearer <api.mcp_auth_token>" }
    }
  }
}
```

With `api.host: 127.0.0.1` the server is only reachable from the same machine, which is the right setup for a client running next to batcontrol. For anything else, set a token.

## Evaluation

Per the MCP server guidance, the tool set is checked against questions a user would actually ask - read-only, realistic, verifiable, and independent of each other. See [MCP evaluation](https://mastr.github.io/batcontrol/development/mcp-evaluation/index.md).

## Open ideas

- MCP resources for the config and the dashboard spec, so an assistant can read documentation rather than infer it.
- A prompt template for "explain today's battery plan".
