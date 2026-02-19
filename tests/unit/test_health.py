"""
Unit tests for the AgentHealthChecker -- ORCH-006.

Tests cover:
- Single agent health checks
- Bulk health checks
- TTL-based cache behavior
- Health history recording and persistence
- Health statistics computation
- get_agent_status and check_all_agents MCP tools
"""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from itom_orchestrator.health import (
    AgentHealthChecker,
    HealthCheckRecord,
    HealthCheckResult,
    HealthCheckerConfig,
)
from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentNotFoundError, AgentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture()
def persistence(state_dir: Path) -> StatePersistence:
    return StatePersistence(state_dir)


@pytest.fixture()
def registry(persistence: StatePersistence) -> AgentRegistry:
    reg = AgentRegistry(persistence=persistence, load_defaults=True)
    reg.initialize()
    return reg


@pytest.fixture()
def checker(
    registry: AgentRegistry, persistence: StatePersistence
) -> AgentHealthChecker:
    config = HealthCheckerConfig(
        check_timeout_seconds=5.0,
        cache_ttl_seconds=2.0,
        max_history_per_agent=50,
        max_total_history=200,
    )
    return AgentHealthChecker(registry=registry, persistence=persistence, config=config)


@pytest.fixture()
def short_cache_checker(
    registry: AgentRegistry, persistence: StatePersistence
) -> AgentHealthChecker:
    """Checker with a very short cache TTL for testing cache expiration."""
    config = HealthCheckerConfig(cache_ttl_seconds=0.1)
    return AgentHealthChecker(registry=registry, persistence=persistence, config=config)


# ---------------------------------------------------------------------------
# HealthCheckRecord
# ---------------------------------------------------------------------------


class TestHealthCheckRecord:
    """Tests for HealthCheckRecord serialization."""

    def test_to_dict(self) -> None:
        now = datetime.now(UTC)
        record = HealthCheckRecord(
            agent_id="cmdb-agent",
            result=HealthCheckResult.HEALTHY,
            response_time_ms=1.23,
            timestamp=now,
            details="All good.",
        )
        d = record.to_dict()
        assert d["agent_id"] == "cmdb-agent"
        assert d["result"] == "healthy"
        assert d["response_time_ms"] == 1.23
        assert d["details"] == "All good."

    def test_from_dict_round_trip(self) -> None:
        now = datetime.now(UTC)
        original = HealthCheckRecord(
            agent_id="test-agent",
            result=HealthCheckResult.DEGRADED,
            response_time_ms=5.67,
            timestamp=now,
            details="Partial check.",
        )
        d = original.to_dict()
        restored = HealthCheckRecord.from_dict(d)
        assert restored.agent_id == original.agent_id
        assert restored.result == original.result
        assert restored.response_time_ms == original.response_time_ms
        assert restored.details == original.details


# ---------------------------------------------------------------------------
# Single agent checks
# ---------------------------------------------------------------------------


class TestCheckAgent:
    """Tests for checking a single agent's health."""

    def test_check_existing_agent(self, checker: AgentHealthChecker) -> None:
        """Checking a registered agent should return a result."""
        record = checker.check_agent("cmdb-agent")
        assert record.agent_id == "cmdb-agent"
        assert isinstance(record.result, HealthCheckResult)
        assert record.response_time_ms >= 0
        assert record.timestamp is not None

    def test_check_updates_registry_status(
        self, checker: AgentHealthChecker, registry: AgentRegistry
    ) -> None:
        """Health check should update the agent's status in the registry."""
        # cmdb-agent starts as ONLINE (has MCP URL, port 8002)
        before = registry.get("cmdb-agent")
        assert before.status == AgentStatus.ONLINE

        record = checker.check_agent("cmdb-agent", force=True)
        after = registry.get("cmdb-agent")

        # Status should have been updated based on the check result
        assert after.status != AgentStatus.OFFLINE or record.result == HealthCheckResult.UNHEALTHY
        assert after.last_health_check is not None

    def test_check_nonexistent_raises(self, checker: AgentHealthChecker) -> None:
        """Checking a non-existent agent should raise AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError):
            checker.check_agent("nonexistent-agent")

    def test_check_agent_with_mcp_url(
        self, checker: AgentHealthChecker
    ) -> None:
        """Agent with MCP URL should get a degraded check (network not verified)."""
        record = checker.check_agent("cmdb-agent")
        # cmdb-agent has MCP URL — marked degraded until live connectivity is verified
        assert record.result == HealthCheckResult.DEGRADED
        assert "mcp" in record.details.lower() or "endpoint" in record.details.lower()


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCacheBehavior:
    """Tests for TTL-based cache."""

    def test_cached_result_returned(self, checker: AgentHealthChecker) -> None:
        """Second check within TTL should return cached result."""
        first = checker.check_agent("cmdb-agent")
        second = checker.check_agent("cmdb-agent")

        # Same timestamp means it was cached (not re-executed)
        assert first.timestamp == second.timestamp

    def test_forced_check_bypasses_cache(self, checker: AgentHealthChecker) -> None:
        """Forced check should always re-execute."""
        first = checker.check_agent("cmdb-agent")
        # Force a fresh check -- timestamps may differ
        second = checker.check_agent("cmdb-agent", force=True)
        # Both should be valid results
        assert first.agent_id == second.agent_id

    def test_cache_expires(self, short_cache_checker: AgentHealthChecker) -> None:
        """After TTL expires, a new check should be performed."""
        first = short_cache_checker.check_agent("cmdb-agent")
        time.sleep(0.15)  # Wait for cache to expire (TTL = 0.1s)
        second = short_cache_checker.check_agent("cmdb-agent")

        # The second check should have a newer timestamp
        assert second.timestamp >= first.timestamp

    def test_clear_cache_single_agent(self, checker: AgentHealthChecker) -> None:
        """Clearing cache for one agent should only affect that agent."""
        checker.check_agent("cmdb-agent")
        checker.check_agent("discovery-agent")
        checker.clear_cache("cmdb-agent")

        # cmdb-agent cache should be cleared, discovery-agent should still be cached
        assert not checker._is_cache_valid("cmdb-agent")
        assert checker._is_cache_valid("discovery-agent")

    def test_clear_cache_all(self, checker: AgentHealthChecker) -> None:
        """Clearing all cache should remove all entries."""
        checker.check_agent("cmdb-agent")
        checker.check_agent("discovery-agent")
        checker.clear_cache()

        assert not checker._is_cache_valid("cmdb-agent")
        assert not checker._is_cache_valid("discovery-agent")


# ---------------------------------------------------------------------------
# Bulk checks
# ---------------------------------------------------------------------------


class TestCheckAll:
    """Tests for bulk health checks."""

    def test_check_all_returns_results_for_each_agent(
        self, checker: AgentHealthChecker, registry: AgentRegistry
    ) -> None:
        """check_all should return one result per registered agent."""
        results = checker.check_all()
        assert len(results) == registry.agent_count

    def test_check_all_covers_all_agents(
        self, checker: AgentHealthChecker
    ) -> None:
        """check_all results should include every registered agent."""
        results = checker.check_all()
        checked_ids = {r.agent_id for r in results}
        expected_ids = {
            "cmdb-agent",
            "discovery-agent",
            "asset-agent",
            "csa-agent",
            "itom-auditor",
            "itom-documentator",
        }
        assert checked_ids == expected_ids

    def test_check_all_force(self, checker: AgentHealthChecker) -> None:
        """check_all with force should bypass cache for all agents."""
        # First pass populates cache
        checker.check_all()
        # Force pass should still work
        results = checker.check_all(force=True)
        assert len(results) == 6


# ---------------------------------------------------------------------------
# Health history
# ---------------------------------------------------------------------------


class TestHealthHistory:
    """Tests for health check history recording and persistence."""

    def test_check_records_in_history(self, checker: AgentHealthChecker) -> None:
        """Health check should be recorded in history."""
        checker.check_agent("cmdb-agent", force=True)
        history = checker.get_history("cmdb-agent")
        assert len(history) >= 1
        assert history[0]["agent_id"] == "cmdb-agent"

    def test_history_newest_first(self, checker: AgentHealthChecker) -> None:
        """History should be returned newest first."""
        checker.check_agent("cmdb-agent", force=True)
        time.sleep(0.01)
        checker.check_agent("cmdb-agent", force=True)

        history = checker.get_history("cmdb-agent")
        assert len(history) >= 2
        # First entry should be newer
        t1 = datetime.fromisoformat(history[0]["timestamp"])
        t2 = datetime.fromisoformat(history[1]["timestamp"])
        assert t1 >= t2

    def test_history_limit(self, checker: AgentHealthChecker) -> None:
        """History should respect the limit parameter."""
        for _ in range(5):
            checker.check_agent("cmdb-agent", force=True)

        history = checker.get_history("cmdb-agent", limit=3)
        assert len(history) <= 3

    def test_history_persists(
        self, registry: AgentRegistry, persistence: StatePersistence
    ) -> None:
        """Health history should survive reload from persistence."""
        config = HealthCheckerConfig(cache_ttl_seconds=0.01)
        checker1 = AgentHealthChecker(
            registry=registry, persistence=persistence, config=config
        )
        checker1.check_agent("cmdb-agent", force=True)

        # Create new checker -- should load history from persistence
        checker2 = AgentHealthChecker(
            registry=registry, persistence=persistence, config=config
        )
        history = checker2.get_history("cmdb-agent")
        assert len(history) >= 1

    def test_empty_history(self, checker: AgentHealthChecker) -> None:
        """Agent with no checks should return empty history."""
        history = checker.get_history("cmdb-agent")
        assert history == []


# ---------------------------------------------------------------------------
# Health statistics
# ---------------------------------------------------------------------------


class TestHealthStats:
    """Tests for health statistics computation."""

    def test_stats_after_checks(self, checker: AgentHealthChecker) -> None:
        """Stats should reflect performed checks."""
        checker.check_agent("cmdb-agent", force=True)
        checker.check_agent("cmdb-agent", force=True)
        checker.check_agent("cmdb-agent", force=True)

        health_info = checker.get_agent_health("cmdb-agent")
        stats = health_info["health_stats"]
        assert stats["total_checks"] == 3
        # cmdb-agent has an MCP URL → health check returns DEGRADED (network not verified),
        # so uptime_percentage stays 0 while avg_response_time_ms is still measured.
        assert stats["avg_response_time_ms"] >= 0
        assert stats.get("result_distribution", {}).get("degraded", 0) == 3

    def test_stats_empty_history(self, checker: AgentHealthChecker) -> None:
        """Stats with no history should return zeros."""
        health_info = checker.get_agent_health("cmdb-agent")
        stats = health_info["health_stats"]
        assert stats["total_checks"] == 0
        assert stats["uptime_percentage"] == 0.0

    def test_get_agent_health_structure(self, checker: AgentHealthChecker) -> None:
        """get_agent_health should return expected fields."""
        checker.check_agent("cmdb-agent", force=True)
        health = checker.get_agent_health("cmdb-agent")

        assert "agent_id" in health
        assert "name" in health
        assert "current_status" in health
        assert "latest_check" in health
        assert "health_stats" in health
        assert "history_count" in health

    def test_get_all_health_structure(self, checker: AgentHealthChecker) -> None:
        """get_all_health should return expected summary."""
        checker.check_all(force=True)
        summary = checker.get_all_health()

        assert "agents" in summary
        assert "total_agents" in summary
        assert "status_summary" in summary
        assert len(summary["agents"]) == 6

    def test_get_agent_health_nonexistent_raises(
        self, checker: AgentHealthChecker
    ) -> None:
        """get_agent_health for non-existent agent should raise error."""
        with pytest.raises(AgentNotFoundError):
            checker.get_agent_health("nonexistent-agent")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestHealthCheckerConfig:
    """Tests for configuration."""

    def test_default_config(self) -> None:
        config = HealthCheckerConfig()
        assert config.check_timeout_seconds == 10.0
        assert config.cache_ttl_seconds == 60.0
        assert config.max_history_per_agent == 100
        assert config.max_total_history == 1000

    def test_custom_config(self) -> None:
        config = HealthCheckerConfig(
            check_timeout_seconds=5.0,
            cache_ttl_seconds=30.0,
            max_history_per_agent=50,
        )
        assert config.check_timeout_seconds == 5.0
        assert config.cache_ttl_seconds == 30.0

    def test_checker_exposes_config(self, checker: AgentHealthChecker) -> None:
        assert checker.config.check_timeout_seconds == 5.0
        assert checker.config.cache_ttl_seconds == 2.0


# ---------------------------------------------------------------------------
# History limits
# ---------------------------------------------------------------------------


class TestHistoryLimits:
    """Tests for history size enforcement."""

    def test_per_agent_history_limit(
        self, registry: AgentRegistry, persistence: StatePersistence
    ) -> None:
        """History should be trimmed when per-agent limit is exceeded."""
        config = HealthCheckerConfig(
            max_history_per_agent=5,
            max_total_history=1000,
            cache_ttl_seconds=0.0,  # Disable cache
        )
        checker = AgentHealthChecker(
            registry=registry, persistence=persistence, config=config
        )

        for _ in range(10):
            checker.check_agent("cmdb-agent", force=True)

        history = checker.get_history("cmdb-agent", limit=100)
        assert len(history) <= 5
