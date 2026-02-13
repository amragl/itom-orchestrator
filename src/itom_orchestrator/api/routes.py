"""
API route definitions for the ITOM Orchestrator HTTP server.

All routes are prefixed with ``/api`` and bridge to the same internal
logic used by the MCP tools. This ensures consistent behaviour between
MCP and HTTP interfaces.

Endpoints:
- GET  /api/health                  -- orchestrator health (unauthenticated)
- GET  /api/agents/status           -- summary health for all agents
- GET  /api/agents/{id}             -- detailed info for a specific agent
- GET  /api/agents/{id}/health      -- health check for a specific agent

This module implements ORCH-028: GET /api/health and GET /api/agents/status
endpoints for chat-ui connection monitoring.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


def _get_registry() -> Any:
    """Get the registry singleton via the http_server module."""
    from itom_orchestrator.http_server import _get_registry as get_reg

    return get_reg()


def _get_health_checker() -> Any:
    """Get the health checker singleton via the http_server module."""
    from itom_orchestrator.http_server import _get_health_checker as get_hc

    return get_hc()


@router.get("/health")
async def get_health() -> dict[str, Any]:
    """Return the current health status of the ITOM Orchestrator.

    This endpoint is unauthenticated and intended for monitoring tools
    and the itom-chat-ui connection indicator. Chat-ui polls this endpoint
    to determine whether the orchestrator is reachable.

    Returns:
        JSON object with:
        - status: "healthy" or "degraded"
        - version: orchestrator version string
        - uptime_seconds: seconds since server start
        - connected_agents: count of agents with status "online"
        - total_registered_agents: total agents in registry
        - active_workflows: count of running workflows
        - data_dir: orchestrator data directory path
        - timestamp: ISO 8601 timestamp of this response
    """
    from itom_orchestrator.server import _get_orchestrator_health

    health = _get_orchestrator_health()
    logger.info(
        "HTTP health check served",
        extra={"extra_data": {"status": health.get("status")}},
    )
    return health


@router.get("/agents/status")
async def get_agents_status(
    force_check: bool = Query(
        default=False,
        description="If true, bypass health cache and perform fresh checks on all agents.",
    ),
) -> dict[str, Any]:
    """Return health status for all registered agents.

    Bridges to the existing health checker to provide per-agent status,
    aggregate statistics, and overall summary. When ``force_check=true``,
    performs fresh health checks instead of returning cached results.

    Args:
        force_check: If true, force fresh health checks for all agents.

    Returns:
        JSON object with:
        - agents: list of agent health records (agent_id, name, status, last_check)
        - total_agents: count of registered agents
        - status_summary: dict mapping status -> count
        - timestamp: ISO 8601 timestamp
    """
    health_checker = _get_health_checker()

    if force_check:
        # Run fresh checks on all agents
        health_checker.check_all(force=True)

    summary = health_checker.get_all_health()

    logger.info(
        "HTTP agents status served",
        extra={
            "extra_data": {
                "total_agents": summary.get("total_agents", 0),
                "force_check": force_check,
            }
        },
    )
    return summary


@router.get("/agents/{agent_id}")
async def get_agent_details(agent_id: str) -> dict[str, Any]:
    """Return detailed information for a specific agent.

    Bridges to the existing registry and health checker for full agent
    details including capabilities, metadata, and health history.

    Args:
        agent_id: The unique identifier of the agent (e.g., "cmdb-agent").

    Returns:
        JSON object with full agent details.

    Raises:
        HTTPException: 404 if the agent is not found.
    """
    from itom_orchestrator.server import _get_agent_details

    result = _get_agent_details(agent_id)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    logger.info(
        "HTTP agent details served",
        extra={"extra_data": {"agent_id": agent_id}},
    )
    return result


@router.get("/agents/{agent_id}/health")
async def get_agent_health(
    agent_id: str,
    force_check: bool = Query(
        default=False,
        description="If true, bypass health cache and perform a fresh check.",
    ),
) -> dict[str, Any]:
    """Return health status for a specific agent.

    Bridges to the existing health checker and provides the latest health
    check result, current status, and health statistics for the agent.

    When ``force_check=true``, performs a fresh health check instead of
    returning the cached result.

    Args:
        agent_id: The unique identifier of the agent.
        force_check: If true, force a fresh health check.

    Returns:
        JSON object with agent health info, latest check, and statistics.

    Raises:
        HTTPException: 404 if the agent is not found.
    """
    health_checker = _get_health_checker()

    try:
        # Run the check (uses cache unless forced)
        record = health_checker.check_agent(agent_id, force=force_check)
        health_info = health_checker.get_agent_health(agent_id)
        health_info["latest_check_result"] = record.to_dict()
    except Exception as exc:
        error_msg = str(exc)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg) from exc
        raise HTTPException(status_code=500, detail=error_msg) from exc

    logger.info(
        "HTTP agent health served",
        extra={
            "extra_data": {
                "agent_id": agent_id,
                "force_check": force_check,
                "result": record.result.value,
            }
        },
    )
    return health_info
