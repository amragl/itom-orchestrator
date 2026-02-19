"""
Tests for the FastMCP server and the MCP tools.

Validates that the MCP server instance is correctly configured and that
health, registry, and agent detail tools return complete, accurate information.
"""

from datetime import UTC, datetime

from fastmcp import FastMCP
from fastmcp.tools.tool import FunctionTool

from itom_orchestrator import __version__
from itom_orchestrator.config import get_config
from itom_orchestrator.server import (
    _get_agent_details,
    _get_agent_registry,
    _get_orchestrator_health,
    get_agent_details,
    get_agent_registry,
    get_orchestrator_health,
    mcp,
)


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

    def test_registry_tool_is_registered(self) -> None:
        """The get_agent_registry tool is registered on the MCP server."""
        assert isinstance(get_agent_registry, FunctionTool)
        assert get_agent_registry.fn is _get_agent_registry

    def test_agent_details_tool_is_registered(self) -> None:
        """The get_agent_details tool is registered on the MCP server."""
        assert isinstance(get_agent_details, FunctionTool)
        assert get_agent_details.fn is _get_agent_details


class TestGetOrchestratorHealth:
    """Tests for the get_orchestrator_health tool implementation."""

    def test_returns_all_expected_fields(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        expected_keys = {
            "status",
            "version",
            "uptime_seconds",
            "connected_agents",
            "total_registered_agents",
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

    def test_connected_agents_includes_cmdb(self, test_config) -> None:  # type: ignore[no-untyped-def]
        # cmdb-agent starts ONLINE (has MCP URL); all others start OFFLINE
        result = _get_orchestrator_health()
        assert result["connected_agents"] == 1

    def test_active_workflows_is_zero(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_orchestrator_health()
        assert result["active_workflows"] == 0

    def test_total_registered_agents(self, test_config) -> None:  # type: ignore[no-untyped-def]
        """Health check should report the total number of registered agents."""
        result = _get_orchestrator_health()
        assert result["total_registered_agents"] == 6


class TestGetAgentRegistry:
    """Tests for the get_agent_registry MCP tool."""

    def test_list_all_agents(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry()
        assert result["count"] == 6
        assert result["total_registered"] == 6
        assert len(result["agents"]) == 6

    def test_filter_by_domain(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry(domain="cmdb")
        assert result["count"] == 1
        assert result["agents"][0]["agent_id"] == "cmdb-agent"
        assert result["filters_applied"]["domain"] == "cmdb"

    def test_filter_by_status(self, test_config) -> None:  # type: ignore[no-untyped-def]
        # cmdb-agent starts ONLINE; 5 agents start OFFLINE
        result = _get_agent_registry(status="offline")
        assert result["count"] == 5

    def test_filter_by_capability(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry(capability="query_cis")
        assert result["count"] == 1
        assert result["agents"][0]["agent_id"] == "cmdb-agent"

    def test_invalid_domain_returns_error(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry(domain="invalid_domain")
        assert "error" in result
        assert result["agents"] == []

    def test_invalid_status_returns_error(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry(status="invalid_status")
        assert "error" in result
        assert result["agents"] == []

    def test_agent_has_expected_fields(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_registry()
        agent = result["agents"][0]
        expected_keys = {
            "agent_id",
            "name",
            "domain",
            "status",
            "capabilities",
            "capability_count",
            "registered_at",
            "last_health_check",
        }
        assert expected_keys.issubset(set(agent.keys()))


class TestGetAgentDetails:
    """Tests for the get_agent_details MCP tool."""

    def test_get_existing_agent(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_details("cmdb-agent")
        assert result["agent_id"] == "cmdb-agent"
        assert result["domain"] == "cmdb"
        assert result["capability_count"] == 6
        assert len(result["capabilities"]) == 6
        assert "description" in result

    def test_get_nonexistent_agent(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_details("nonexistent-agent")
        assert "error" in result
        assert "available_agents" in result

    def test_capabilities_have_detail(self, test_config) -> None:  # type: ignore[no-untyped-def]
        result = _get_agent_details("cmdb-agent")
        cap = result["capabilities"][0]
        assert "name" in cap
        assert "domain" in cap
        assert "description" in cap
