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
# Include plural forms for whole-word matching.
_CI_TYPE_KEYWORDS: dict[str, list[str]] = {
    "server": ["server", "servers", "linux", "windows", "host", "hosts", "vm", "vms", "virtual machine"],
    "database": ["database", "databases", "db", "dbs", "oracle", "mysql", "postgres", "sql"],
    "application": ["application", "applications", "app", "apps", "service", "services", "web app"],
    "network_gear": ["network", "switch", "switches", "router", "routers", "firewall", "firewalls", "load balancer"],
    "storage": ["storage", "san", "nas", "disk", "disks", "volume", "volumes"],
}


def _infer_ci_type(message_lower: str) -> str | None:
    """Infer the CI type from a chat message.

    Scans the lowercased message for keywords associated with each CI type.
    Uses word-boundary matching to avoid false positives (e.g. "db" in "cmdb").
    Returns the first match, or None to search across all types.
    """
    import re

    words = set(re.findall(r"\b\w+\b", message_lower))
    for ci_type, keywords in _CI_TYPE_KEYWORDS.items():
        for kw in keywords:
            # Multi-word keywords: check substring match
            if " " in kw:
                if kw in message_lower:
                    return ci_type
            # Single-word keywords: check whole-word match
            elif kw in words:
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
    if not hint:
        return None

    # Guard: if hint looks like a description rather than a CI name, discard it.
    # Real CI names are short (e.g. "web-server-01", "db-prod-03") and don't
    # contain generic command / filter vocabulary.
    _NON_NAME_WORDS = {
        "create", "dashboard", "eol", "without", "missing", "by",
        "criticality", "production", "development", "staging",
        "environment", "all", "list", "search", "show", "find",
        "generate", "report", "analyze", "display", "build", "make",
    }
    hint_words = hint.lower().split()
    if len(hint_words) > 3 or any(w in _NON_NAME_WORDS for w in hint_words):
        return None

    return hint


def _normalize_message(message: str) -> str:
    """Strip leading /command prefixes from chat messages.

    Users may type ``/ci-search production servers`` where the ``/ci-search``
    prefix is a UI command, not part of the query.  This function removes
    the leading ``/word`` token so downstream parsing only sees the query.
    """
    import re

    return re.sub(r"^/\S+\s*", "", message).strip()


def _extract_environment(message_lower: str) -> str | None:
    """Extract a ServiceNow environment value from a chat message.

    Maps common natural-language environment references to the canonical
    ServiceNow ``environment`` field values used by ``search_configuration_items``.
    """
    if any(kw in message_lower for kw in ["production", " prod "]):
        return "Production"
    if any(kw in message_lower for kw in [" dev ", "development"]):
        return "Development"
    if any(kw in message_lower for kw in ["staging", " stage ", "uat"]):
        return "Staging"
    if any(kw in message_lower for kw in [" test ", "testing env"]):
        return "Test"
    return None


def _extract_custom_query(message_lower: str) -> str | None:
    """Build a ServiceNow encoded query for 'missing field' filters.

    Recognises phrases like "without serial number", "no owner", etc. and
    converts them to ``fieldISEMPTY`` encoded-query fragments joined with ``^``.
    """
    parts: list[str] = []
    if any(kw in message_lower for kw in [
        "without sn", "no sn", "missing sn",
        "without serial", "no serial", "missing serial",
        "without serial number", "no serial number",
    ]):
        parts.append("serial_numberISEMPTY")
    if any(kw in message_lower for kw in [
        "without owner", "no owner", "missing owner", "unowned",
    ]):
        parts.append("owned_byISEMPTY")
    if any(kw in message_lower for kw in [
        "without os", "no os", "missing os",
    ]):
        parts.append("osISEMPTY")
    return "^".join(parts) if parts else None


def _extract_identifier(message: str) -> str | None:
    """Extract a CI identifier (sys_id or name) from a message.

    Looks for a 32-char hex string (sys_id) first, then falls back to
    a short, meaningful phrase that looks like a CI name (e.g. "web-server-01").
    Returns ``None`` when no plausible identifier is found so callers can
    display a helpful prompt instead of sending garbage to the MCP server.
    """
    import re

    # Check for sys_id (32-char hex)
    match = re.search(r"\b[0-9a-f]{32}\b", message.lower())
    if match:
        return match.group()

    # Strip common command words and see what's left.
    # Words are compared after stripping punctuation so "change." matches "change".
    filler = {
        "show", "get", "find", "the", "of", "for", "a", "an", "me",
        "do", "run", "can", "i", "my", "our", "please", "want", "need",
        "what", "how", "is", "are", "would", "will", "so", "just",
        "details", "detail", "info", "about", "history", "changes",
        "to", "impact", "analysis", "dependency", "tree", "dependencies",
        "relationships", "relationship", "relations", "state", "compare",
        "ci", "configuration", "item", "full", "dry", "review", "change",
        "across", "all", "without", "action", "plan", "validation",
        "validate", "check", "perform", "generate", "report", "give",
        "list", "query", "search", "look", "up", "and", "or", "but",
        "that", "this", "it", "its", "with", "from", "on", "in", "at",
    }
    words = message.split()
    remaining = [w for w in words if re.sub(r"[^\w]", "", w).lower() not in filler]
    candidate = " ".join(remaining).strip().strip(".,!?;:\"'")

    # A plausible CI identifier is short (1-4 words) and not a generic
    # sentence fragment.  If what remains is too long or empty, give up.
    if not candidate or len(candidate.split()) > 4:
        return None

    return candidate


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


def _to_chat_markdown(lines: list[str]) -> str:
    """Join formatter lines into markdown suitable for react-markdown.

    Converts indented plain-text lines to proper markdown structure:
    - Empty strings → paragraph breaks (blank line)
    - Lines starting with ``  - `` → ``- `` list items (de-indent)
    - Lines with 4+ leading spaces → ``  - `` nested list items
    - Lines with 2 leading spaces → ``- `` list items
    - Everything else → as-is
    """
    out: list[str] = []
    for line in lines:
        if not line:
            out.append("")
        elif line.startswith("  - "):
            out.append("- " + line[4:])
        elif line.startswith("       "):
            out.append("  - " + line.strip())
        elif line.startswith("    "):
            out.append("  - " + line.strip())
        elif line.startswith("  "):
            out.append("- " + line[2:])
        else:
            out.append(line)
    return "\n".join(out)


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
        return _to_chat_markdown(lines)

    if tool_name == "get_cmdb_health_metrics":
        ci_type = data.get("ci_type", "all")
        summary = data.get("summary", {})
        score = summary.get("overall_health_score", "N/A")
        inventory = data.get("inventory_kpis", {})
        quality = data.get("data_quality_kpis", {})
        discovery = data.get("discovery_kpis", {})
        relationships = data.get("relationship_kpis", {})
        lifecycle = data.get("lifecycle_kpis", {})

        lines = [f"**CMDB Health Report — {ci_type}s**", ""]

        # Overall score
        grade = quality.get("grade", "N/A")
        lines.append(f"  Health Score: **{score}/100** (Data Quality Grade: **{grade}**)")
        lines.append("")

        # Inventory
        total = inventory.get("total_count", 0)
        by_env = inventory.get("by_environment", {})
        virt = inventory.get("virtual_vs_physical", {})
        lines.append(f"**Inventory** ({total} total)")
        if by_env:
            env_parts = [f"{env}: {count}" for env, count in by_env.items() if env != "Unknown"]
            if env_parts:
                lines.append(f"  Environment: {', '.join(env_parts)}")
        if virt:
            lines.append(f"  Virtual: {virt.get('virtual', 0)}, Physical: {virt.get('physical', 0)}")
        created = inventory.get("created_last_30_days", 0)
        if created:
            lines.append(f"  Created last 30 days: {created}")
        lines.append("")

        # Data Quality
        completeness = quality.get("completeness_score", 0)
        complete = quality.get("complete_count", 0)
        incomplete = quality.get("incomplete_count", 0)
        lines.append(f"**Data Quality** ({completeness:.0f}% complete)")
        lines.append(f"  Complete: {complete}, Incomplete: {incomplete}")
        for field_key in ("missing_serial_number", "missing_os", "missing_owner"):
            field_data = quality.get(field_key, {})
            if isinstance(field_data, dict) and field_data.get("count", 0) > 0:
                label = field_key.replace("missing_", "Missing ").replace("_", " ")
                lines.append(f"  {label}: {field_data['count']}")
        lines.append("")

        # Discovery
        coverage = discovery.get("discovery_coverage_percent", 0)
        never = discovery.get("never_discovered", {})
        never_count = never.get("count", 0) if isinstance(never, dict) else never
        stale_90 = discovery.get("stale_90_plus_days", {})
        stale_90_count = stale_90.get("count", 0) if isinstance(stale_90, dict) else stale_90
        lines.append(f"**Discovery** ({coverage:.0f}% coverage)")
        if never_count:
            lines.append(f"  Never discovered: {never_count}")
        if stale_90_count:
            lines.append(f"  Stale 90+ days: {stale_90_count}")
        by_source = discovery.get("by_source", {})
        if by_source:
            src_parts = [f"{src}: {cnt}" for src, cnt in by_source.items() if src != "Unknown"]
            if src_parts:
                lines.append(f"  Sources: {', '.join(src_parts)}")
        lines.append("")

        # Relationships
        orphans = relationships.get("orphan_cis", {})
        orphan_count = orphans.get("count", 0) if isinstance(orphans, dict) else orphans
        biz_svc = relationships.get("mapped_to_business_service_percent", 0)
        avg_rels = relationships.get("avg_relationships_per_ci", 0)
        lines.append(f"**Relationships** (avg {avg_rels:.1f} per CI)")
        lines.append(f"  Mapped to business service: {biz_svc:.0f}%")
        if orphan_count:
            lines.append(f"  Orphan CIs (no relationships): {orphan_count}")
        lines.append("")

        # Priority issues from summary
        issues = summary.get("priority_issues", [])
        if issues:
            lines.append("**Priority Issues**")
            for issue in issues[:5]:
                if isinstance(issue, dict):
                    text = issue.get("issue", str(issue))
                    impact = issue.get("impact", "")
                    impact_tag = f" [{impact.upper()}]" if impact else ""
                    lines.append(f"  - {text}{impact_tag}")
                else:
                    lines.append(f"  - {issue}")

        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

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
        return _to_chat_markdown(lines)

    if tool_name == "get_cmdb_health_trend_report":
        lines = ["**CMDB Health Trend Report**", ""]
        if isinstance(data, dict):
            lookback = data.get("lookback_days", "N/A")
            snapshots = data.get("snapshots", data.get("trend_data", []))
            lines.append(f"  Lookback: {lookback} days, Snapshots: {len(snapshots)}")
            lines.append("")
            if isinstance(snapshots, list):
                for snap in snapshots[:10]:
                    if isinstance(snap, dict):
                        ts = snap.get("timestamp", snap.get("captured_at", ""))
                        score = snap.get("overall_health_score", snap.get("score", "N/A"))
                        lines.append(f"  {ts}: score {score}")
            trends = data.get("trends", {})
            if isinstance(trends, dict):
                for k, v in trends.items():
                    lines.append(f"  {k.replace('_', ' ').title()}: {_format_dict_value(v)}")
        return _to_chat_markdown(lines)

    if tool_name == "reconcile_cmdb_configuration_data":
        lines = ["**CMDB Data Reconciliation**", ""]
        if isinstance(data, dict):
            for check_name, check_data in data.items():
                if isinstance(check_data, dict):
                    status = check_data.get("status", "")
                    count = check_data.get("count", check_data.get("total", ""))
                    lines.append(f"  **{check_name.replace('_', ' ').title()}**: {status}")
                    if count:
                        lines.append(f"    Issues found: {count}")
                    issues = check_data.get("issues", check_data.get("items", []))
                    if isinstance(issues, list):
                        for item in issues[:5]:
                            if isinstance(item, dict):
                                lines.append(f"    - {item.get('name', item.get('sys_id', str(item)))}")
                    lines.append("")
        return _to_chat_markdown(lines)

    if tool_name == "query_ci_dependency_tree":
        lines = ["**CI Dependency Tree**", ""]
        if isinstance(data, dict):
            root = data.get("root", data.get("ci", {}))
            if isinstance(root, dict):
                lines.append(f"  Root: **{root.get('name', root.get('sys_id', 'unknown'))}**")
            tree = data.get("tree", data.get("children", data.get("dependencies", [])))
            if isinstance(tree, list):
                for node in tree[:15]:
                    if isinstance(node, dict):
                        name = node.get("name", node.get("display_name", ""))
                        rel = node.get("relationship_type", node.get("type", ""))
                        depth = node.get("depth", 0)
                        indent = "  " * (depth + 1)
                        lines.append(f"  {indent}{'└─' if depth else '├─'} {name} ({rel})")
            total = data.get("total_nodes", len(tree) if isinstance(tree, list) else 0)
            lines.append(f"\n  Total nodes: {total}")
        return _to_chat_markdown(lines)

    if tool_name == "analyze_configuration_item_impact":
        lines = ["**CI Impact Analysis**", ""]
        if isinstance(data, dict):
            ci = data.get("ci", data.get("target", {}))
            if isinstance(ci, dict):
                lines.append(f"  Target: **{ci.get('name', 'unknown')}**")
            change_type = data.get("change_type", "unknown")
            lines.append(f"  Change type: {change_type}")
            lines.append("")
            impacted = data.get("impacted_cis", data.get("impact", []))
            if isinstance(impacted, list):
                lines.append(f"  **Impacted CIs: {len(impacted)}**")
                for item in impacted[:10]:
                    if isinstance(item, dict):
                        name = item.get("name", "unknown")
                        svc = item.get("sys_class_name", "")
                        lines.append(f"    - {name} ({svc})" if svc else f"    - {name}")
        return _to_chat_markdown(lines)

    if tool_name == "get_configuration_item_history":
        lines = ["**CI Change History**", ""]
        if isinstance(data, dict):
            history = data.get("history", data.get("entries", data.get("changes", [])))
            if isinstance(history, list):
                lines.append(f"  {len(history)} change(s) found")
                lines.append("")
                for entry in history[:15]:
                    if isinstance(entry, dict):
                        ts = entry.get("sys_updated_on", entry.get("timestamp", ""))
                        field = entry.get("field", entry.get("fieldname", ""))
                        old = entry.get("old_value", entry.get("oldvalue", ""))
                        new = entry.get("new_value", entry.get("newvalue", ""))
                        user = entry.get("user", entry.get("sys_updated_by", ""))
                        line = f"  {ts}: **{field}** changed"
                        if old and new:
                            line += f" from '{old}' to '{new}'"
                        elif new:
                            line += f" to '{new}'"
                        if user:
                            line += f" (by {user})"
                        lines.append(line)
        return _to_chat_markdown(lines)

    if tool_name == "list_ci_types":
        lines = ["**Available CI Types**", ""]
        if isinstance(data, dict):
            for ci_type, info in data.items():
                if isinstance(info, dict):
                    fields = info.get("fields", info.get("available_fields", []))
                    field_count = len(fields) if isinstance(fields, list) else "?"
                    lines.append(f"  **{ci_type}** — {field_count} fields")
                else:
                    lines.append(f"  **{ci_type}**")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    lines.append(f"  **{item.get('name', item.get('type', str(item)))}**")
                else:
                    lines.append(f"  **{item}**")
        return _to_chat_markdown(lines)

    if tool_name == "list_relationship_types_available":
        lines = ["**Available Relationship Types**", ""]
        if isinstance(data, dict):
            rel_types = data.get("relationship_types", data.get("types", data))
            if isinstance(rel_types, list):
                for rt in rel_types:
                    if isinstance(rt, dict):
                        name = rt.get("name", rt.get("type", "unknown"))
                        desc = rt.get("description", "")
                        lines.append(f"  - **{name}**" + (f": {desc}" if desc else ""))
                    else:
                        lines.append(f"  - {rt}")
            elif isinstance(rel_types, dict):
                for name, info in rel_types.items():
                    lines.append(f"  - **{name}**: {_format_dict_value(info)}")
        return _to_chat_markdown(lines)

    if tool_name in ("list_ci_classes_with_ire", "get_ire_rules_for_class"):
        title = "CI Classes with IRE" if "list" in tool_name else "IRE Rules"
        lines = [f"**{title}**", ""]
        if isinstance(data, dict):
            for k, v in data.items():
                lines.append(f"  **{k}**: {_format_dict_value(v)}")
        elif isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict):
                    name = item.get("name", item.get("ci_class", str(item)))
                    lines.append(f"  - {name}")
                else:
                    lines.append(f"  - {item}")
        return _to_chat_markdown(lines)

    if tool_name in ("query_audit_log", "get_ci_activity_log"):
        title = "Audit Log" if "audit" in tool_name else "CI Activity Log"
        lines = [f"**{title}**", ""]
        entries = data if isinstance(data, list) else data.get("entries", data.get("results", []))
        if isinstance(entries, list):
            lines.append(f"  {len(entries)} entries")
            lines.append("")
            for entry in entries[:15]:
                if isinstance(entry, dict):
                    ts = entry.get("timestamp", entry.get("sys_updated_on", ""))
                    action = entry.get("action", entry.get("operation", ""))
                    target = entry.get("ci_name", entry.get("target", ""))
                    line = f"  {ts}: {action}"
                    if target:
                        line += f" — {target}"
                    lines.append(line)
        elif isinstance(data, dict):
            for k, v in data.items():
                lines.append(f"  {k.replace('_', ' ').title()}: {_format_dict_value(v)}")
        return _to_chat_markdown(lines)

    # Generic fallback: format JSON keys as a readable summary
    if isinstance(data, dict):
        lines = [f"**{tool_name.replace('_', ' ').title()}**", ""]
        for k, v in data.items():
            label = k.replace("_", " ").title()
            if isinstance(v, dict):
                lines.append(f"  **{label}**")
                for sk, sv in list(v.items())[:10]:
                    lines.append(f"    {sk.replace('_', ' ').title()}: {_format_dict_value(sv)}")
            elif isinstance(v, list):
                lines.append(f"  **{label}**: {len(v)} items")
                for item in v[:5]:
                    if isinstance(item, dict):
                        lines.append(f"    - {_format_dict_value(item)}")
                    else:
                        lines.append(f"    - {item}")
            else:
                lines.append(f"  {label}: {_format_dict_value(v)}")
        return _to_chat_markdown(lines)

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
        message = _normalize_message(task.description or task.title)
        message_lower = message.lower()

        # Map chat intent to CMDB MCP tools based on keywords.
        # Tool names and argument schemas must match the MCP server exactly.
        # IMPORTANT: Specific commands are checked FIRST, generic search LAST.
        tool_name: str | None = None
        arguments: dict[str, Any] = {}

        # --- Specific tool commands (highest priority) ---

        # CMDB health & data quality — checked FIRST because most queries
        # that mention "health" in the CMDB context mean CMDB health, not
        # MCP server health.  Trend check BEFORE generic health ("trend
        # report" contains "report" which would match "health report").
        if any(kw in message_lower for kw in ["health trend", "trend report", "health over time"]):
            tool_name = "get_cmdb_health_trend_report"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["cmdb health", "cmdb metrics", "data quality",
                                                  "health metric", "health score", "completeness",
                                                  "discovery coverage", "health report",
                                                  "health check", "cmdb analysis",
                                                  "cmdb report", "cmdb overview"]):
            tool_name = "get_cmdb_health_metrics"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type}

        # Operational / MCP server health — only when explicitly asking
        # about the MCP server, not CMDB data health.
        elif "mcp" in message_lower and any(kw in message_lower for kw in ["health", "status", "check"]):
            tool_name = "check_server_health"
        elif any(kw in message_lower for kw in ["operational dashboard", "ops dashboard", "dashboard"]):
            tool_name = "get_operational_dashboard"
        elif any(kw in message_lower for kw in ["prometheus", "prom metrics"]):
            tool_name = "get_prometheus_metrics"
        elif any(kw in message_lower for kw in ["capture snapshot", "health snapshot", "take snapshot"]):
            tool_name = "capture_cmdb_health_snapshot"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["validate metrics", "verify metrics"]):
            tool_name = "validate_cmdb_health_metrics"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}

        # Compliance & reconciliation
        elif any(kw in message_lower for kw in ["compliance"]):
            tool_name = "run_compliance_check"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["reconcile", "reconciliation", "data drift"]):
            tool_name = "reconcile_cmdb_configuration_data"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["remediate", "remediation", "fix data"]):
            tool_name = "remediate_cmdb_data_issues"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"issue_type": "missing_fields", "ci_type": ci_type, "action": "preview"}

        # Audit & activity (CMDB-specific audit tools)
        elif any(kw in message_lower for kw in ["cmdb audit", "audit summary", "audit stats"]):
            tool_name = "get_audit_summary"
        elif any(kw in message_lower for kw in ["audit log", "audit entries", "audit history"]):
            tool_name = "query_audit_log"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}
        elif any(kw in message_lower for kw in ["activity log", "recent activity", "recent changes"]):
            tool_name = "get_ci_activity_log"
            ci_type = _infer_ci_type(message_lower)
            if ci_type:
                arguments = {"ci_type": ci_type}

        # Data quality checks — EOL / end-of-life routes to stale CI search
        # with a 365-day window to capture assets past their lifecycle.
        elif any(kw in message_lower for kw in ["eol", "end of life", "end-of-life",
                                                  "end of support", "lifecycle"]):
            tool_name = "find_stale_configuration_items"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type, "days": 365}
        elif any(kw in message_lower for kw in ["stale", "outdated"]):
            tool_name = "find_stale_configuration_items"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type, "days": 90}
        elif any(kw in message_lower for kw in ["duplicate", "dedup"]):
            tool_name = "find_duplicate_configuration_items"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type}

        # Relationships & impact — these require a CI identifier (sys_id or name).
        # If no identifier is found, return a prompt asking for one.
        elif any(kw in message_lower for kw in ["dependency tree", "dependencies of"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "query_ci_dependency_tree"
                arguments = {"sys_id": _id}
        elif any(kw in message_lower for kw in ["impact analysis", "impact of", "change impact"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "analyze_configuration_item_impact"
                arguments = {"sys_id": _id, "change_type": "modify"}
        elif any(kw in message_lower for kw in ["relationship type", "relation type"]):
            tool_name = "list_relationship_types_available"
        elif any(kw in message_lower for kw in ["relationship", "relations", "upstream", "downstream"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "query_ci_relationships"
                arguments = {"sys_id": _id}

        # CI details & history
        elif any(kw in message_lower for kw in ["history of", "change history", "changes to"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "get_configuration_item_history"
                arguments = {"sys_id": _id}
        elif any(kw in message_lower for kw in ["compare state", "compare ci", "state comparison"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "compare_configuration_item_state"
                arguments = {"sys_id": _id, "timestamp": "2025-01-01"}
        elif any(kw in message_lower for kw in ["detail", "info about", "show ci"]):
            _id = _extract_identifier(message)
            if _id:
                tool_name = "get_configuration_item_details"
                arguments = {"identifier": _id}

        # IRE (Identification & Reconciliation)
        elif any(kw in message_lower for kw in ["ire rule", "identification rule"]):
            tool_name = "get_ire_rules_for_class"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_class": f"cmdb_ci_{ci_type}"}
        elif any(kw in message_lower for kw in ["ire class", "ci class", "classes with ire"]):
            tool_name = "list_ci_classes_with_ire"
        elif any(kw in message_lower for kw in ["validate ci", "validate against ire"]):
            tool_name = "validate_ci_against_ire"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type, "fields": {}}

        # CI types listing
        elif any(kw in message_lower for kw in ["ci type", "ci class", "list types", "what types"]):
            tool_name = "list_ci_types"

        # Count queries
        elif any(kw in message_lower for kw in ["count", "how many"]):
            tool_name = "search_configuration_items"
            ci_type = _infer_ci_type(message_lower)
            arguments = {"ci_type": ci_type, "limit": 1}
            arguments["_count_only"] = True

        # Generic health/status/analysis fallback — broad queries that
        # indicate the user wants an overview of CMDB data, not a CI search.
        elif any(kw in message_lower for kw in ["health", "status", "analysis",
                                                  "overview", "summary", "findings",
                                                  "report", "assess", "evaluate"]):
            tool_name = "get_cmdb_health_metrics"
            ci_type = _infer_ci_type(message_lower) or "server"
            arguments = {"ci_type": ci_type}

        # --- Fallback logic ---
        if tool_name is None:
            # Check if the message contained a CI-specific keyword but no
            # identifier was found (e.g. "show relationships" without a CI
            # name).  Return a helpful prompt instead of calling a tool.
            _needs_id_keywords = [
                "relationship", "relations", "upstream", "downstream",
                "dependency", "dependencies", "impact",
                "history of", "change history", "changes to",
                "compare state", "compare ci",
                "detail", "info about", "show ci",
            ]
            if any(kw in message_lower for kw in _needs_id_keywords):
                return {
                    "agent_response": (
                        "I need a CI name or sys_id to run that query. "
                        "Try something like:\n\n"
                        "- **Show relationships for web-server-01**\n"
                        "- **History of a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6**\n"
                        "- **Impact analysis for db-prod-03**\n\n"
                        "Or use `/ci-search <name>` to find a CI first."
                    ),
                    "tool_used": None,
                    "source": "cmdb-dispatch",
                }

            # Generic CI search — only when a CI type or name is detected.
            ci_type = _infer_ci_type(message_lower)
            name_hint = _extract_name_hint(message, ci_type)
            if ci_type or name_hint:
                tool_name = "search_configuration_items"
                arguments = {"ci_type": ci_type, "limit": 10}
                if name_hint:
                    arguments["name"] = f"*{name_hint}*"
                # Add environment filter when the message mentions an env
                env = _extract_environment(message_lower)
                if env:
                    arguments["environment"] = env
                # Add custom_query for "missing field" phrases
                cq = _extract_custom_query(message_lower)
                if cq:
                    arguments["custom_query"] = cq
            else:
                # No CI type or name detected — show the dashboard as a
                # helpful default instead of an empty search result.
                tool_name = "get_operational_dashboard"

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


def _make_generic_handler(server_url: str, agent_name: str) -> Any:
    """Create a generic dispatch handler for a FastMCP agent.

    Lists the agent's available tools, selects the best match for the
    user's message using keyword scoring, and calls it. Falls back to
    the first available tool if no keyword match is found.

    Args:
        server_url: Base URL of the MCP server (e.g. http://localhost:8003/mcp).
        agent_name: Human-readable agent name for logging and error messages.

    Returns:
        A callable(Task) -> dict handler for the executor.
    """

    def handler(task: Task) -> dict[str, Any]:
        message = task.description or task.title
        message_lower = message.lower()

        def _run_in_new_loop() -> Any:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from fastmcp import Client

                async def _call() -> Any:
                    async with Client(server_url) as client:
                        # List all available tools
                        tools_result = await client.list_tools()
                        tools = tools_result if isinstance(tools_result, list) else getattr(tools_result, "tools", [])

                        if not tools:
                            return None, None

                        # Score each tool by keyword overlap with the message
                        best_tool = tools[0]
                        best_score = 0
                        for tool in tools:
                            tool_name = getattr(tool, "name", str(tool))
                            tool_desc = getattr(tool, "description", "") or ""
                            combined = (tool_name + " " + tool_desc).lower().replace("_", " ")
                            # Count matching words
                            score = sum(
                                1 for word in message_lower.split()
                                if len(word) > 3 and word in combined
                            )
                            if score > best_score:
                                best_score = score
                                best_tool = tool

                        tool_name = getattr(best_tool, "name", str(best_tool))

                        # Build minimal arguments: try common parameter names
                        # that most query tools accept, then fall back to any
                        # required parameter from the schema.
                        arguments: dict[str, Any] = {}
                        input_schema = getattr(best_tool, "inputSchema", {}) or {}
                        props = input_schema.get("properties", {})
                        required = input_schema.get("required", [])
                        matched = False
                        for param in ("query", "message", "text", "search", "filter",
                                      "user_request", "request", "input", "prompt",
                                      "description", "task", "command"):
                            if param in props:
                                arguments[param] = message
                                matched = True
                                break
                        # Fall back: use the first required string parameter
                        if not matched:
                            for param in required:
                                param_schema = props.get(param, {})
                                if param_schema.get("type") in ("string", None, ""):
                                    arguments[param] = message
                                    break

                        result = await client.call_tool(tool_name, arguments)
                        return tool_name, result

                return loop.run_until_complete(_call())
            finally:
                loop.close()

        future = _thread_pool.submit(_run_in_new_loop)
        tool_name, result = future.result(timeout=30)

        if result is None:
            return {
                "agent_response": f"**{agent_name}** is connected but has no tools available.",
                "tool_used": None,
                "source": agent_name.lower().replace(" ", "-"),
            }

        # Extract text from FastMCP CallToolResult
        content_items = getattr(result, "content", None)
        if content_items is None:
            content_items = result if isinstance(result, list) else [result]

        texts = []
        for item in content_items:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            else:
                texts.append(str(item))

        response_text = "\n".join(texts)

        logger.info(
            f"{agent_name} dispatch succeeded",
            extra={"extra_data": {"task_id": task.task_id, "tool_used": tool_name}},
        )

        return {
            "agent_response": response_text,
            "tool_used": tool_name,
            "source": agent_name.lower().replace(" ", "-"),
        }

    return handler


def register_all_handlers() -> None:
    """Register dispatch handlers for all configured agent endpoints."""
    from itom_orchestrator.executor import TaskExecutor

    config = get_config()

    _agent_configs = [
        ("cmdb-agent", config.cmdb_agent_url, None),  # uses specialized handler
        ("csa-agent", config.csa_agent_url, "CSA Agent"),
        ("discovery-agent", config.discovery_agent_url, "Discovery Agent"),
        ("asset-agent", config.asset_agent_url, "Asset Management Agent"),
        ("itom-auditor", config.auditor_agent_url, "ITOM Auditor"),
        ("itom-documentator", config.documentator_agent_url, "ITOM Documentator"),
    ]

    for agent_id, url, display_name in _agent_configs:
        if not url:
            logger.info(f"No URL configured for {agent_id}, skipping dispatch handler")
            continue
        if agent_id == "cmdb-agent":
            logger.info("Registering CMDB dispatch handler", extra={"extra_data": {"url": url}})
            TaskExecutor.register_dispatch_handler(agent_id, _make_cmdb_handler(url))
        else:
            logger.info(f"Registering {display_name} dispatch handler", extra={"extra_data": {"url": url}})
            TaskExecutor.register_dispatch_handler(agent_id, _make_generic_handler(url, display_name))
