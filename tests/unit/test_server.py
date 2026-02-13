"""
Tests for the FastMCP server and the get_orchestrator_health tool.

Validates that the MCP server instance is correctly configured and that the
health endpoint returns complete, accurate information.
"""

from datetime import UTC, datetime

from fastmcp import FastMCP
from fastmcp.tools.tool import FunctionTool

from itom_orchestrator import __version__
from itom_orchestrator.config import get_config
from itom_orchestrator.server import _get_orchestrator_health, get_orchestrator_health, mcp


class TestMCPServerInstance:
    """Tests for the FastMCP server object."""

    def test_mcp_is_fastmcp_instance(self) -> None:
        assert isinstance(mcp, FastMCP)

    def test_mcp_server_name(self) -> None:
        assert mcp.name == "itom-orchestrator"

    def test_health_tool_is_registered(self) -> None:
        """The get_orchestrator_health tool is registered on the MCP server."""
        assert isinstance(get_orchestrator_health, FunctionTool)
        assert get_orchestrator_health.fn is _get_orchestrator_health


class TestGetOrchestratorHealth:
    """Tests for the get_orchestrator_health tool implementation."""

    def test_returns_all_expected_fields(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        expected_keys = {
            "status",
            "version",
            "uptime_seconds",
            "connected_agents",
            "active_workflows",
            "data_dir",
            "timestamp",
        }
        assert set(result.keys()) == expected_keys

    def test_status_is_healthy(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert result["status"] == "healthy"

    def test_uptime_is_positive_float(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] > 0

    def test_version_matches_package(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert result["version"] == __version__

    def test_data_dir_from_config(self, test_config) -> None:  # type: ignore[no-untyped-def]
        config = get_config()
        result = _get_orchestrator_health()
        assert result["data_dir"] == config.data_dir

    def test_timestamp_is_valid_iso8601(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        ts = result["timestamp"]
        # datetime.fromisoformat will raise ValueError if the string is not valid ISO 8601
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None  # Must be timezone-aware
        assert parsed.tzinfo == UTC

    def test_connected_agents_is_zero(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert result["connected_agents"] == 0

    def test_active_workflows_is_zero(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert result["active_workflows"] == 0
