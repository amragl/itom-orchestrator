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
- POST /api/chat                    -- route chat messages to agents

This module implements ORCH-026, ORCH-027, and ORCH-028.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from itom_orchestrator.api.chat import ChatRequest, ChatResponse, process_chat_message
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


@router.post("/chat", response_model=ChatResponse)
async def post_chat(request: ChatRequest) -> ChatResponse:
    """Route a chat message to the appropriate ITOM agent.

    Receives a message from itom-chat-ui, routes it to the best agent
    based on domain hints, keywords, or explicit targeting, and returns
    the agent's response.

    Error responses:
    - 400: Invalid request (empty message, invalid domain)
    - 502: Agent routing or execution failure
    - 504: Agent execution timed out

    Args:
        request: Chat message with optional routing hints.

    Returns:
        ChatResponse with the agent's response and routing metadata.
    """
    from itom_orchestrator.executor import ExecutionError, TaskTimeoutError
    from itom_orchestrator.router import RoutingError
    from itom_orchestrator.server import _get_executor, _get_router

    executor = _get_executor()
    task_router = _get_router()

    try:
        response = process_chat_message(
            request=request,
            router=task_router,
            executor=executor,
        )
        return response

    except ValueError as exc:
        # Invalid domain or request validation error -> 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except RoutingError as exc:
        # No agent found — return a helpful response instead of 502.
        # This commonly happens in "Auto" mode when the message does not
        # contain keywords matching any routing rule.
        logger.warning(
            "Chat routing failed",
            extra={
                "extra_data": {
                    "error_code": exc.error_code,
                    "message": exc.message,
                }
            },
        )

        # For "no route found" errors, return a helpful chat response
        # instead of an HTTP error so the UI can display it gracefully.
        from itom_orchestrator.router import NoRouteFoundError

        if isinstance(exc, NoRouteFoundError):
            return ChatResponse(
                message_id=f"chat-{datetime.now(UTC).strftime('%H%M%S')}",
                status="success",
                agent_id="orchestrator",
                agent_name="Orchestrator",
                domain="general",
                response={
                    "task_id": None,
                    "result": {
                        "agent_response": (
                            "I'm not sure which agent can help with that. "
                            "Try asking about:\n\n"
                            "- **CMDB** — search CIs, health metrics, "
                            "compliance, relationships\n"
                            "- **Discovery** — network scans, IP ranges\n"
                            "- **Assets** — inventory, licenses, hardware\n"
                            "- **Audit** — compliance, drift, policies\n"
                            "- **Documentation** — runbooks, architecture\n\n"
                            "You can also type `/help` to see all available slash commands."
                        ),
                    },
                    "routing": {"method": "fallback", "reason": exc.message},
                },
                routing_method="fallback",
                timestamp=datetime.now(UTC).isoformat(),
            )

        # For other routing errors (agent unavailable, etc.) -> 502
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": exc.error_code,
                "error_message": exc.message,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ) from exc

    except TaskTimeoutError as exc:
        # Agent timed out -> 504
        logger.warning(
            "Chat execution timed out",
            extra={"extra_data": {"task_id": exc.task_id}},
        )
        raise HTTPException(
            status_code=504,
            detail={
                "error_code": exc.error_code,
                "error_message": exc.message,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ) from exc

    except ExecutionError as exc:
        # Other execution failures -> 502
        logger.warning(
            "Chat execution failed",
            extra={
                "extra_data": {
                    "error_code": exc.error_code,
                    "task_id": exc.task_id,
                }
            },
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": exc.error_code,
                "error_message": exc.message,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ) from exc
