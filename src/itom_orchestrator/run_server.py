"""
Entry point for running the ITOM Orchestrator.

Supports two modes:
- MCP mode (default): Runs the FastMCP server over stdio transport.
- HTTP mode (--http): Runs the FastAPI HTTP server on a configurable port.

Both modes share the same internal logic -- registry, health checker,
persistence, and all MCP tool implementations.

Usage:
    itom-orchestrator          # MCP server via stdio
    itom-orchestrator --http   # HTTP server on port 8000
    itom-orchestrator-http     # HTTP server (convenience entry point)
"""

import argparse
import sys

from dotenv import load_dotenv

from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import setup_logging


def main() -> None:
    """Start the ITOM Orchestrator.

    Parses command-line arguments to determine the server mode.
    With ``--http``, starts the FastAPI server. Otherwise, starts the
    FastMCP server over stdio.
    """
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="ITOM Orchestrator -- central coordinator for all ITOM agents.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Start the HTTP API server instead of the MCP stdio server.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind the HTTP server to (overrides ORCH_HTTP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the HTTP server (overrides ORCH_HTTP_PORT).",
    )

    args = parser.parse_args()

    config = get_config()
    setup_logging(level=config.log_level, log_dir=config.resolved_log_dir)

    if args.http:
        _run_http(
            host=args.host or config.http_host,
            port=args.port or config.http_port,
        )
    else:
        _run_mcp()


def main_http() -> None:
    """Convenience entry point that always starts the HTTP server.

    Used by the ``itom-orchestrator-http`` console script.
    """
    load_dotenv()
    config = get_config()
    setup_logging(level=config.log_level, log_dir=config.resolved_log_dir)
    _run_http(host=config.http_host, port=config.http_port)


def _run_mcp() -> None:
    """Start the FastMCP server over stdio transport."""
    from itom_orchestrator.server import mcp

    mcp.run()


def _run_http(host: str, port: int) -> None:
    """Start the FastAPI HTTP server with uvicorn.

    Args:
        host: Host to bind to.
        port: Port to listen on.
    """
    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn is required for HTTP mode. "
            "Install it with: pip install 'itom-orchestrator[dev]' or pip install uvicorn",
            file=sys.stderr,
        )
        sys.exit(1)

    from itom_orchestrator.http_server import create_app

    app = create_app()

    print(f"Starting ITOM Orchestrator HTTP server on {host}:{port}")
    print(f"API docs available at http://{host}:{port}/api/docs")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
