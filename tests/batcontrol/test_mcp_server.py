"""Tests for the MCP server implementation

Requires the optional 'mcp' dependency (Python >=3.10).
Tests are skipped automatically when the mcp package is not installed.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock

from batcontrol.mcp_server import is_available as mcp_is_available

if not mcp_is_available():
    pytest.skip(
        "MCP SDK not installed (requires Python >=3.10)",
        allow_module_level=True
    )

from batcontrol.mcp_server import BatcontrolMcpServer, _format_forecast_array, MODE_NAMES
from batcontrol.override_manager import OverrideManager, OverrideState


class TestFormatForecastArray:
    """Test the forecast array formatting utility."""

    def test_none_array(self):
        result = _format_forecast_array(None, 1000.0, 60)
        assert result == []

    def test_simple_array(self):
        arr = np.array([100.0, 200.0, 300.0])
        result = _format_forecast_array(arr, 3600.0, 60)
        assert len(result) == 3
        assert result[0]['slot'] == 0
        assert result[0]['value'] == 100.0
        assert result[1]['value'] == 200.0
        assert result[2]['value'] == 300.0

    def test_15min_interval(self):
        arr = np.array([100.0, 200.0])
        result = _format_forecast_array(arr, 900.0, 15)
        assert len(result) == 2
        # 15 min = 900 seconds between slots
        assert result[1]['time_start'] - result[0]['time_start'] == 900

    def test_60min_interval(self):
        arr = np.array([100.0, 200.0])
        result = _format_forecast_array(arr, 3600.0, 60)
        assert len(result) == 2
        assert result[1]['time_start'] - result[0]['time_start'] == 3600


class TestBatcontrolMcpServer:
    """Test MCP server tool registration and execution."""

    @pytest.fixture
    def mock_bc(self):
        """Create a mock Batcontrol instance with all necessary attributes."""
        bc = MagicMock()
        bc.override_manager = OverrideManager()
        bc.last_mode = 10
        bc.last_charge_rate = 0
        bc.last_SOC = 65.0
        bc.last_stored_energy = 5000.0
        bc.last_stored_usable_energy = 4500.0
        bc.last_max_capacity = 10000.0
        bc.last_free_capacity = 5000.0
        bc.last_reserved_energy = 1000.0
        bc.discharge_blocked = False
        bc.last_run_time = 1700000000.0
        bc.time_resolution = 60
        bc.last_prices = np.array([0.15, 0.20, 0.25, 0.18])
        bc.last_production = np.array([500.0, 1000.0, 800.0, 200.0])
        bc.last_consumption = np.array([300.0, 400.0, 500.0, 350.0])
        bc.last_net_consumption = np.array([-200.0, -600.0, -300.0, 150.0])
        bc.production_offset_percent = 1.0
        bc.max_charging_from_grid_limit = 0.8
        bc.min_price_difference = 0.05
        bc.min_price_difference_rel = 0.1
        bc._limit_battery_charge_rate = -1
        bc.get_always_allow_discharge_limit.return_value = 0.9
        bc.api_get_decision_explanation.return_value = [
            "Current price: 0.1500, Battery: 5000 Wh stored",
            "Decision: Allow discharge"
        ]
        bc.api_set_always_allow_discharge_limit = MagicMock()
        bc.api_set_max_charging_from_grid_limit = MagicMock()
        bc.api_set_min_price_difference = MagicMock()
        bc.api_set_min_price_difference_rel = MagicMock()
        bc.api_set_production_offset = MagicMock()
        bc._apply_override = MagicMock()
        return bc

    @pytest.fixture
    def server(self, mock_bc):
        """Create a BatcontrolMcpServer instance."""
        return BatcontrolMcpServer(mock_bc, {})

    def test_server_creation(self, server):
        """Test that server creates without errors."""
        assert server.mcp is not None
        assert server._bc is not None

    def test_tools_registered(self, server):
        """Test that all expected tools are registered."""
        tools = server.mcp._tool_manager._tools
        expected_tools = [
            'get_system_status',
            'get_price_forecast',
            'get_solar_forecast',
            'get_consumption_forecast',
            'get_net_consumption_forecast',
            'get_battery_info',
            'get_decision_explanation',
            'get_configuration',
            'get_override_status',
            'set_mode_override',
            'clear_mode_override',
            'set_charge_rate',
            'set_parameter',
        ]
        for tool_name in expected_tools:
            assert tool_name in tools, f"Tool '{tool_name}' not registered"

    def test_get_system_status(self, server, mock_bc):
        """Test get_system_status tool returns correct data."""
        # Access the tool function directly
        tool_fn = server.mcp._tool_manager._tools['get_system_status'].fn
        result = tool_fn()
        assert result['mode'] == 10
        assert result['mode_name'] == "Allow Discharge"
        assert result['soc_percent'] == 65.0
        assert result['override'] is None

    def test_get_price_forecast(self, server, mock_bc):
        """Test get_price_forecast tool."""
        tool_fn = server.mcp._tool_manager._tools['get_price_forecast'].fn
        result = tool_fn()
        assert result['interval_minutes'] == 60
        assert result['current_price'] == 0.15
        assert len(result['prices']) == 4

    def test_get_solar_forecast(self, server, mock_bc):
        """Test get_solar_forecast tool."""
        tool_fn = server.mcp._tool_manager._tools['get_solar_forecast'].fn
        result = tool_fn()
        assert len(result['production_w']) == 4
        assert result['production_offset_percent'] == 1.0

    def test_get_battery_info(self, server, mock_bc):
        """Test get_battery_info tool."""
        tool_fn = server.mcp._tool_manager._tools['get_battery_info'].fn
        result = tool_fn()
        assert result['soc_percent'] == 65.0
        assert result['max_capacity_wh'] == 10000.0
        assert result['always_allow_discharge_limit'] == 0.9

    def test_get_decision_explanation(self, server, mock_bc):
        """Test get_decision_explanation tool."""
        tool_fn = server.mcp._tool_manager._tools['get_decision_explanation'].fn
        result = tool_fn()
        assert result['mode'] == 10
        assert len(result['explanation_steps']) == 2
        assert "Allow discharge" in result['explanation_steps'][1]
        assert result['override_active'] is False

    def test_get_configuration(self, server, mock_bc):
        """Test get_configuration tool."""
        tool_fn = server.mcp._tool_manager._tools['get_configuration'].fn
        result = tool_fn()
        assert result['min_price_difference'] == 0.05
        assert result['time_resolution_minutes'] == 60

    def test_get_override_status_inactive(self, server):
        """Test get_override_status when no override is active."""
        tool_fn = server.mcp._tool_manager._tools['get_override_status'].fn
        result = tool_fn()
        assert result['active'] is False
        assert result['override'] is None

    def test_get_override_status_active(self, server, mock_bc):
        """Test get_override_status when override is active."""
        mock_bc.override_manager.set_override(mode=-1, duration_minutes=30, reason="test")
        tool_fn = server.mcp._tool_manager._tools['get_override_status'].fn
        result = tool_fn()
        assert result['active'] is True
        assert result['override']['mode'] == -1
        assert result['mode_name'] == "Force Charge from Grid"

    def test_set_mode_override(self, server, mock_bc):
        """Test set_mode_override tool."""
        tool_fn = server.mcp._tool_manager._tools['set_mode_override'].fn
        result = tool_fn(mode=0, duration_minutes=60, reason="test override")
        assert result['success'] is True
        assert result['mode_name'] == "Avoid Discharge"
        assert mock_bc.override_manager.is_active()
        mock_bc._apply_override.assert_called_once()

    def test_set_mode_override_invalid_mode(self, server):
        """Test set_mode_override with invalid mode."""
        tool_fn = server.mcp._tool_manager._tools['set_mode_override'].fn
        result = tool_fn(mode=99, duration_minutes=30)
        assert 'error' in result

    def test_set_mode_override_invalid_duration(self, server):
        """Test set_mode_override with invalid duration."""
        tool_fn = server.mcp._tool_manager._tools['set_mode_override'].fn
        result = tool_fn(mode=0, duration_minutes=0)
        assert 'error' in result

    def test_clear_mode_override(self, server, mock_bc):
        """Test clear_mode_override tool."""
        mock_bc.override_manager.set_override(mode=-1, duration_minutes=30)
        tool_fn = server.mcp._tool_manager._tools['clear_mode_override'].fn
        result = tool_fn()
        assert result['success'] is True
        assert result['was_active'] is True
        assert not mock_bc.override_manager.is_active()

    def test_set_charge_rate(self, server, mock_bc):
        """Test set_charge_rate tool."""
        tool_fn = server.mcp._tool_manager._tools['set_charge_rate'].fn
        result = tool_fn(charge_rate_w=2000, duration_minutes=45, reason="charge test")
        assert result['success'] is True
        assert mock_bc.override_manager.is_active()
        override = mock_bc.override_manager.get_override()
        assert override.mode == -1
        assert override.charge_rate == 2000
        mock_bc._apply_override.assert_called_once()

    def test_set_charge_rate_invalid(self, server):
        """Test set_charge_rate with invalid rate."""
        tool_fn = server.mcp._tool_manager._tools['set_charge_rate'].fn
        result = tool_fn(charge_rate_w=0)
        assert 'error' in result

    def test_set_parameter_valid(self, server, mock_bc):
        """Test set_parameter with a valid parameter."""
        tool_fn = server.mcp._tool_manager._tools['set_parameter'].fn
        result = tool_fn(parameter='min_price_difference', value=0.08)
        assert result['success'] is True
        mock_bc.api_set_min_price_difference.assert_called_once_with(0.08)

    def test_set_parameter_invalid(self, server):
        """Test set_parameter with unknown parameter."""
        tool_fn = server.mcp._tool_manager._tools['set_parameter'].fn
        result = tool_fn(parameter='nonexistent', value=1.0)
        assert 'error' in result

    def test_set_parameter_all_valid_params(self, server, mock_bc):
        """Test that all documented parameters are accepted."""
        tool_fn = server.mcp._tool_manager._tools['set_parameter'].fn
        valid_params = [
            ('always_allow_discharge_limit', 0.85),
            ('max_charging_from_grid_limit', 0.75),
            ('min_price_difference', 0.03),
            ('min_price_difference_rel', 0.15),
            ('production_offset', 0.9),
        ]
        for param, value in valid_params:
            result = tool_fn(parameter=param, value=value)
            assert result['success'] is True, f"Parameter '{param}' should be valid"
