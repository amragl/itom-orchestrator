"""
FastMCP server for the ITOM Orchestrator.

This is the MCP server entry point. MCP tools are registered on the ``mcp``
instance and exposed to connected clients.

Registered tools:
- get_orchestrator_health -- server health and uptime information

Future tools (subsequent tickets):
- get_agent_registry / get_agent_status
- route_task
- execute_workflow / get_workflow_status
- send_agent_message
- enforce_role_boundaries
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP

from itom_orchestrator import __version__
from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import get_structured_logger

mcp = FastMCP("itom-orchestrator")

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Captured at module load time -- used to compute uptime in health checks.
_server_start_time: float = time.monotonic()


def _get_orchestrator_health() -> dict[str, Any]:
    """Return the current health status of the ITOM Orchestrator.

    This is the implementation function. It is registered as an MCP tool via
    :data:`get_orchestrator_health` and can also be called directly in tests.

    Returns:
        Dictionary with health status fields including status, version,
        uptime, connected agent count, active workflow count, data directory,
        and an ISO 8601 timestamp.
    """
    config = get_config()
    uptime = time.monotonic() - _server_start_time
    now = datetime.now(tz=UTC).isoformat()

    health: dict[str, Any] = {
        "status": "healthy",
        "version": __version__,
        "uptime_seconds": round(uptime, 3),
        "connected_agents": 0,
        "active_workflows": 0,
        "data_dir": config.data_dir,
        "timestamp": now,
    }

    logger.info(
        "Health check completed",
        extra={"extra_data": health},
    )

    return health


# Register the tool on the MCP server. The decorator returns a FunctionTool
# object; the raw implementation remains available as ``_get_orchestrator_health``.
get_orchestrator_health = mcp.tool()(_get_orchestrator_health)
