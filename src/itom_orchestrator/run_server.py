"""
Entry point for running the ITOM Orchestrator MCP server.

Loads environment variables from ``.env``, configures structured logging,
and starts the FastMCP server with stdio transport.
"""

from dotenv import load_dotenv

from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import setup_logging
from itom_orchestrator.server import mcp


def main() -> None:
    """Start the ITOM Orchestrator MCP server."""
    load_dotenv()

    config = get_config()
    setup_logging(level=config.log_level, log_dir=config.resolved_log_dir)

    mcp.run()


if __name__ == "__main__":
    main()
