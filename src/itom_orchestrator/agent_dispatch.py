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
import json
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


def _format_dict_value(v: Any) -> str:
    """Format a value for display, handling nested dicts and lists."""
    if isinstance(v, dict):
        parts = [f"{k}: {v2}" for k, v2 in v.items()]
        return ", ".join(parts)
    if isinstance(v, list):
        if len(v) <= 5:
            return ", ".join(str(x) for x in v)
        return f"{len(v)} items"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _format_cmdb_response(tool_name: str, raw_text: str) -> str:
    """Format raw CMDB tool JSON into a human-readable chat response."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text

    if tool_name == "_count_only":
        # Count-only mode: just show the total from a search result
        if isinstance(data, dict):
            total = data.get("total_count", data.get("total", 0))
            ci_types = data.get("ci_types_searched", [])
            ci_label = ci_types[0] if ci_types else "configuration items"
            ci_label = ci_label.replace("cmdb_ci_", "").replace("_", " ") + "s"
        else:
            total = 0
            ci_label = "configuration items"
        return f"**{total}** {ci_label} found in the CMDB."

    if tool_name == "check_server_health":
        status = data.get("status", "unknown")
        uptime = data.get("uptime", {}).get("formatted", "unknown")
        lines = [f"**CMDB Health: {status.upper()}**", f"Uptime: {uptime}", ""]
        for name, check in data.get("checks", {}).items():
            check_status = check.get("status", "unknown")
            icon = "OK" if check_status == "healthy" else "WARN"
            lines.append(f"  [{icon}] **{name}**")
            if name == "servicenow":
                lines.append(f"       Instance: {check.get('instance', 'N/A')}")
                lines.append(f"       Latency: {check.get('latency_ms', 'N/A')}ms")
                lines.append(f"       Auth: {'valid' if check.get('auth_valid') else 'invalid'}")
            elif name == "cache":
                lines.append(f"       Hit rate: {check.get('hit_rate', 0)}% ({check.get('hits', 0)} hits / {check.get('misses', 0)} misses)")
                lines.append(f"       Size: {check.get('size', 0)} entries, TTL: {check.get('ttl_seconds', 0)}s")
            elif name == "session_pool":
                lines.append(f"       Connections: {check.get('pool_connections', 'N/A')}, Max: {check.get('pool_maxsize', 'N/A')}")
        return "\n".join(lines)

    if tool_name == "get_operational_dashboard":
        lines = ["**CMDB Operational Dashboard**", ""]
        instance_info = data.get("instance", {})
        uptime_info = data.get("uptime", {})
        if isinstance(instance_info, dict):
            lines.append(f"Instance: {instance_info.get('instance_url', 'N/A')}")
        else:
            lines.append(f"Instance: {instance_info}")
        if isinstance(uptime_info, dict):
            lines.append(f"Uptime: {uptime_info.get('formatted', 'N/A')}")
        else:
            lines.append(f"Uptime: {uptime_info}")
        lines.append("")
        # Show key sections, skip internal metadata
        skip_keys = {"timestamp", "instance", "uptime", "generation_time_ms", "tracing"}
        if isinstance(data, dict):
            for section, values in data.items():
                if section in skip_keys:
                    continue
                lines.append(f"**{section.replace('_', ' ').title()}**")
                if isinstance(values, dict):
                    for k, v in values.items():
                        label = k.replace("_", " ").title()
                        lines.append(f"  {label}: {_format_dict_value(v)}")
                elif isinstance(values, list):
                    for item in values[:5]:
                        lines.append(f"  - {item}")
                else:
                    lines.append(f"  {values}")
                lines.append("")
        return "\n".join(lines)

    if tool_name == "get_audit_summary":
        lines = ["**CMDB Audit Summary**", ""]
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    lines.append(f"**{k.replace('_', ' ').title()}**")
                    for sk, sv in v.items():
                        lines.append(f"  {sk.replace('_', ' ').title()}: {sv}")
                    lines.append("")
                else:
                    lines.append(f"  {k.replace('_', ' ').title()}: {v}")
        return "\n".join(lines)

    if tool_name == "search_configuration_items":
        if isinstance(data, dict):
            results = data.get("result", data.get("results", data.get("items", [])))
            total = data.get("total_count", data.get("total", data.get("count", len(results))))
        elif isinstance(data, list):
            results = data
            total = len(results)
        else:
            return raw_text

        lines = [f"**Found {total} configuration item(s)**", ""]
        for i, ci in enumerate(results[:15], 1):
            if isinstance(ci, dict):
                name = ci.get("name", ci.get("display_name", "Unnamed"))
                ci_class = ci.get("sys_class_name", ci.get("ci_type", ""))
                op_status = ci.get("operational_status", "")
                ip = ci.get("ip_address", "")
                os_name = ci.get("os", "")
                line = f"  {i}. **{name}**"
                details = []
                if ci_class:
                    details.append(ci_class)
                if os_name:
                    details.append(os_name)
                if op_status:
                    details.append(f"status: {op_status}")
                if ip:
                    details.append(ip)
                if details:
                    line += f"  ({', '.join(details)})"
                lines.append(line)
        if isinstance(total, int) and total > 15:
            lines.append(f"\n  ... and {total - 15} more")
        return "\n".join(lines)

    if tool_name == "find_stale_configuration_items":
        if isinstance(data, dict):
            results = data.get("stale_cis", data.get("result", []))
            total = data.get("stale_count", data.get("total_count", len(results)))
            cutoff = data.get("cutoff_date", "")
        elif isinstance(data, list):
            results = data
            total = len(results)
            cutoff = ""
        else:
            return raw_text
        header = f"**Found {total} stale CI(s)**"
        if cutoff:
            header += f"  (not updated since {cutoff})"
        lines = [header, ""]
        for i, ci in enumerate(results[:10], 1):
            if isinstance(ci, dict):
                name = ci.get("name", "Unnamed")
                updated = ci.get("sys_updated_on", "")
                detail = f"  (last updated: {updated})" if updated else ""
                lines.append(f"  {i}. {name}{detail}")
        if isinstance(total, int) and total > 10:
            lines.append(f"\n  ... and {total - 10} more")
        return "\n".join(lines)

    if tool_name == "find_duplicate_configuration_items":
        if isinstance(data, dict):
            duplicates = data.get("duplicates", {})
            total = data.get("duplicate_count", len(duplicates))
            match_field = data.get("match_field", "name")
        else:
            return raw_text
        lines = [f"**Found {total} duplicate group(s)** (matched by {match_field})", ""]
        # duplicates is a dict: {name: [list of CIs with that name]}
        if isinstance(duplicates, dict):
            for i, (name, cis) in enumerate(list(duplicates.items())[:10], 1):
                count = len(cis) if isinstance(cis, list) else "?"
                lines.append(f"  {i}. **{name}** — {count} copies")
        elif isinstance(duplicates, list):
            for i, ci in enumerate(duplicates[:10], 1):
                name = ci.get("name", "Unnamed") if isinstance(ci, dict) else str(ci)
                lines.append(f"  {i}. {name}")
        if isinstance(total, int) and total > 10:
            lines.append(f"\n  ... and {total - 10} more groups")
        return "\n".join(lines)

    if tool_name == "run_compliance_check":
        lines = ["**CMDB Compliance Check**", ""]
        if isinstance(data, dict):
            overall = data.get("overall_status", data.get("status", "unknown"))
            lines.append(f"  Overall: **{overall.upper()}**")
            for k, v in data.items():
                if k in ("overall_status", "status"):
                    continue
                if isinstance(v, dict):
                    lines.append(f"\n  **{k.replace('_', ' ').title()}**")
                    for sk, sv in v.items():
                        lines.append(f"    {sk.replace('_', ' ').title()}: {sv}")
                elif isinstance(v, list):
                    lines.append(f"\n  **{k.replace('_', ' ').title()}**: {len(v)} items")
                else:
                    lines.append(f"  {k.replace('_', ' ').title()}: {v}")
        return "\n".join(lines)

    # Fallback: return raw text for unhandled tools
    return raw_text


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
            tool_name = "search_configuration_items"
            ci_type = _infer_ci_type(message_lower)
            arguments = {"ci_type": ci_type, "limit": 1}
            arguments["_count_only"] = True
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

        # Extract internal flags before sending to MCP
        count_only = arguments.pop("_count_only", False)

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

            raw_response = "\n".join(texts)
            if count_only:
                formatted = _format_cmdb_response("_count_only", raw_response)
            else:
                formatted = _format_cmdb_response(tool_name, raw_response)

            return {
                "agent_response": formatted,
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
