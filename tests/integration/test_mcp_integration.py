"""
Integration tests for MCP tool and HTTP server (ORCH-024).

Tests that MCP tools and the FastAPI app return expected response shapes.
Uses TestClient for FastAPI app testing with no external dependencies.
"""

import pytest

from itom_orchestrator.http_server import create_app


@pytest.mark.integration
class TestHTTPServerIntegration:
    """Tests for the FastAPI HTTP server endpoints."""

    def test_health_endpoint(self, test_config):
        """Test GET /api/health returns expected shape."""
        from httpx import ASGITransport, AsyncClient
        import asyncio

        app = create_app()

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health")
            return response

        response = asyncio.get_event_loop().run_until_complete(_test())
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "version" in data

    def test_agents_status_endpoint(self, test_config):
        """Test GET /api/agents/status returns agent list."""
        from httpx import ASGITransport, AsyncClient
        import asyncio

        app = create_app()

        async def _test():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/agents/status")
            return response

        response = asyncio.get_event_loop().run_until_complete(_test())
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)


@pytest.mark.integration
class TestMCPToolShapes:
    """Tests that MCP tool functions return expected response shapes."""

    def test_get_orchestrator_health_shape(self, test_config):
        """Test _get_orchestrator_health returns expected keys."""
        from itom_orchestrator.server import _get_orchestrator_health

        result = _get_orchestrator_health()
        assert isinstance(result, dict)
        assert "status" in result
        assert "version" in result
        assert "uptime_seconds" in result
        assert "connected_agents" in result
        assert "timestamp" in result

    def test_get_agent_registry_shape(self, test_config):
        """Test _get_agent_registry returns expected keys."""
        from itom_orchestrator.server import _get_agent_registry

        result = _get_agent_registry()
        assert isinstance(result, dict)
        assert "agents" in result
        assert "count" in result
        assert isinstance(result["agents"], list)

    def test_get_agent_details_valid(self, test_config):
        """Test _get_agent_details with a valid agent ID."""
        from itom_orchestrator.server import _get_agent_details

        result = _get_agent_details("cmdb-agent")
        assert isinstance(result, dict)
        assert result.get("agent_id") == "cmdb-agent"
        assert "capabilities" in result
        assert "domain" in result

    def test_get_agent_details_invalid(self, test_config):
        """Test _get_agent_details with an invalid agent ID."""
        from itom_orchestrator.server import _get_agent_details

        result = _get_agent_details("nonexistent-agent")
        assert isinstance(result, dict)
        assert "error" in result

    def test_route_task_valid(self, test_config):
        """Test _route_task with valid parameters."""
        from itom_orchestrator.server import _route_task

        result = _route_task(
            task_id="test-task",
            title="Query CMDB CIs",
            description="Look up all servers in CMDB",
            domain="cmdb",
        )
        assert isinstance(result, dict)
        assert result.get("status") in ("routed", "error")

    def test_route_task_invalid_domain(self, test_config):
        """Test _route_task with an invalid domain."""
        from itom_orchestrator.server import _route_task

        result = _route_task(
            task_id="test-task",
            title="Test",
            description="Test",
            domain="invalid_domain",
        )
        assert isinstance(result, dict)
        assert "error" in result

    def test_get_execution_history_shape(self, test_config):
        """Test _get_execution_history returns expected keys."""
        from itom_orchestrator.server import _get_execution_history

        result = _get_execution_history()
        assert isinstance(result, dict)
        assert "records" in result
        assert "stats" in result
        assert "record_count" in result
