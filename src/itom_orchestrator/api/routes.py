"""
API route definitions for the ITOM Orchestrator HTTP server.

All routes are prefixed with ``/api`` and bridge to the same internal
logic used by the MCP tools. This ensures consistent behaviour between
MCP and HTTP interfaces.

Endpoints:
- GET  /api/health          -- orchestrator health (unauthenticated)
- GET  /api/agents/status   -- summary health for all agents
- GET  /api/agents/{id}     -- detailed info for a specific agent
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

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
    and the itom-chat-ui connection indicator.

    Returns:
        JSON object with status, version, uptime, and agent counts.
    """
    from itom_orchestrator.server import _get_orchestrator_health

    health = _get_orchestrator_health()
    logger.info(
        "HTTP health check served",
        extra={"extra_data": {"status": health.get("status")}},
    )
    return health


@router.get("/agents/status")
async def get_agents_status() -> dict[str, Any]:
    """Return health status for all registered agents.

    Bridges to the existing health checker to provide per-agent status,
    aggregate statistics, and overall summary.

    Returns:
        JSON object with agents list, status summary, and total count.
    """
    health_checker = _get_health_checker()
    summary = health_checker.get_all_health()

    logger.info(
        "HTTP agents status served",
        extra={"extra_data": {"total_agents": summary.get("total_agents", 0)}},
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
