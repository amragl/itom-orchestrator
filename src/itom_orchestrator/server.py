"""
FastMCP server for the ITOM Orchestrator.

This is the MCP server entry point. MCP tools are registered on the ``mcp``
instance and exposed to connected clients.

Registered tools:
- get_orchestrator_health -- server health and uptime information
- get_agent_registry -- list all registered agents and their capabilities
- get_agent_details -- get detailed info for a specific agent by ID
- get_agent_status -- health check and status for a specific agent
- check_all_agents -- bulk health check across all registered agents
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP

from itom_orchestrator import __version__
from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain, AgentStatus

mcp = FastMCP("itom-orchestrator")

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Captured at module load time -- used to compute uptime in health checks.
_server_start_time: float = time.monotonic()

# Lazy-initialized singleton references.
_registry_instance: Any = None
_health_checker_instance: Any = None


def _get_registry() -> Any:
    """Get or create the AgentRegistry singleton.

    Uses lazy initialization to avoid circular imports. The registry is
    created on first access and persisted via the StatePersistence layer.
    """
    global _registry_instance
    if _registry_instance is None:
        from itom_orchestrator.persistence import get_persistence
        from itom_orchestrator.registry import AgentRegistry

        persistence = get_persistence()
        _registry_instance = AgentRegistry(persistence=persistence, load_defaults=True)
        _registry_instance.initialize()
    return _registry_instance


def _get_health_checker() -> Any:
    """Get or create the AgentHealthChecker singleton.

    Uses lazy initialization. The health checker is created on first access,
    using the registry and persistence singletons.
    """
    global _health_checker_instance
    if _health_checker_instance is None:
        from itom_orchestrator.health import AgentHealthChecker
        from itom_orchestrator.persistence import get_persistence

        registry = _get_registry()
        persistence = get_persistence()
        _health_checker_instance = AgentHealthChecker(
            registry=registry, persistence=persistence
        )
    return _health_checker_instance


def reset_registry() -> None:
    """Reset the registry and health checker singletons. For use in tests."""
    global _registry_instance, _health_checker_instance
    _registry_instance = None
    _health_checker_instance = None


def _get_orchestrator_health() -> dict[str, Any]:
    """Return the current health status of the ITOM Orchestrator.

    Returns:
        Dictionary with health status fields including status, version,
        uptime, connected agent count, active workflow count, data directory,
        and an ISO 8601 timestamp.
    """
    config = get_config()
    uptime = time.monotonic() - _server_start_time
    now = datetime.now(tz=UTC).isoformat()

    # Get registry agent count if available
    try:
        registry = _get_registry()
        connected_agents = len(registry.search_by_status(AgentStatus.ONLINE))
        total_agents = registry.agent_count
    except Exception:
        connected_agents = 0
        total_agents = 0

    health: dict[str, Any] = {
        "status": "healthy",
        "version": __version__,
        "uptime_seconds": round(uptime, 3),
        "connected_agents": connected_agents,
        "total_registered_agents": total_agents,
        "active_workflows": 0,
        "data_dir": config.data_dir,
        "timestamp": now,
    }

    logger.info(
        "Health check completed",
        extra={"extra_data": health},
    )

    return health


def _get_agent_registry(
    domain: str | None = None,
    status: str | None = None,
    capability: str | None = None,
) -> dict[str, Any]:
    """List all registered agents with optional filtering.

    Args:
        domain: Filter by agent domain (e.g., "cmdb", "discovery", "asset").
        status: Filter by agent status (e.g., "online", "offline").
        capability: Filter by capability name.

    Returns:
        Dictionary with agent list, summary statistics, and applied filters.
    """
    registry = _get_registry()

    if capability:
        agents = registry.search_by_capability(capability)
    elif domain:
        try:
            agent_domain = AgentDomain(domain)
            agents = registry.search_by_domain(agent_domain)
        except ValueError:
            return {
                "error": f"Invalid domain '{domain}'. Valid domains: {[d.value for d in AgentDomain]}",
                "agents": [],
            }
    elif status:
        try:
            agent_status = AgentStatus(status)
            agents = registry.search_by_status(agent_status)
        except ValueError:
            return {
                "error": f"Invalid status '{status}'. Valid statuses: {[s.value for s in AgentStatus]}",
                "agents": [],
            }
    else:
        agents = registry.list_all()

    agent_list = []
    for agent in agents:
        agent_list.append({
            "agent_id": agent.agent_id,
            "name": agent.name,
            "domain": agent.domain.value,
            "status": agent.status.value,
            "capabilities": [c.name for c in agent.capabilities],
            "capability_count": len(agent.capabilities),
            "registered_at": agent.registered_at.isoformat(),
            "last_health_check": (
                agent.last_health_check.isoformat() if agent.last_health_check else None
            ),
        })

    summary = registry.get_summary()
    filters_applied = {}
    if domain:
        filters_applied["domain"] = domain
    if status:
        filters_applied["status"] = status
    if capability:
        filters_applied["capability"] = capability

    result: dict[str, Any] = {
        "agents": agent_list,
        "count": len(agent_list),
        "total_registered": summary["total_agents"],
        "filters_applied": filters_applied,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    logger.info(
        "Agent registry queried",
        extra={"extra_data": {"count": len(agent_list), "filters": filters_applied}},
    )

    return result


def _get_agent_details(agent_id: str) -> dict[str, Any]:
    """Get detailed information about a specific agent.

    Args:
        agent_id: The unique identifier of the agent.

    Returns:
        Dictionary with full agent details including all capabilities,
        metadata, and registration info.
    """
    registry = _get_registry()

    try:
        agent = registry.get(agent_id)
    except Exception:
        return {
            "error": f"Agent '{agent_id}' not found in registry.",
            "agent_id": agent_id,
            "available_agents": [a.agent_id for a in registry.list_all()],
        }

    capabilities_detail = []
    for cap in agent.capabilities:
        cap_info: dict[str, Any] = {
            "name": cap.name,
            "domain": cap.domain.value,
            "description": cap.description,
        }
        if cap.input_schema:
            cap_info["input_schema"] = cap.input_schema
        if cap.output_schema:
            cap_info["output_schema"] = cap.output_schema
        capabilities_detail.append(cap_info)

    result: dict[str, Any] = {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "description": agent.description,
        "domain": agent.domain.value,
        "status": agent.status.value,
        "capabilities": capabilities_detail,
        "capability_count": len(agent.capabilities),
        "mcp_server_url": agent.mcp_server_url,
        "registered_at": agent.registered_at.isoformat(),
        "last_health_check": (
            agent.last_health_check.isoformat() if agent.last_health_check else None
        ),
        "metadata": agent.metadata,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    logger.info(
        "Agent details retrieved",
        extra={"extra_data": {"agent_id": agent_id, "domain": agent.domain.value}},
    )

    return result


def _get_agent_status(agent_id: str, force_check: bool = False) -> dict[str, Any]:
    """Get health status for a specific agent, optionally forcing a fresh check.

    Args:
        agent_id: The unique identifier of the agent.
        force_check: If True, bypass cache and perform a fresh health check.

    Returns:
        Dictionary with agent health status, latest check result, and stats.
    """
    health_checker = _get_health_checker()

    try:
        # Perform the check (may use cache unless forced)
        record = health_checker.check_agent(agent_id, force=force_check)

        # Get comprehensive health info
        health_info = health_checker.get_agent_health(agent_id)
        health_info["latest_check_result"] = record.to_dict()

        return health_info

    except Exception as exc:
        return {
            "error": str(exc),
            "agent_id": agent_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }


def _check_all_agents(force_check: bool = False) -> dict[str, Any]:
    """Run health checks on all registered agents.

    Args:
        force_check: If True, bypass cache for all agents.

    Returns:
        Dictionary with per-agent results and aggregate summary.
    """
    health_checker = _get_health_checker()
    results = health_checker.check_all(force=force_check)

    agent_results = [r.to_dict() for r in results]

    # Get full summary
    summary = health_checker.get_all_health()

    return {
        "check_results": agent_results,
        "summary": summary,
        "force_check": force_check,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# Register MCP tools
get_orchestrator_health = mcp.tool()(_get_orchestrator_health)
get_agent_registry = mcp.tool()(_get_agent_registry)
get_agent_details = mcp.tool()(_get_agent_details)
get_agent_status = mcp.tool()(_get_agent_status)
check_all_agents = mcp.tool()(_check_all_agents)
