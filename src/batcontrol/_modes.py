"""Inverter mode constants shared by core.py and mcp_server.py.

Extracted to avoid the circular import that would arise from mcp_server.py
importing directly from core.py (core.py imports mcp_server at startup).
"""

MODE_ALLOW_DISCHARGING = 10
MODE_LIMIT_BATTERY_CHARGE_RATE = 8
MODE_AVOID_DISCHARGING = 0
MODE_FORCE_CHARGING = -1

MODE_NAMES = {
    MODE_FORCE_CHARGING: "Force Charge from Grid",
    MODE_AVOID_DISCHARGING: "Avoid Discharge",
    MODE_LIMIT_BATTERY_CHARGE_RATE: "Limit PV Charge Rate",
    MODE_ALLOW_DISCHARGING: "Allow Discharge",
}

VALID_MODES = set(MODE_NAMES.keys())
