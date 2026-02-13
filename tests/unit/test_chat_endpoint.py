"""
Tests for the POST /api/chat endpoint (ORCH-027).

Validates that:
- Chat messages are routed to the correct agent
- Domain hints direct messages appropriately
- Explicit agent targeting works
- Empty messages return 400
- Invalid domains return 400
- Unroutable messages return 502
- Response includes agent info and routing metadata
- Session ID is echoed back in response
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from itom_orchestrator.executor import TaskExecutor
from itom_orchestrator.http_server import create_app, reset_http_singletons


@pytest.fixture()
def http_app(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client for the FastAPI app with isolated state."""
    monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("ORCH_LOG_LEVEL", "DEBUG")
    reset_http_singletons()
    TaskExecutor.clear_dispatch_handlers()
    app = create_app()
    return TestClient(app)


class TestChatRouting:
    """Tests for chat message routing."""

    def test_chat_routes_cmdb_message(self, http_app: TestClient) -> None:
        """Message mentioning CMDB should route to cmdb-agent."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Query CMDB for all Linux servers"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "cmdb-agent"
        assert data["status"] == "success"

    def test_chat_routes_discovery_message(self, http_app: TestClient) -> None:
        """Message about discovery should route to discovery-agent."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Run a discovery scan on the 10.0.0.0/24 network"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "discovery-agent"

    def test_chat_routes_by_domain_hint(self, http_app: TestClient) -> None:
        """Domain hint should direct routing regardless of message content."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Show me everything",
                "domain": "asset",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "asset-agent"

    def test_chat_routes_by_explicit_target(self, http_app: TestClient) -> None:
        """Explicit target_agent should bypass routing."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Generate documentation",
                "target_agent": "itom-auditor",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "itom-auditor"


class TestChatResponseFormat:
    """Tests for chat response structure."""

    def test_response_includes_message_id(self, http_app: TestClient) -> None:
        """Response should have a unique message_id."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Check CMDB health"},
        )
        data = response.json()
        assert "message_id" in data
        assert data["message_id"].startswith("chat-")

    def test_response_includes_agent_info(self, http_app: TestClient) -> None:
        """Response should include agent_id, agent_name, and domain."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Query CMDB for servers"},
        )
        data = response.json()
        assert "agent_id" in data
        assert "agent_name" in data
        assert "domain" in data

    def test_response_includes_routing_method(self, http_app: TestClient) -> None:
        """Response should include how the message was routed."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Run compliance audit"},
        )
        data = response.json()
        assert "routing_method" in data
        assert data["routing_method"] in ("explicit", "rule", "domain", "capability")

    def test_response_includes_timestamp(self, http_app: TestClient) -> None:
        """Response should include a timestamp."""
        response = http_app.post(
            "/api/chat",
            json={"message": "Check asset inventory"},
        )
        data = response.json()
        assert "timestamp" in data
        assert "T" in data["timestamp"]

    def test_session_id_echoed_back(self, http_app: TestClient) -> None:
        """Session ID from request should be echoed in response."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Query CMDB",
                "session_id": "session-abc-123",
            },
        )
        data = response.json()
        assert data["session_id"] == "session-abc-123"


class TestChatErrorHandling:
    """Tests for chat endpoint error responses."""

    def test_empty_message_returns_422(self, http_app: TestClient) -> None:
        """Empty message should return 422 (Pydantic validation)."""
        response = http_app.post(
            "/api/chat",
            json={"message": ""},
        )
        assert response.status_code == 422

    def test_whitespace_message_returns_422(self, http_app: TestClient) -> None:
        """Whitespace-only message should return 422."""
        response = http_app.post(
            "/api/chat",
            json={"message": "   "},
        )
        assert response.status_code == 422

    def test_missing_message_returns_422(self, http_app: TestClient) -> None:
        """Missing message field should return 422."""
        response = http_app.post(
            "/api/chat",
            json={},
        )
        assert response.status_code == 422

    def test_invalid_domain_returns_400(self, http_app: TestClient) -> None:
        """Invalid domain should return 400."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Do something",
                "domain": "invalid-domain",
            },
        )
        assert response.status_code == 400

    def test_unroutable_message_returns_502(self, http_app: TestClient) -> None:
        """Message that cannot be routed should return 502."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Something completely generic with no keywords",
            },
        )
        assert response.status_code == 502

    def test_nonexistent_target_returns_502(self, http_app: TestClient) -> None:
        """Non-existent target agent should return 502."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Do something",
                "target_agent": "nonexistent-agent",
            },
        )
        assert response.status_code == 502


class TestChatContext:
    """Tests for chat context passing."""

    def test_context_accepted(self, http_app: TestClient) -> None:
        """Context dict should be accepted and passed through."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Show CMDB details for this CI",
                "context": {
                    "selected_ci": "sys_id_12345",
                    "previous_query": "list servers",
                },
            },
        )
        assert response.status_code == 200

    def test_empty_context_ok(self, http_app: TestClient) -> None:
        """Empty context should be accepted."""
        response = http_app.post(
            "/api/chat",
            json={
                "message": "Query CMDB",
                "context": {},
            },
        )
        assert response.status_code == 200
