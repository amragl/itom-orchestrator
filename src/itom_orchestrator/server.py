"""
FastMCP server for the ITOM Orchestrator.

This is the MCP server entry point. MCP tools are registered on the ``mcp``
instance and exposed to connected clients.

Tools will be added in subsequent tickets (ORCH-002+):
- get_orchestrator_health
- get_agent_registry / get_agent_status
- route_task
- execute_workflow / get_workflow_status
- send_agent_message
- enforce_role_boundaries
"""

from fastmcp import FastMCP

mcp = FastMCP("itom-orchestrator")
