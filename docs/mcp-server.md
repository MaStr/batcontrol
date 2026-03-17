# MCP Server — Architecture & Integration Guide

## Overview

Batcontrol exposes an MCP (Model Context Protocol) server that enables AI clients
(Claude Desktop, Claude Code, HA voice assistants) to query system state, inspect
forecasts, understand decisions, and manage battery overrides via natural language.

The MCP server runs **in-process** as a daemon thread alongside the main evaluation
loop, sharing direct access to the `Batcontrol` instance — same pattern as `MqttApi`.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Batcontrol Process                         │
│                                             │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Main     │  │ Scheduler │  │ MQTT API │ │
│  │ Loop     │  │ Thread    │  │ Thread   │ │
│  └────┬─────┘  └───────────┘  └──────────┘ │
│       │                                     │
│       ▼                                     │
│  ┌──────────────────────────────────────┐   │
│  │ Batcontrol Core Instance             │   │
│  │  (forecasts, state, inverter ctrl)   │   │
│  └──────────┬───────────────────────────┘   │
│             │                               │
│       ┌─────┴─────┐                         │
│       ▼           ▼                         │
│  ┌──────────┐ ┌──────────────┐              │
│  │ Override  │ │ MCP Server   │              │
│  │ Manager  │ │ (HTTP/stdio) │              │
│  └──────────┘ └──────────────┘              │
│                    ▲                         │
│                    │ HTTP :8081              │
└────────────────────┼────────────────────────┘
                     │
         MCP Clients (Claude Desktop,
          HA voice, custom dashboards)
```

## Files

| File | Role |
|------|------|
| `src/batcontrol/mcp_server.py` | MCP server class, tool definitions, transport setup |
| `src/batcontrol/override_manager.py` | Duration-based override state machine |
| `src/batcontrol/core.py` | Integration: init, shutdown, `_apply_override()`, `api_get_decision_explanation()` |
| `src/batcontrol/__main__.py` | `--mcp-stdio` CLI flag |
| `src/batcontrol/logic/logic_interface.py` | `CalculationOutput.explanation` field |
| `src/batcontrol/logic/default.py` | `_explain()` annotations throughout decision logic |

## Configuration

```yaml
# In batcontrol_config.yaml
mcp:
  enabled: false
  transport: http          # 'http' for network, 'stdio' for pipe
  host: 0.0.0.0            # Bind address
  port: 8081               # HTTP port
```

CLI alternative for stdio transport:
```bash
python -m batcontrol --mcp-stdio --config config/batcontrol_config.yaml
```

## MCP Tools Reference

### Read Tools (9)

| Tool | Returns | Key Fields |
|------|---------|------------|
| `get_system_status` | Current mode, SOC, charge rate, override | `mode`, `soc_percent`, `override` |
| `get_price_forecast` | Electricity prices per interval | `prices[]`, `current_price` |
| `get_solar_forecast` | PV production in W per interval | `production_w[]` |
| `get_consumption_forecast` | Household consumption in W | `consumption_w[]` |
| `get_net_consumption_forecast` | Consumption minus production | `net_consumption_w[]` |
| `get_battery_info` | SOC, capacity, stored/reserved energy | `soc_percent`, `max_capacity_wh` |
| `get_decision_explanation` | Step-by-step reasoning from last eval | `explanation_steps[]` |
| `get_configuration` | Runtime parameters | `min_price_difference`, limits |
| `get_override_status` | Active override details | `active`, `override{}` |

### Write Tools (4)

| Tool | Parameters | Effect |
|------|-----------|--------|
| `set_mode_override` | `mode` (-1,0,8,10), `duration_minutes`, `reason` | Time-bounded mode override |
| `clear_mode_override` | — | Cancel override, resume autonomous |
| `set_charge_rate` | `charge_rate_w`, `duration_minutes`, `reason` | Force charge at rate |
| `set_parameter` | `parameter` name, `value` | Adjust runtime config |

#### `set_parameter` valid parameters:
- `always_allow_discharge_limit` (0.0–1.0)
- `max_charging_from_grid_limit` (0.0–1.0)
- `min_price_difference` (≥ 0.0, EUR)
- `min_price_difference_rel` (≥ 0.0)
- `production_offset` (0.0–2.0)

## Mode Constants

| Value | Name | Meaning |
|-------|------|---------|
| `-1` | Force Charge from Grid | Charge battery from grid at configured rate |
| `0` | Avoid Discharge | Hold charge, allow PV charging |
| `8` | Limit PV Charge Rate | Allow discharge, cap PV charge rate |
| `10` | Allow Discharge | Normal operation, discharge when optimal |

## Dependencies

- `mcp>=1.0` — Official MCP Python SDK (includes FastMCP, uvicorn, starlette)
- Docker: port 8081 exposed in Dockerfile
