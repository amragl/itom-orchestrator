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
- route_task -- route a task to the appropriate agent via domain/capability matching
- get_execution_history -- retrieve task execution history with optional filtering
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
_router_instance: Any = None
_executor_instance: Any = None


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


def _get_router() -> Any:
    """Get or create the TaskRouter singleton.

    Uses lazy initialization. The router is created on first access,
    using the registry singleton for agent lookups. Attempts to load
    routing rules from routing-rules.json configuration file if available.
    Falls back to default rules if config file is not found.
    """
    global _router_instance
    if _router_instance is None:
        from pathlib import Path
        from itom_orchestrator.router import TaskRouter, RoutingRulesLoader

        registry = _get_registry()
        config = get_config()

        # Attempt to load routing rules from config file
        routing_config_path = Path(config.data_dir) / "config" / "routing-rules.json"
        rules = None

        if routing_config_path.exists():
            try:
                loader = RoutingRulesLoader(
                    config_path=str(routing_config_path),
                    validate_on_load=True,
                    cache_config=True,
                    enable_hot_reload=True,
                )
                routing_config = loader.load()
                logger.info(
                    "Loaded routing rules configuration",
                    extra={
                        "extra_data": {
                            "config_path": str(routing_config_path),
                            "domains": len(routing_config.get("domains", {})),
                            "rules": len(routing_config.get("routing_rules", [])),
                        }
                    },
                )
            except (FileNotFoundError, ValueError) as e:
                logger.warning(
                    "Failed to load routing rules config, using defaults",
                    extra={"extra_data": {"error": str(e)}},
                )
        else:
            logger.debug(
                "Routing rules config file not found, using default rules",
                extra={"extra_data": {"path": str(routing_config_path)}},
            )

        # require_available=False until agents have real health endpoints.
        # Once MCP client connectivity is implemented, switch to True.
        _router_instance = TaskRouter(registry=registry, rules=rules, require_available=False)
    return _router_instance


def _get_executor() -> Any:
    """Get or create the TaskExecutor singleton.

    Uses lazy initialization. The executor is created on first access,
    using the router and persistence singletons. Also registers any
    configured agent dispatch handlers on first creation.
    """
    global _executor_instance
    if _executor_instance is None:
        from itom_orchestrator.executor import TaskExecutor
        from itom_orchestrator.persistence import get_persistence

        router = _get_router()
        persistence = get_persistence()
        _executor_instance = TaskExecutor(router=router, persistence=persistence)

        # Register dispatch handlers for configured agent endpoints
        from itom_orchestrator.agent_dispatch import register_all_handlers

        register_all_handlers()
    return _executor_instance


def reset_registry() -> None:
    """Reset all singletons. For use in tests."""
    global _registry_instance, _health_checker_instance, _router_instance, _executor_instance
    _registry_instance = None
    _health_checker_instance = None
    _router_instance = None
    _executor_instance = None


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


def _route_task(
    task_id: str,
    title: str,
    description: str,
    domain: str | None = None,
    target_agent: str | None = None,
    priority: str = "medium",
    parameters: dict[str, Any] | None = None,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Route a task to the most appropriate ITOM agent.

    Evaluates the task against routing criteria and selects the best
    available agent. Does NOT execute the task -- just determines where
    it should go.

    Args:
        task_id: Unique identifier for the task.
        title: Short task title (used for keyword-based routing).
        description: Detailed task description.
        domain: Optional domain hint (cmdb, discovery, asset, csa, audit, documentation).
        target_agent: Optional explicit agent ID (bypasses routing).
        priority: Task priority (critical, high, medium, low).
        parameters: Optional input parameters for the task.
        timeout_seconds: Maximum execution time in seconds.

    Returns:
        Dictionary with routing decision, selected agent info, and task status.
    """
    from itom_orchestrator.models.agents import AgentDomain as AD
    from itom_orchestrator.models.tasks import Task, TaskPriority, TaskStatus
    from itom_orchestrator.router import RoutingError

    # Parse domain if provided
    parsed_domain = None
    if domain:
        try:
            parsed_domain = AD(domain)
        except ValueError:
            return {
                "error": f"Invalid domain '{domain}'. Valid domains: {[d.value for d in AD]}",
                "task_id": task_id,
                "status": "error",
            }

    # Parse priority
    try:
        parsed_priority = TaskPriority(priority)
    except ValueError:
        return {
            "error": f"Invalid priority '{priority}'. Valid: {[p.value for p in TaskPriority]}",
            "task_id": task_id,
            "status": "error",
        }

    # Create the task model
    task = Task(
        task_id=task_id,
        title=title,
        description=description,
        domain=parsed_domain,
        target_agent=target_agent,
        priority=parsed_priority,
        status=TaskStatus.PENDING,
        parameters=parameters or {},
        created_at=datetime.now(UTC),
        timeout_seconds=timeout_seconds,
    )

    # Route the task
    router = _get_router()
    try:
        decision = router.route(task)
    except RoutingError as exc:
        return {
            "error": exc.message,
            "error_code": exc.error_code,
            "task_id": task_id,
            "status": "error",
        }

    result: dict[str, Any] = {
        "task_id": task_id,
        "status": "routed",
        "routing_decision": decision.to_dict(),
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "domain": task.domain.value if task.domain else None,
            "priority": task.priority.value,
            "target_agent": task.target_agent,
            "timeout_seconds": task.timeout_seconds,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }

    logger.info(
        "Task routed via MCP",
        extra={"extra_data": {"task_id": task_id, "agent_id": decision.agent.agent_id}},
    )

    return result


def _get_execution_history(
    task_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Retrieve task execution history with optional filtering.

    Args:
        task_id: If provided, filter history to this task only.
        limit: Maximum number of records to return (default 50, most recent first).

    Returns:
        Dictionary with execution records, statistics, and active tasks.
    """
    executor = _get_executor()

    records = executor.get_execution_history(task_id=task_id, limit=limit)
    stats = executor.get_execution_stats()
    active = executor.get_active_tasks()

    result: dict[str, Any] = {
        "records": records,
        "record_count": len(records),
        "stats": stats,
        "active_tasks": active,
        "filters": {"task_id": task_id, "limit": limit},
        "timestamp": datetime.now(UTC).isoformat(),
    }

    logger.info(
        "Execution history queried",
        extra={
            "extra_data": {
                "record_count": len(records),
                "task_id_filter": task_id,
            }
        },
    )

    return result


# Register MCP tools
get_orchestrator_health = mcp.tool()(_get_orchestrator_health)
get_agent_registry = mcp.tool()(_get_agent_registry)
get_agent_details = mcp.tool()(_get_agent_details)
get_agent_status = mcp.tool()(_get_agent_status)
check_all_agents = mcp.tool()(_check_all_agents)
route_task = mcp.tool()(_route_task)
get_execution_history = mcp.tool()(_get_execution_history)
