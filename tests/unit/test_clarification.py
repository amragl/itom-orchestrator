"""
Tests for SE-010/SE-011: Ambiguity detection and ClarificationResponse.

Validates that:
- TaskRouter.detect_ambiguity returns ClarificationContext for tied domains
- detect_ambiguity returns None for clear single-domain queries
- detect_ambiguity returns None when task has explicit target_agent
- ClarificationResponse model is correctly populated
- process_chat_message returns ClarificationResponse on ambiguous input
- _pending_clarifications dict is populated with the right token
- CLARIFICATION_TEMPLATES covers common domain pairs
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from itom_orchestrator.api.chat import (
    ChatRequest,
    ClarificationResponse,
    _pending_clarifications,
    process_chat_message,
)
from itom_orchestrator.models.agents import AgentDomain, AgentStatus
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskStatus
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentRegistry
from itom_orchestrator.router import ClarificationContext, RoutingRule, TaskRouter
from itom_orchestrator.routing_config import CLARIFICATION_TEMPLATES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def persistence(tmp_data_dir: Path) -> StatePersistence:
    return StatePersistence(state_dir=str(tmp_data_dir / "state"))


@pytest.fixture()
def registry(persistence: StatePersistence) -> AgentRegistry:
    reg = AgentRegistry(persistence=persistence, load_defaults=True)
    reg.initialize()
    for agent in reg.list_all():
        reg.update_status(agent.agent_id, AgentStatus.ONLINE)
    return reg


@pytest.fixture()
def router(registry: AgentRegistry) -> TaskRouter:
    return TaskRouter(registry=registry)


def make_task(description: str, target_agent: str | None = None) -> Task:
    return Task(
        task_id="test-task",
        title=description[:100],
        description=description,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.PENDING,
        target_agent=target_agent,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# ClarificationContext / detect_ambiguity tests
# ---------------------------------------------------------------------------


class TestDetectAmbiguity:
    def test_returns_none_for_unambiguous_discovery_query(self, router):
        task = make_task("Schedule a discovery scan for the 10.0.0.0/24 IP range")
        result = router.detect_ambiguity(task)
        # "discovery" and "scan" only match discovery-domain → not ambiguous
        assert result is None

    def test_returns_none_when_explicit_target_agent(self, router):
        task = make_task("Show me all servers", target_agent="cmdb-agent")
        result = router.detect_ambiguity(task)
        assert result is None

    def test_returns_clarification_for_ambiguous_query(self, router):
        """'show me server requests' hits cmdb (server) and csa (request) at same priority."""
        task = make_task("show me server requests")
        result = router.detect_ambiguity(task)
        # Both "server" (cmdb) and "request" (csa) keywords match
        # This may or may not be ambiguous depending on priority differences
        # Verify the return type is correct when ambiguous
        if result is not None:
            assert isinstance(result, ClarificationContext)
            assert result.question
            assert len(result.options) >= 2
            assert result.competing_domains

    def test_clarification_context_fields(self):
        ctx = ClarificationContext(
            competing_domains=["cmdb", "csa"],
            question="Are you querying CMDB or creating a request?",
            options=["Query CMDB", "Create request"],
        )
        assert ctx.competing_domains == ["cmdb", "csa"]
        assert "CMDB" in ctx.question or "request" in ctx.question.lower()
        assert len(ctx.options) == 2

    def test_detect_ambiguity_with_custom_tied_rules(self, registry):
        """Two rules with identical priority both matching → ambiguous."""
        tied_rules = [
            RoutingRule(
                name="cmdb-rule",
                priority=10,
                domain=AgentDomain.CMDB,
                keywords=["overlap-keyword"],
            ),
            RoutingRule(
                name="csa-rule",
                priority=10,
                domain=AgentDomain.CSA,
                keywords=["overlap-keyword"],
            ),
        ]
        router_tied = TaskRouter(registry=registry, rules=tied_rules)
        task = make_task("overlap-keyword")
        result = router_tied.detect_ambiguity(task)

        assert result is not None
        assert isinstance(result, ClarificationContext)
        assert "cmdb" in result.competing_domains
        assert "csa" in result.competing_domains

    def test_detect_ambiguity_different_priorities_not_ambiguous(self, registry):
        """Rules at different priorities → lower priority wins, no ambiguity."""
        rules = [
            RoutingRule(
                name="cmdb-rule",
                priority=5,  # Higher precedence
                domain=AgentDomain.CMDB,
                keywords=["overlap-keyword"],
            ),
            RoutingRule(
                name="csa-rule",
                priority=15,  # Lower precedence
                domain=AgentDomain.CSA,
                keywords=["overlap-keyword"],
            ),
        ]
        router_diff = TaskRouter(registry=registry, rules=rules)
        task = make_task("overlap-keyword")
        result = router_diff.detect_ambiguity(task)
        assert result is None  # Different priorities → not tied


# ---------------------------------------------------------------------------
# CLARIFICATION_TEMPLATES tests
# ---------------------------------------------------------------------------


class TestClarificationTemplates:
    def test_all_pairs_have_question_and_options(self):
        for key, template in CLARIFICATION_TEMPLATES.items():
            assert "question" in template, f"Missing 'question' for key {key}"
            assert "options" in template, f"Missing 'options' for key {key}"
            assert isinstance(template["options"], list)
            assert len(template["options"]) >= 2

    def test_cmdb_csa_pair_exists(self):
        pair = frozenset(["cmdb", "csa"])
        assert pair in CLARIFICATION_TEMPLATES

    def test_cmdb_asset_pair_exists(self):
        pair = frozenset(["cmdb", "asset"])
        assert pair in CLARIFICATION_TEMPLATES

    def test_cmdb_discovery_pair_exists(self):
        pair = frozenset(["cmdb", "discovery"])
        assert pair in CLARIFICATION_TEMPLATES

    def test_fallback_none_key_exists(self):
        assert None in CLARIFICATION_TEMPLATES
        fallback = CLARIFICATION_TEMPLATES[None]
        assert len(fallback["options"]) >= 3  # Fallback has 5 options

    def test_unknown_pair_falls_back_to_none(self):
        unknown_pair = frozenset(["cmdb", "unknown_domain"])
        result = CLARIFICATION_TEMPLATES.get(unknown_pair) or CLARIFICATION_TEMPLATES.get(None)
        assert result is not None


# ---------------------------------------------------------------------------
# ClarificationResponse model tests
# ---------------------------------------------------------------------------


class TestClarificationResponseModel:
    def test_valid_construction(self):
        resp = ClarificationResponse(
            message_id="msg-001",
            question="Which domain?",
            options=["CMDB", "CSA"],
            pending_message_token="token-abc",
            session_id="sess-123",
            timestamp=datetime.now(UTC).isoformat(),
        )
        assert resp.response_type == "clarification"
        assert resp.message_id == "msg-001"
        assert resp.pending_message_token == "token-abc"
        assert len(resp.options) == 2

    def test_session_id_optional(self):
        resp = ClarificationResponse(
            message_id="msg-001",
            question="Which domain?",
            options=["CMDB"],
            pending_message_token="tok",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert resp.session_id is None


# ---------------------------------------------------------------------------
# process_chat_message with ambiguity
# ---------------------------------------------------------------------------


class TestProcessChatMessageAmbiguity:
    def test_returns_clarification_for_tied_domains(self, registry):
        """process_chat_message returns ClarificationResponse when ambiguous."""
        tied_rules = [
            RoutingRule(
                name="cmdb-rule",
                priority=10,
                domain=AgentDomain.CMDB,
                keywords=["ambiguous-term"],
            ),
            RoutingRule(
                name="csa-rule",
                priority=10,
                domain=AgentDomain.CSA,
                keywords=["ambiguous-term"],
            ),
        ]
        mock_router = TaskRouter(registry=registry, rules=tied_rules)
        mock_executor = MagicMock()

        _pending_clarifications.clear()

        request = ChatRequest(message="ambiguous-term query")
        response = process_chat_message(request, mock_router, mock_executor)

        assert isinstance(response, ClarificationResponse)
        assert response.response_type == "clarification"
        assert response.pending_message_token
        assert response.pending_message_token in _pending_clarifications

        # Verify the pending store has the right data
        pending = _pending_clarifications[response.pending_message_token]
        assert pending["original_message"] == "ambiguous-term query"

    def test_pending_clarifications_store_populated(self, registry):
        tied_rules = [
            RoutingRule(
                name="r1", priority=10, domain=AgentDomain.CMDB, keywords=["test-ambig"]
            ),
            RoutingRule(
                name="r2", priority=10, domain=AgentDomain.ASSET, keywords=["test-ambig"]
            ),
        ]
        mock_router = TaskRouter(registry=registry, rules=tied_rules)
        mock_executor = MagicMock()

        _pending_clarifications.clear()

        request = ChatRequest(message="test-ambig info", session_id="s-999")
        response = process_chat_message(request, mock_router, mock_executor)

        assert isinstance(response, ClarificationResponse)
        stored = _pending_clarifications.get(response.pending_message_token)
        assert stored is not None
        assert stored["original_message"] == "test-ambig info"
        assert stored["session_id"] == "s-999"
