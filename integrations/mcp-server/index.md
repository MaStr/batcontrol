# MCP Server

batcontrol can expose itself to AI assistants over the **Model Context Protocol**. With it, an assistant can answer questions about the battery in plain language:

> *Why is the battery not discharging right now?* *When is electricity cheapest today, and how much cheaper than now?* *Will the solar forecast cover the house this afternoon?*

Read-only

The assistant can read and explain, never steer. There is no tool that sets a mode, a charge rate or a parameter - deliberately: a misread forecast costs a wrong sentence, a wrong write costs a battery charged from the grid at peak price. Runtime overrides stay on the [MQTT API](https://mastr.github.io/batcontrol/integrations/mqtt-api/index.md).

## Enabling it

```
api:
  enabled: true
  host: 127.0.0.1
  port: 8080
  mcp_enabled: true       # serves /mcp
  # mcp_auth_token: ""    # token for /mcp only; empty = inherits auth_token
```

The server is reached at `http://<host>:<port>/mcp` over the streamable-HTTP transport.

### Why there is no stdio transport

Most MCP servers offer stdio, where the client starts the server process. That does not work here: a freshly started batcontrol would either open a second connection to the inverter - two controllers on one battery - or run without a control loop and have nothing to report. The useful MCP server is the one attached to the batcontrol that is already running, and that one is reachable over HTTP.

## Client configuration

Claude Desktop, Claude Code and other MCP clients take a remote server like this:

```
{
  "mcpServers": {
    "batcontrol": {
      "type": "http",
      "url": "http://batcontrol.lan:8080/mcp",
      "headers": { "Authorization": "Bearer <your mcp_auth_token>" }
    }
  }
}
```

Drop the `headers` block when no token is configured. With the default `host: 127.0.0.1` the server is only reachable from the machine batcontrol runs on; to use it from elsewhere, bind to the network **and** set a token.

## Available tools

All read-only, all reporting values from the last completed control cycle.

| Tool                                      | Answers                                                                                                |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `batcontrol_get_status`                   | Which mode is active, who set it, when the next evaluation is due                                      |
| `batcontrol_get_battery`                  | SoC, capacities, reserved energy, SoC limits                                                           |
| `batcontrol_get_current`                  | Current price together with the SoC                                                                    |
| `batcontrol_get_price_status`             | Cheapest and most expensive slot, rank of the current price, average and spread over the next 24 hours |
| `batcontrol_get_price_forecast`           | Price per slot in EUR/kWh                                                                              |
| `batcontrol_get_solar_forecast`           | Expected PV production in Wh per slot                                                                  |
| `batcontrol_get_consumption_forecast`     | Expected household consumption in Wh per slot                                                          |
| `batcontrol_get_net_consumption_forecast` | Consumption minus production; negative is surplus                                                      |
| `batcontrol_get_solar`                    | Whether there is solar surplus, and how low the battery gets until PV takes over                       |
| `batcontrol_get_peak_shaving`             | Whether peak shaving ran, was suspended by evcc, and which PV charge limit it set                      |
| `batcontrol_list_inverters`               | The inverters by id                                                                                    |
| `batcontrol_get_inverter`                 | The values of one inverter                                                                             |
| `batcontrol_get_decision`                 | Why the current mode was chosen, with the decisive rule                                                |
| `batcontrol_list_decisions`               | Recent decisions from the in-memory journal                                                            |
| `batcontrol_get_config`                   | The thresholds a decision has to be read against                                                       |
| `batcontrol_get_version`                  | Version, runtime, forecast interval, timezone                                                          |

Forecast tools default to the next **24 hours** and page for more, so an assistant is not handed several hundred slots it has to pay for. For "when is it cheapest" questions `batcontrol_get_price_status` answers in one small call without any paging.

## What it cannot tell you

Worth knowing before trusting an answer - the tools say so too, but assistants summarise:

- The solar and consumption numbers are **forecasts**, not measurements. They say nothing about what the panels produced in the past or what is running right now.
- The consumption forecast is a load profile scaled to the configured yearly consumption. It does not know that the oven is on.
- The decision journal lives in memory. After a restart it is empty, and it holds a few hundred entries at most - it is not a history.
- Before the first control cycle every tool answers "no data yet" rather than guessing.

## Security

`/mcp` carries the same data as the [REST API](https://mastr.github.io/batcontrol/integrations/rest-api/index.md) and deserves the same care. It has its own token (`api.mcp_auth_token`) so an assistant can be given access to the MCP endpoint without the REST API; left empty it inherits `api.auth_token`, so securing the API does not leave MCP open by accident.

batcontrol does not validate the `Host` header, so reverse proxies work unchanged. Without a token, a malicious web page could in principle use DNS rebinding to make your browser talk to a local endpoint - set a token whenever the endpoint is reachable from a browser or a proxy.

For the design rationale and the tool contract, see the [MCP server specification](https://mastr.github.io/batcontrol/development/mcp-spec/index.md).
