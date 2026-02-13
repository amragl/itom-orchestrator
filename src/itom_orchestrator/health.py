"""
Agent health checking and status monitoring for the ITOM Orchestrator.

Provides per-agent and bulk health checks with configurable timeout,
TTL-based result caching, and health history tracking. Integrates with
the AgentRegistry to update agent statuses based on check results.

This module implements ORCH-006: Agent health checking and status monitoring.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentRegistration, AgentStatus
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentNotFoundError, AgentRegistry

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Persistence key for health history state
HEALTH_HISTORY_KEY = "health-history"


class HealthCheckResult(StrEnum):
    """Result of a single health check."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNREACHABLE = "unreachable"
    SKIPPED = "skipped"


# Mapping from check result to agent status
_RESULT_TO_STATUS: dict[HealthCheckResult, AgentStatus] = {
    HealthCheckResult.HEALTHY: AgentStatus.ONLINE,
    HealthCheckResult.DEGRADED: AgentStatus.DEGRADED,
    HealthCheckResult.UNHEALTHY: AgentStatus.OFFLINE,
    HealthCheckResult.UNREACHABLE: AgentStatus.OFFLINE,
    HealthCheckResult.SKIPPED: AgentStatus.MAINTENANCE,
}


@dataclass
class HealthCheckRecord:
    """Record of a single health check execution.

    Attributes:
        agent_id: The agent that was checked.
        result: The check outcome.
        response_time_ms: Round-trip time of the health check in milliseconds.
        timestamp: When the check was performed.
        details: Additional context from the check (error messages, etc.).
    """

    agent_id: str
    result: HealthCheckResult
    response_time_ms: float
    timestamp: datetime
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "agent_id": self.agent_id,
            "result": self.result.value,
            "response_time_ms": round(self.response_time_ms, 2),
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealthCheckRecord":
        """Deserialize from a dictionary."""
        return cls(
            agent_id=data["agent_id"],
            result=HealthCheckResult(data["result"]),
            response_time_ms=data["response_time_ms"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            details=data.get("details", ""),
        )


@dataclass
class CachedCheckResult:
    """Cached result of a health check with TTL tracking.

    Attributes:
        record: The health check record.
        cached_at: Monotonic time when the result was cached.
    """

    record: HealthCheckRecord
    cached_at: float  # time.monotonic() value


@dataclass
class HealthCheckerConfig:
    """Configuration for the AgentHealthChecker.

    Attributes:
        check_timeout_seconds: Maximum time to wait for a single health check.
        cache_ttl_seconds: How long cached results are considered valid.
        max_history_per_agent: Maximum number of history records per agent.
        max_total_history: Maximum total history records across all agents.
    """

    check_timeout_seconds: float = 10.0
    cache_ttl_seconds: float = 60.0
    max_history_per_agent: int = 100
    max_total_history: int = 1000


class AgentHealthChecker:
    """Monitors agent health via periodic checks with caching and history.

    The health checker performs connectivity checks against registered agents.
    Since ITOM agents may run as MCP servers, the check verifies that the agent
    is reachable and responsive. For agents without an MCP server URL (running
    as local tools), the check verifies the agent's registration status.

    Results are cached with a configurable TTL to avoid excessive polling.
    All check results are recorded in a persistent health history.

    Args:
        registry: The AgentRegistry containing agent registrations.
        persistence: StatePersistence for saving health history.
        config: Health checker configuration. If None, uses defaults.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        persistence: StatePersistence,
        config: HealthCheckerConfig | None = None,
    ) -> None:
        self._registry = registry
        self._persistence = persistence
        self._config = config or HealthCheckerConfig()
        self._cache: dict[str, CachedCheckResult] = {}
        self._history: dict[str, list[HealthCheckRecord]] = {}
        self._load_history()

    def _load_history(self) -> None:
        """Load health history from persistence."""
        data = self._persistence.load(HEALTH_HISTORY_KEY)
        if data is None:
            self._history = {}
            return

        try:
            history_data = data.get("agents", {})
            for agent_id, records_data in history_data.items():
                self._history[agent_id] = [
                    HealthCheckRecord.from_dict(r) for r in records_data
                ]
            logger.info(
                "Health history loaded",
                extra={
                    "extra_data": {
                        "agents_with_history": len(self._history),
                        "total_records": sum(
                            len(recs) for recs in self._history.values()
                        ),
                    }
                },
            )
        except Exception:
            logger.warning("Failed to parse health history, starting fresh", exc_info=True)
            self._history = {}

    def _save_history(self) -> None:
        """Persist health history to storage."""
        data: dict[str, Any] = {
            "agents": {},
            "last_updated": datetime.now(UTC).isoformat(),
        }
        for agent_id, records in self._history.items():
            data["agents"][agent_id] = [r.to_dict() for r in records]

        try:
            self._persistence.save(HEALTH_HISTORY_KEY, data)
        except OSError:
            logger.error("Failed to save health history", exc_info=True)

    def _append_history(self, record: HealthCheckRecord) -> None:
        """Add a check record to history, enforcing size limits."""
        agent_id = record.agent_id
        if agent_id not in self._history:
            self._history[agent_id] = []

        self._history[agent_id].append(record)

        # Enforce per-agent limit
        if len(self._history[agent_id]) > self._config.max_history_per_agent:
            excess = len(self._history[agent_id]) - self._config.max_history_per_agent
            self._history[agent_id] = self._history[agent_id][excess:]

        # Enforce total limit
        total = sum(len(recs) for recs in self._history.values())
        while total > self._config.max_total_history:
            # Remove oldest record across all agents
            oldest_agent = None
            oldest_time = None
            for aid, recs in self._history.items():
                if recs:
                    if oldest_time is None or recs[0].timestamp < oldest_time:
                        oldest_time = recs[0].timestamp
                        oldest_agent = aid
            if oldest_agent and self._history[oldest_agent]:
                self._history[oldest_agent].pop(0)
                if not self._history[oldest_agent]:
                    del self._history[oldest_agent]
            total -= 1

    def _is_cache_valid(self, agent_id: str) -> bool:
        """Check if the cached result for an agent is still within TTL."""
        if agent_id not in self._cache:
            return False
        cached = self._cache[agent_id]
        elapsed = time.monotonic() - cached.cached_at
        return elapsed < self._config.cache_ttl_seconds

    def _perform_check(self, agent: AgentRegistration) -> HealthCheckRecord:
        """Execute a health check against a single agent.

        For agents with an MCP server URL, this would attempt a connection.
        For agents without a URL (local tools), this verifies registration
        health based on metadata and last known state.

        Currently implements registration-based health checking. Network-based
        health checks will be added when agents run as remote MCP servers.

        Args:
            agent: The agent to check.

        Returns:
            HealthCheckRecord with the result.
        """
        start = time.monotonic()
        now = datetime.now(UTC)

        # Registration-based health check:
        # - Agent is registered: baseline healthy
        # - Agent has MCP URL but we cannot verify connectivity yet: degraded
        # - Agent metadata indicates issues: unhealthy
        if agent.mcp_server_url:
            # Agent declares an MCP endpoint. We mark as degraded since
            # we cannot perform actual network connectivity checks yet.
            # When MCP client connectivity is implemented, this will
            # attempt a real connection.
            elapsed_ms = (time.monotonic() - start) * 1000
            return HealthCheckRecord(
                agent_id=agent.agent_id,
                result=HealthCheckResult.DEGRADED,
                response_time_ms=elapsed_ms,
                timestamp=now,
                details=(
                    f"Agent declares MCP endpoint at {agent.mcp_server_url}. "
                    "Network connectivity check not yet implemented. "
                    "Marking as degraded until remote verification is available."
                ),
            )

        # For local agents (no MCP URL), check based on registration validity
        elapsed_ms = (time.monotonic() - start) * 1000

        # Validate capabilities are declared
        if not agent.capabilities:
            return HealthCheckRecord(
                agent_id=agent.agent_id,
                result=HealthCheckResult.UNHEALTHY,
                response_time_ms=elapsed_ms,
                timestamp=now,
                details="Agent has no capabilities declared.",
            )

        # Agent is registered with capabilities -- considered healthy
        return HealthCheckRecord(
            agent_id=agent.agent_id,
            result=HealthCheckResult.HEALTHY,
            response_time_ms=elapsed_ms,
            timestamp=now,
            details=f"Registration check passed. {len(agent.capabilities)} capabilities declared.",
        )

    def check_agent(
        self, agent_id: str, force: bool = False
    ) -> HealthCheckRecord:
        """Perform a health check on a specific agent.

        Returns a cached result if available and within TTL, unless
        ``force=True`` is specified.

        Args:
            agent_id: The ID of the agent to check.
            force: If True, bypass the cache and perform a fresh check.

        Returns:
            HealthCheckRecord with the check result.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        # Check cache first (unless forced)
        if not force and self._is_cache_valid(agent_id):
            cached = self._cache[agent_id]
            logger.debug(
                "Returning cached health check",
                extra={"extra_data": {"agent_id": agent_id}},
            )
            return cached.record

        # Get agent from registry (raises AgentNotFoundError if missing)
        agent = self._registry.get(agent_id)

        # Perform the check
        record = self._perform_check(agent)

        # Update cache
        self._cache[agent_id] = CachedCheckResult(
            record=record,
            cached_at=time.monotonic(),
        )

        # Update registry status
        new_status = _RESULT_TO_STATUS.get(record.result, AgentStatus.OFFLINE)
        self._registry.update_status(
            agent_id, new_status, last_health_check=record.timestamp
        )

        # Record in history
        self._append_history(record)
        self._save_history()

        logger.info(
            "Health check completed",
            extra={
                "extra_data": {
                    "agent_id": agent_id,
                    "result": record.result.value,
                    "response_time_ms": record.response_time_ms,
                    "new_status": new_status.value,
                }
            },
        )

        return record

    def check_all(self, force: bool = False) -> list[HealthCheckRecord]:
        """Perform health checks on all registered agents.

        Args:
            force: If True, bypass cache for all agents.

        Returns:
            List of HealthCheckRecord objects, one per agent.
        """
        agents = self._registry.list_all()
        results: list[HealthCheckRecord] = []

        for agent in agents:
            record = self.check_agent(agent.agent_id, force=force)
            results.append(record)

        logger.info(
            "Bulk health check completed",
            extra={
                "extra_data": {
                    "total_agents": len(results),
                    "healthy": sum(1 for r in results if r.result == HealthCheckResult.HEALTHY),
                    "degraded": sum(1 for r in results if r.result == HealthCheckResult.DEGRADED),
                    "unhealthy": sum(
                        1 for r in results
                        if r.result in (HealthCheckResult.UNHEALTHY, HealthCheckResult.UNREACHABLE)
                    ),
                }
            },
        )

        return results

    def get_agent_health(self, agent_id: str) -> dict[str, Any]:
        """Get comprehensive health information for an agent.

        Returns the latest check result, current status from registry,
        and recent health history.

        Args:
            agent_id: The ID of the agent.

        Returns:
            Dictionary with current status, latest check, and history.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        agent = self._registry.get(agent_id)
        history = self._history.get(agent_id, [])

        # Get latest check (from cache or history)
        latest_check = None
        if agent_id in self._cache:
            latest_check = self._cache[agent_id].record.to_dict()
        elif history:
            latest_check = history[-1].to_dict()

        # Compute health statistics from history
        stats = self._compute_stats(agent_id)

        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "current_status": agent.status.value,
            "last_health_check": (
                agent.last_health_check.isoformat() if agent.last_health_check else None
            ),
            "latest_check": latest_check,
            "health_stats": stats,
            "history_count": len(history),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def get_all_health(self) -> dict[str, Any]:
        """Get a summary of health across all registered agents.

        Returns:
            Dictionary with per-agent statuses, aggregate stats, and timestamp.
        """
        agents = self._registry.list_all()
        agent_health: list[dict[str, Any]] = []

        for agent in agents:
            history = self._history.get(agent.agent_id, [])
            latest = None
            if agent.agent_id in self._cache:
                latest = self._cache[agent.agent_id].record.to_dict()
            elif history:
                latest = history[-1].to_dict()

            agent_health.append({
                "agent_id": agent.agent_id,
                "name": agent.name,
                "status": agent.status.value,
                "last_check": latest,
                "checks_in_history": len(history),
            })

        # Aggregate stats
        status_counts: dict[str, int] = {}
        for ah in agent_health:
            s = ah["status"]
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "agents": agent_health,
            "total_agents": len(agents),
            "status_summary": status_counts,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def get_history(
        self, agent_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent health check history for an agent.

        Args:
            agent_id: The ID of the agent.
            limit: Maximum number of records to return (most recent first).

        Returns:
            List of health check records as dictionaries, newest first.
        """
        records = self._history.get(agent_id, [])
        # Return newest first, limited
        recent = records[-limit:] if limit < len(records) else records
        return [r.to_dict() for r in reversed(recent)]

    def _compute_stats(self, agent_id: str) -> dict[str, Any]:
        """Compute health statistics from an agent's check history.

        Args:
            agent_id: The agent to compute stats for.

        Returns:
            Dictionary with uptime_percentage, avg_response_time_ms,
            total_checks, and result distribution.
        """
        records = self._history.get(agent_id, [])
        if not records:
            return {
                "total_checks": 0,
                "uptime_percentage": 0.0,
                "avg_response_time_ms": 0.0,
                "result_distribution": {},
            }

        total = len(records)
        healthy_count = sum(
            1 for r in records if r.result == HealthCheckResult.HEALTHY
        )
        uptime_pct = (healthy_count / total) * 100 if total > 0 else 0.0

        avg_response = sum(r.response_time_ms for r in records) / total

        distribution: dict[str, int] = {}
        for r in records:
            key = r.result.value
            distribution[key] = distribution.get(key, 0) + 1

        return {
            "total_checks": total,
            "uptime_percentage": round(uptime_pct, 2),
            "avg_response_time_ms": round(avg_response, 2),
            "result_distribution": distribution,
        }

    def clear_cache(self, agent_id: str | None = None) -> None:
        """Clear cached health check results.

        Args:
            agent_id: If specified, clear cache for only this agent.
                If None, clear the entire cache.
        """
        if agent_id:
            self._cache.pop(agent_id, None)
        else:
            self._cache.clear()
        logger.info(
            "Health cache cleared",
            extra={"extra_data": {"agent_id": agent_id or "all"}},
        )

    @property
    def config(self) -> HealthCheckerConfig:
        """Return the current health checker configuration."""
        return self._config
