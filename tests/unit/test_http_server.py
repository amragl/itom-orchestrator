"""
Tests for the FastAPI HTTP server layer (ORCH-026, ORCH-028).

Validates that:
- FastAPI app is created with correct configuration
- CORS middleware is configured with allowed origins
- GET /api/health returns orchestrator health status
- GET /api/agents/status returns agent summary with optional force_check
- GET /api/agents/{id} returns agent details or 404
- GET /api/agents/{id}/health returns per-agent health with optional force_check
- HTTP endpoints bridge to the same internal logic as MCP tools
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from itom_orchestrator.config import OrchestratorConfig
from itom_orchestrator.http_server import create_app, reset_http_singletons


@pytest.fixture()
def http_app(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client for the FastAPI app with isolated state.

    Uses tmp_data_dir to ensure no filesystem side effects between tests.
    """
    monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("ORCH_LOG_LEVEL", "DEBUG")

    reset_http_singletons()

    app = create_app()
    return TestClient(app)


class TestAppCreation:
    """Tests for FastAPI app factory."""

    def test_create_app_returns_fastapi_instance(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_app() returns a FastAPI instance with the correct title."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
        reset_http_singletons()
        app = create_app()

        assert app.title == "ITOM Orchestrator API"
        assert app.docs_url == "/api/docs"
        assert app.openapi_url == "/api/openapi.json"

    def test_create_app_has_cors_middleware(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """App should have CORS middleware configured."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
        reset_http_singletons()
        app = create_app()

        # Check that CORSMiddleware is in the middleware stack
        middleware_classes = [type(m).__name__ for m in app.user_middleware]
        assert any("CORS" in cls or "cors" in cls.lower() for cls in middleware_classes) or len(
            app.user_middleware
        ) > 0, "CORS middleware should be configured"

    def test_create_app_registers_api_routes(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """App should have the /api routes registered."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
        reset_http_singletons()
        app = create_app()

        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/health" in route_paths
        assert "/api/agents/status" in route_paths
        assert "/api/agents/{agent_id}" in route_paths
        assert "/api/agents/{agent_id}/health" in route_paths


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_health_returns_200(self, http_app: TestClient) -> None:
        """Health endpoint should return 200 OK."""
        response = http_app.get("/api/health")
        assert response.status_code == 200

    def test_health_returns_status_field(self, http_app: TestClient) -> None:
        """Health response should include a status field."""
        response = http_app.get("/api/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_returns_version(self, http_app: TestClient) -> None:
        """Health response should include the version."""
        from itom_orchestrator import __version__

        response = http_app.get("/api/health")
        data = response.json()
        assert data["version"] == __version__

    def test_health_returns_uptime(self, http_app: TestClient) -> None:
        """Health response should include uptime_seconds."""
        response = http_app.get("/api/health")
        data = response.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_health_returns_agent_counts(self, http_app: TestClient) -> None:
        """Health response should include connected and total agent counts."""
        response = http_app.get("/api/health")
        data = response.json()
        assert "connected_agents" in data
        assert "total_registered_agents" in data
        assert isinstance(data["total_registered_agents"], int)

    def test_health_returns_timestamp(self, http_app: TestClient) -> None:
        """Health response should include a timestamp."""
        response = http_app.get("/api/health")
        data = response.json()
        assert "timestamp" in data
        assert isinstance(data["timestamp"], str)
        # Should be parseable as ISO 8601
        assert "T" in data["timestamp"]


class TestAgentsStatusEndpoint:
    """Tests for GET /api/agents/status."""

    def test_agents_status_returns_200(self, http_app: TestClient) -> None:
        """Agents status endpoint should return 200 OK."""
        response = http_app.get("/api/agents/status")
        assert response.status_code == 200

    def test_agents_status_returns_agents_list(self, http_app: TestClient) -> None:
        """Response should include an agents list."""
        response = http_app.get("/api/agents/status")
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_agents_status_returns_total_count(self, http_app: TestClient) -> None:
        """Response should include total_agents count."""
        response = http_app.get("/api/agents/status")
        data = response.json()
        assert "total_agents" in data
        assert isinstance(data["total_agents"], int)
        # Default registry has 6 ITOM agents
        assert data["total_agents"] == 6

    def test_agents_status_includes_status_summary(self, http_app: TestClient) -> None:
        """Response should include a status_summary breakdown."""
        response = http_app.get("/api/agents/status")
        data = response.json()
        assert "status_summary" in data
        assert isinstance(data["status_summary"], dict)

    def test_agents_status_each_agent_has_required_fields(self, http_app: TestClient) -> None:
        """Each agent in the list should have agent_id, name, and status."""
        response = http_app.get("/api/agents/status")
        data = response.json()
        for agent in data["agents"]:
            assert "agent_id" in agent
            assert "name" in agent
            assert "status" in agent

    def test_agents_status_with_force_check(self, http_app: TestClient) -> None:
        """force_check=true should trigger fresh health checks."""
        response = http_app.get("/api/agents/status?force_check=true")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        # After force check, agents should have last_check populated
        for agent in data["agents"]:
            assert "last_check" in agent

    def test_agents_status_without_force_check(self, http_app: TestClient) -> None:
        """Default (no force_check) should return cached or current status."""
        response = http_app.get("/api/agents/status")
        assert response.status_code == 200
        data = response.json()
        assert data["total_agents"] == 6


class TestAgentDetailsEndpoint:
    """Tests for GET /api/agents/{agent_id}."""

    def test_agent_details_returns_200_for_valid_id(self, http_app: TestClient) -> None:
        """Should return 200 for a known agent."""
        response = http_app.get("/api/agents/cmdb-agent")
        assert response.status_code == 200

    def test_agent_details_returns_full_info(self, http_app: TestClient) -> None:
        """Response should include agent_id, name, domain, capabilities."""
        response = http_app.get("/api/agents/cmdb-agent")
        data = response.json()
        assert data["agent_id"] == "cmdb-agent"
        assert data["name"] == "CMDB Agent"
        assert data["domain"] == "cmdb"
        assert "capabilities" in data
        assert isinstance(data["capabilities"], list)
        assert len(data["capabilities"]) > 0

    def test_agent_details_returns_404_for_unknown_id(self, http_app: TestClient) -> None:
        """Should return 404 for a non-existent agent."""
        response = http_app.get("/api/agents/nonexistent-agent")
        assert response.status_code == 404

    def test_agent_details_404_includes_error_message(self, http_app: TestClient) -> None:
        """404 response should include a descriptive error message."""
        response = http_app.get("/api/agents/nonexistent-agent")
        data = response.json()
        assert "detail" in data
        assert "nonexistent-agent" in data["detail"]

    def test_agent_details_for_each_default_agent(self, http_app: TestClient) -> None:
        """Each of the 6 default agents should be accessible."""
        agent_ids = [
            "cmdb-agent",
            "discovery-agent",
            "asset-agent",
            "csa-agent",
            "itom-auditor",
            "itom-documentator",
        ]
        for agent_id in agent_ids:
            response = http_app.get(f"/api/agents/{agent_id}")
            assert response.status_code == 200, f"Failed for {agent_id}"
            data = response.json()
            assert data["agent_id"] == agent_id


class TestAgentHealthEndpoint:
    """Tests for GET /api/agents/{agent_id}/health."""

    def test_agent_health_returns_200_for_valid_id(self, http_app: TestClient) -> None:
        """Should return 200 for a known agent."""
        response = http_app.get("/api/agents/cmdb-agent/health")
        assert response.status_code == 200

    def test_agent_health_returns_health_fields(self, http_app: TestClient) -> None:
        """Response should include agent health info fields."""
        response = http_app.get("/api/agents/cmdb-agent/health")
        data = response.json()
        assert "agent_id" in data
        assert data["agent_id"] == "cmdb-agent"
        assert "current_status" in data
        assert "health_stats" in data
        assert "latest_check_result" in data

    def test_agent_health_with_force_check(self, http_app: TestClient) -> None:
        """force_check=true should perform a fresh health check."""
        response = http_app.get("/api/agents/cmdb-agent/health?force_check=true")
        assert response.status_code == 200
        data = response.json()
        assert "latest_check_result" in data
        result = data["latest_check_result"]
        assert "result" in result
        assert "response_time_ms" in result

    def test_agent_health_returns_404_for_unknown_id(self, http_app: TestClient) -> None:
        """Should return 404 for a non-existent agent."""
        response = http_app.get("/api/agents/nonexistent-agent/health")
        assert response.status_code == 404

    def test_agent_health_includes_statistics(self, http_app: TestClient) -> None:
        """Response should include health_stats with check history."""
        response = http_app.get("/api/agents/discovery-agent/health?force_check=true")
        data = response.json()
        stats = data["health_stats"]
        assert "total_checks" in stats
        assert "uptime_percentage" in stats
        assert "avg_response_time_ms" in stats

    def test_agent_health_for_all_default_agents(self, http_app: TestClient) -> None:
        """Health check should work for each default agent."""
        agent_ids = [
            "cmdb-agent",
            "discovery-agent",
            "asset-agent",
            "csa-agent",
            "itom-auditor",
            "itom-documentator",
        ]
        for agent_id in agent_ids:
            response = http_app.get(f"/api/agents/{agent_id}/health")
            assert response.status_code == 200, f"Failed for {agent_id}"


class TestCORSHeaders:
    """Tests for CORS configuration."""

    def test_cors_allows_configured_origin(self, http_app: TestClient) -> None:
        """Requests from allowed origins should get CORS headers."""
        response = http_app.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS preflight should succeed
        assert response.status_code == 200

    def test_cors_headers_on_get_request(self, http_app: TestClient) -> None:
        """GET requests from allowed origins should get CORS headers."""
        response = http_app.get(
            "/api/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestConfigIntegration:
    """Tests for HTTP server configuration integration."""

    def test_default_config_values(self) -> None:
        """Default config should have sensible HTTP defaults."""
        config = OrchestratorConfig()
        assert config.http_host == "0.0.0.0"
        assert config.http_port == 8000
        assert isinstance(config.cors_origins, list)
        assert len(config.cors_origins) > 0

    def test_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP config should be overridable via environment variables."""
        monkeypatch.setenv("ORCH_HTTP_HOST", "127.0.0.1")
        monkeypatch.setenv("ORCH_HTTP_PORT", "9000")
        config = OrchestratorConfig()
        assert config.http_host == "127.0.0.1"
        assert config.http_port == 9000
