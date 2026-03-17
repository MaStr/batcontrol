# MCP Server for Batcontrol — Implementation Plan

## Motivation

Batcontrol is a powerful home battery optimization system, but it lacks an interactive
query interface. Users can only observe behavior via MQTT topics or log files. There is
no way to ask "why did you charge at 3am?" or "what's the forecast for tonight?".

An MCP (Model Context Protocol) server turns batcontrol into a system you can have a
conversation with — through any MCP-compatible AI client (Claude Desktop, Claude Code,
Home Assistant voice assistants, etc.).

Additionally, the current override mechanism (`api_overwrite`) is **single-shot**: it
only survives one evaluation cycle (~3 minutes) before the autonomous logic takes back
control. This makes manual overrides essentially meaningless. The MCP server work
includes fixing this with a proper duration-based override system that benefits both
MCP and the existing MQTT API.

---

## Architecture Decision: Transport & Deployment

### Transport: Streamable HTTP (primary), stdio (secondary)

**Why Streamable HTTP over stdio:**
- The HA addon runs batcontrol in a Docker container — stdio MCP requires the client
  to spawn the server process, which doesn't work across container boundaries
- Streamable HTTP allows the MCP server to run inside the existing batcontrol process
  and accept connections over the network (localhost or LAN)
- Home Assistant addons can expose ports — a single HTTP port is simple to configure
- Multiple clients can connect simultaneously (HA dashboard + Claude Desktop)
- Streamable HTTP is the current MCP standard, replacing the deprecated SSE transport

**stdio as secondary option:**
- Useful for local development and testing
- Can be offered as a CLI flag (`--mcp-stdio`) for direct integration with tools
  like Claude Desktop when running batcontrol outside Docker

### Deployment Model

```
┌─────────────────────────────────────────────┐
│  Batcontrol Process (existing)              │
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

The MCP server runs **in-process** as an additional thread, sharing direct access to
the `Batcontrol` instance — same pattern as `MqttApi`.

---

## Phase 1: Duration-Based Override Manager

**Problem:** `api_overwrite` is a boolean flag reset after one evaluation cycle.

**Solution:** A standalone `OverrideManager` class that both MCP and MQTT can use.

### Design

```python
class OverrideManager:
    """Manages time-bounded overrides for batcontrol operation."""

    def set_override(self, mode: int, duration_minutes: int,
                     charge_rate: int = None, reason: str = "") -> OverrideState
    def clear_override() -> None
    def get_override() -> Optional[OverrideState]  # None if expired/inactive
    def is_active() -> bool
    def remaining_minutes() -> float
```

### Behavior
- Override has a **mode**, **duration**, optional **charge_rate**, and a **reason**
- `core.py:run()` checks `override_manager.is_active()` instead of `api_overwrite`
- If active: apply the override's mode/rate, skip autonomous logic
- If expired: resume autonomous logic automatically
- Override can be cleared early via `clear_override()`
- MQTT `mode/set` uses this manager with a configurable default duration
- MCP tools specify duration explicitly

### Integration Points
- `core.py`: Replace `api_overwrite` flag with `OverrideManager` queries
- `mqtt_api.py`: Route `mode/set` through `OverrideManager` (backward compatible)
- New file: `src/batcontrol/override_manager.py`

### Files to Create/Modify
- [ ] `src/batcontrol/override_manager.py` — New: OverrideManager class
- [ ] `src/batcontrol/core.py` — Modify: integrate OverrideManager
- [ ] `src/batcontrol/mqtt_api.py` — Modify: use OverrideManager for mode/set
- [ ] `tests/batcontrol/test_override_manager.py` — New: unit tests

---

## Phase 2: MCP Server Implementation

### MCP Tools (Read Operations)

| Tool | Description | Data Source |
|------|-------------|-------------|
| `get_system_status` | Current mode, SOC, charge rate, override status, last evaluation time | `core.py` state |
| `get_price_forecast` | Hourly/15-min electricity prices for next 24-48h | `dynamictariff` provider |
| `get_solar_forecast` | Expected PV production (W per interval) | `forecastsolar` provider |
| `get_consumption_forecast` | Expected household consumption (W per interval) | `forecastconsumption` provider |
| `get_net_consumption_forecast` | Consumption minus production (grid dependency) | Computed from above |
| `get_battery_info` | SOC, capacity, stored energy, reserved energy, free capacity | Inverter + logic |
| `get_decision_explanation` | Why the system chose the current mode, with price/forecast context | Logic output + state |
| `get_configuration` | Current runtime config (thresholds, limits, offsets) | `core.py` config state |
| `get_override_status` | Active override details: mode, remaining time, reason | `OverrideManager` |

### MCP Tools (Write Operations)

| Tool | Description | Safety |
|------|-------------|--------|
| `set_mode_override` | Override mode for N minutes (with reason) | Duration-bounded, auto-expires |
| `clear_mode_override` | Cancel active override, resume autonomous logic | Safe — restores normal operation |
| `set_charge_rate` | Set charge rate (implies force-charge mode) for N minutes | Duration-bounded |
| `set_parameter` | Adjust runtime parameter (discharge limit, price threshold, etc.) | Validated, temporary |

### MCP Resources (optional, later)

Resources provide ambient context without explicit tool calls:
- `batcontrol://status` — Live system status summary
- `batcontrol://forecasts` — Current forecast data

### Implementation

**New files:**
- `src/batcontrol/mcp_server.py` — MCP server class using the `mcp` Python SDK
- Registers tools, handles requests, bridges to `Batcontrol` instance

**MCP SDK:** Use the official `mcp` Python package (PyPI: `mcp`), which provides:
- `FastMCP` high-level server class
- Streamable HTTP and stdio transports built-in
- Tool/resource/prompt decorators

**Thread model:**
- MCP HTTP server runs in its own thread (like MQTT)
- Tool handlers access `Batcontrol` instance (read-mostly, writes via `OverrideManager`)
- Thread safety: Read operations on forecast arrays use existing locks; write
  operations go through `OverrideManager` which has its own lock

### Files to Create/Modify
- [ ] `src/batcontrol/mcp_server.py` — New: MCP server implementation
- [ ] `src/batcontrol/core.py` — Modify: initialize MCP server, expose getters
- [ ] `src/batcontrol/__main__.py` — Modify: add `--mcp-stdio` flag, MCP config
- [ ] `pyproject.toml` — Modify: add `mcp` dependency
- [ ] `config/batcontrol_config_dummy.yaml` — Modify: add MCP config section
- [ ] `tests/batcontrol/test_mcp_server.py` — New: MCP server tests

---

## Phase 3: Decision Explainability

The `get_decision_explanation` tool is the killer feature. It requires the logic
engine to produce human-readable rationale alongside its control output.

### Design
- Extend `CalculationOutput` with a `explanation: list[str]` field
- Logic engine appends reasoning steps as it evaluates:
  - "Current price (0.15 EUR/kWh) is below average (0.22 EUR/kWh)"
  - "Solar forecast shows 3.2 kWh production in next 4 hours"
  - "Battery at 45% SOC with 2.1 kWh reserved for evening peak"
  - "Decision: Allow discharge — prices are above threshold and battery has surplus"
- MCP tool formats this into a structured response

### Files to Modify
- [ ] `src/batcontrol/logic/default.py` — Add explanation accumulation
- [ ] `src/batcontrol/logic/logic_interface.py` — Add explanation to output type
- [ ] `src/batcontrol/core.py` — Store and expose last explanation
- [ ] `tests/batcontrol/logic/test_default.py` — Test explanation output

---

## Phase 4: Home Assistant Addon Integration

The HA addon is maintained in a separate repository (`batcontrol_ha_addon`). The MCP
server integration requires changes in **both** repositories.

### Changes in This Repository (batcontrol)

1. **Configuration section** in `batcontrol_config_dummy.yaml`:
   ```yaml
   mcp:
     enabled: false
     transport: http        # 'http' or 'stdio'
     host: 0.0.0.0          # bind address
     port: 8081             # HTTP port
     # auth_token: ""       # optional bearer token for security
   ```

2. **Entrypoint** (`entrypoint_ha.sh`): No changes needed — config flows through YAML

### Changes Needed in batcontrol_ha_addon Repository

1. **Port exposure** in addon `config.yaml` / `manifest.json`:
   ```yaml
   ports:
     "8081/tcp": 8081
   ports_description:
     "8081/tcp": "MCP Server (Model Context Protocol)"
   ```

2. **Options schema** — Add MCP toggle to addon options:
   ```json
   {
     "mcp_enabled": true,
     "mcp_port": 8081
   }
   ```

3. **Ingress support** (optional, future): HA supports addon ingress for web-based
   interfaces. The MCP HTTP endpoint could be exposed through HA's ingress proxy,
   providing automatic authentication.

### How MCP Fits the HA Ecosystem

```
┌─────────────────────────────────────────────┐
│ Home Assistant                              │
│                                             │
│  ┌──────────┐  ┌────────────────────────┐   │
│  │ HA Core  │  │ Batcontrol Addon       │   │
│  │          │  │                        │   │
│  │  MQTT ◄──┼──┤  MQTT API (existing)   │   │
│  │  Broker  │  │                        │   │
│  │          │  │  MCP Server :8081 (new) │   │
│  └──────────┘  └───────────┬────────────┘   │
│                            │                │
│  ┌─────────────────────────┼──────────────┐ │
│  │ HA Voice / Assist       │              │ │
│  │ (future MCP client)     ▼              │ │
│  │              MCP over HTTP             │ │
│  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
         │
         │ MCP over HTTP (LAN)
         ▼
  Claude Desktop / other MCP clients
```

**Key integration points:**
- MQTT remains the primary HA integration (dashboards, automations, sensors)
- MCP adds a **conversational** layer — ideal for voice assistants and AI agents
- Both share the same `Batcontrol` instance and `OverrideManager`
- HA's future native MCP support could make this seamless (HA is exploring MCP)

---

## Phase 5: Testing & Documentation

- [ ] Unit tests for `OverrideManager` (expiry, clear, concurrent access)
- [ ] Unit tests for MCP tools (mock `Batcontrol` instance)
- [ ] Integration test: MCP client → server → override → evaluation cycle
- [ ] Update `README.MD` with MCP section
- [ ] Update `config/batcontrol_config_dummy.yaml` with MCP config example
- [ ] Document MCP tools and their parameters

---

## Task Checklist

### Phase 1: Override Manager
- [ ] Design and implement `OverrideManager` class
- [ ] Write unit tests for `OverrideManager`
- [ ] Integrate `OverrideManager` into `core.py` (replace `api_overwrite`)
- [ ] Update MQTT `mode/set` to use `OverrideManager`
- [ ] Test backward compatibility with existing MQTT overrides

### Phase 2: MCP Server Core
- [ ] Add `mcp` dependency to `pyproject.toml`
- [ ] Implement `mcp_server.py` with read-only tools
- [ ] Add write tools (`set_mode_override`, `clear_mode_override`, `set_charge_rate`)
- [ ] Add `set_parameter` tool for runtime config changes
- [ ] Initialize MCP server from `core.py` (same pattern as MQTT)
- [ ] Add `--mcp-stdio` CLI flag to `__main__.py`
- [ ] Add MCP config section to config YAML
- [ ] Write MCP server unit tests

### Phase 3: Decision Explainability
- [ ] Extend logic output with explanation field
- [ ] Add explanation accumulation in `DefaultLogic.calculate()`
- [ ] Expose last explanation via `core.py` getter
- [ ] Implement `get_decision_explanation` MCP tool
- [ ] Test explanation output

### Phase 4: HA Addon Integration
- [ ] Document required changes for `batcontrol_ha_addon` repository
- [ ] Add MCP port to Dockerfile EXPOSE
- [ ] Test MCP server in Docker container
- [ ] Test MCP connectivity from external client

### Phase 5: Testing & Documentation
- [ ] End-to-end test: client → MCP → override → evaluation
- [ ] Update README with MCP documentation
- [ ] Update dummy config with MCP section
- [ ] Verify no regressions in existing tests

---

## Dependencies

- `mcp>=1.0` — Official MCP Python SDK (includes FastMCP, transports)
- No other new dependencies required (HTTP server is built into `mcp` SDK via `uvicorn`/`starlette`)

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Thread safety between MCP and main loop | OverrideManager uses threading.Lock; read tools access immutable snapshot data |
| MCP server crash affects batcontrol | MCP runs in isolated thread; exceptions are caught and logged |
| Security (unauthenticated access) | Optional auth_token config; bind to localhost by default |
| Resource usage on Raspberry Pi | MCP server is lightweight; HTTP idle connections use minimal memory |
| Breaking existing MQTT behavior | OverrideManager is backward compatible; MQTT mode/set gets default duration |
