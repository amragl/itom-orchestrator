"""
HTTP dispatch handlers for ITOM agents.

Registers dispatch handlers with the TaskExecutor so that routed tasks
are forwarded to the actual agent MCP servers via HTTP. Each handler
uses the FastMCP Client to call the agent's tools over streamable-http
transport.

Usage:
    from itom_orchestrator.agent_dispatch import register_all_handlers
    register_all_handlers()
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.tasks import Task

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Thread pool for running async MCP client calls from synchronous handlers.
# Needed because the executor calls handlers synchronously, but FastMCP
# Client is async. We can't use asyncio.run() inside FastAPI's event loop.
_thread_pool = ThreadPoolExecutor(max_workers=4)


def _call_mcp_tool_sync(server_url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    """Call an MCP tool on a remote server synchronously.

    Uses FastMCP Client with streamable-http transport to invoke a tool
    and return its result. Runs the async call in a separate thread to
    avoid conflicts with any existing event loop (e.g. FastAPI).

    Args:
        server_url: Base URL of the MCP server (e.g. http://localhost:8002/mcp).
        tool_name: The MCP tool name to call.
        arguments: Tool arguments as a dictionary.

    Returns:
        The tool result (parsed from the MCP response).
    """

    def _run_in_new_loop() -> Any:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from fastmcp import Client

            async def _call() -> Any:
                async with Client(server_url) as client:
                    return await client.call_tool(tool_name, arguments)

            return loop.run_until_complete(_call())
        finally:
            loop.close()

    future = _thread_pool.submit(_run_in_new_loop)
    return future.result(timeout=30)


# CI type keywords used to infer the CI class from a natural language message.
_CI_TYPE_KEYWORDS: dict[str, list[str]] = {
    "server": ["server", "linux", "windows", "host", "vm", "virtual machine"],
    "database": ["database", "db", "oracle", "mysql", "postgres", "sql"],
    "application": ["application", "app", "service", "web app"],
    "network_gear": ["network", "switch", "router", "firewall", "load balancer"],
    "storage": ["storage", "san", "nas", "disk", "volume"],
}


def _infer_ci_type(message_lower: str) -> str | None:
    """Infer the CI type from a chat message.

    Scans the lowercased message for keywords associated with each CI type.
    Returns the first match, or None to search across all types.
    """
    for ci_type, keywords in _CI_TYPE_KEYWORDS.items():
        if any(kw in message_lower for kw in keywords):
            return ci_type
    return None


def _extract_name_hint(message: str, ci_type: str | None) -> str | None:
    """Try to extract a useful name-search pattern from the message.

    Strips common command verbs and the primary CI type name, but keeps
    descriptive qualifiers (like "Linux", "Oracle", "production") that are
    meaningful search filters.
    """
    words_to_strip = {
        "search", "find", "look", "up", "query", "for", "all", "the",
        "show", "me", "list", "get", "a", "an", "in", "on", "with",
        "my", "our", "any", "some", "every",
    }
    if ci_type:
        # Only strip the primary CI type name and its plural — keep qualifiers
        # like "linux", "oracle", "windows" which are useful name filters.
        words_to_strip.add(ci_type)
        words_to_strip.add(ci_type + "s")
        words_to_strip.add(ci_type + "es")
        # Also strip the multi-word CI type names
        if ci_type == "network_gear":
            words_to_strip.update({"network", "gear"})

    words = message.split()
    remaining = [w for w in words if w.lower() not in words_to_strip]
    hint = " ".join(remaining).strip()
    return hint if hint else None


def _make_cmdb_handler(server_url: str) -> Any:
    """Create a dispatch handler for the CMDB agent.

    The handler analyzes the chat message to pick the best CMDB tool,
    calls it on the MCP server, and returns the result.

    Args:
        server_url: URL of the CMDB MCP server.

    Returns:
        A callable(Task) -> dict handler for the executor.
    """

    def handler(task: Task) -> dict[str, Any]:
        message = task.description or task.title
        message_lower = message.lower()

        # Map chat intent to CMDB MCP tools based on keywords.
        # Tool names and argument schemas must match the MCP server exactly.
        # IMPORTANT: Specific commands are checked FIRST, generic search LAST.
        tool_name: str | None = None
        arguments: dict[str, Any] = {}

        # --- Specific tool commands (highest priority) ---
        if any(kw in message_lower for kw in ["dashboard", "metrics", "overview"]):
            tool_name = "get_operational_dashboard"
        elif any(kw in message_lower for kw in ["health check", "server health"]):
            tool_name = "check_server_health"
        elif any(kw in message_lower for kw in ["compliance"]):
            tool_name = "run_compliance_check"
        elif any(kw in message_lower for kw in ["audit", "quality"]):
            tool_name = "get_audit_summary"
        elif any(kw in message_lower for kw in ["stale", "outdated"]):
            tool_name = "find_stale_configuration_items"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type, "days": 90}
        elif any(kw in message_lower for kw in ["duplicate", "dedup"]):
            tool_name = "find_duplicate_configuration_items"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["relationship", "relations", "upstream", "downstream"]):
            tool_name = "query_ci_relationships"
            arguments = {"sys_id": message}
        elif any(kw in message_lower for kw in ["detail", "info about"]):
            tool_name = "get_configuration_item_details"
            arguments = {"identifier": message}
        elif any(kw in message_lower for kw in ["count", "how many"]):
            tool_name = "get_operational_dashboard"
        elif any(kw in message_lower for kw in ["health", "status"]):
            tool_name = "check_server_health"

        # --- Generic search (lowest priority, fallback) ---
        if tool_name is None:
            tool_name = "search_configuration_items"
            ci_type = _infer_ci_type(message_lower)
            arguments = {"ci_type": ci_type, "limit": 10}
            name_hint = _extract_name_hint(message, ci_type)
            if name_hint:
                arguments["name"] = f"*{name_hint}*"

        logger.info(
            "CMDB dispatch: routing to tool",
            extra={
                "extra_data": {
                    "task_id": task.task_id,
                    "tool_name": tool_name,
                    "server_url": server_url,
                }
            },
        )

        try:
            result = _call_mcp_tool_sync(server_url, tool_name, arguments)

            # FastMCP Client returns a CallToolResult with a .content list
            # of TextContent/ImageContent/etc objects. Extract text from them.
            content_items = getattr(result, "content", None)
            if content_items is None:
                # Fallback: maybe it's already a list or plain value
                content_items = result if isinstance(result, list) else [result]

            texts = []
            for item in content_items:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
                else:
                    texts.append(str(item))

            return {
                "agent_response": "\n".join(texts),
                "tool_used": tool_name,
                "source": "cmdb-mcp-server",
            }

        except Exception as exc:
            logger.error(
                "CMDB dispatch failed",
                extra={
                    "extra_data": {
                        "task_id": task.task_id,
                        "tool_name": tool_name,
                        "error": str(exc),
                    }
                },
            )
            raise RuntimeError(f"CMDB agent call failed: {exc}") from exc

    return handler


def register_all_handlers() -> None:
    """Register dispatch handlers for all configured agent endpoints.

    Reads agent endpoint URLs from the orchestrator config and registers
    handlers with the TaskExecutor.
    """
    from itom_orchestrator.executor import TaskExecutor

    config = get_config()

    cmdb_url = config.cmdb_agent_url
    if cmdb_url:
        logger.info(
            "Registering CMDB dispatch handler",
            extra={"extra_data": {"url": cmdb_url}},
        )
        TaskExecutor.register_dispatch_handler("cmdb-agent", _make_cmdb_handler(cmdb_url))
    else:
        logger.info("No CMDB agent URL configured, skipping dispatch handler")
