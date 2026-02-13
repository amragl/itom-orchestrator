"""
FastAPI HTTP server for the ITOM Orchestrator.

Provides a REST API that runs alongside the FastMCP server, enabling
HTTP-based clients (like itom-chat-ui) to interact with the orchestrator.

Registered endpoints:
- GET  /api/health          -- orchestrator health status
- GET  /api/agents/status   -- all agents with health info
- GET  /api/agents/{id}     -- detailed info for a specific agent

This module implements ORCH-026: Add FastAPI HTTP server layer alongside FastMCP.
"""

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from itom_orchestrator import __version__
from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Lazy-initialized singletons for registry and health checker.
# These mirror the pattern in server.py and reuse the same underlying
# instances to avoid duplicate state.
_registry_instance: Any = None
_health_checker_instance: Any = None


def _get_registry() -> Any:
    """Get or create the AgentRegistry singleton.

    Uses lazy initialization to avoid circular imports. The registry is
    created on first access and persisted via the StatePersistence layer.
    """
    global _registry_instance
    if _registry_instance is None:
        from itom_orchestrator.persistence import get_persistence
        from itom_orchestrator.registry import AgentRegistry

        persistence = get_persistence()
        _registry_instance = AgentRegistry(persistence=persistence, load_defaults=True)
        _registry_instance.initialize()
    return _registry_instance


def _get_health_checker() -> Any:
    """Get or create the AgentHealthChecker singleton.

    Uses lazy initialization. The health checker is created on first access,
    using the registry and persistence singletons.
    """
    global _health_checker_instance
    if _health_checker_instance is None:
        from itom_orchestrator.health import AgentHealthChecker
        from itom_orchestrator.persistence import get_persistence

        registry = _get_registry()
        persistence = get_persistence()
        _health_checker_instance = AgentHealthChecker(
            registry=registry, persistence=persistence
        )
    return _health_checker_instance


def reset_http_singletons() -> None:
    """Reset the HTTP server singletons. For use in tests."""
    global _registry_instance, _health_checker_instance
    _registry_instance = None
    _health_checker_instance = None


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Configures CORS middleware with allowed origins from the orchestrator
    config. Registers all API routes.

    Returns:
        Configured FastAPI application instance.
    """
    config = get_config()

    app = FastAPI(
        title="ITOM Orchestrator API",
        description=(
            "REST API for the ITOM Orchestrator. Provides health, agent status, "
            "and chat routing endpoints for HTTP clients like itom-chat-ui."
        ),
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # CORS middleware -- allow itom-chat-ui and other configured origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Import and include the API router
    from itom_orchestrator.api.routes import router

    app.include_router(router)

    logger.info(
        "FastAPI app created",
        extra={
            "extra_data": {
                "version": __version__,
                "cors_origins": config.cors_origins,
                "docs_url": "/api/docs",
            }
        },
    )

    return app
