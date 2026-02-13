"""
ITOM Orchestrator -- Central coordinator for all ITOM agents.

Routes tasks, manages workflows, enforces role boundaries,
handles cross-agent communication, and maintains execution state.
"""

__version__ = "0.1.0"
__author__ = "Cesar Garcia Lopez"

from itom_orchestrator.config import OrchestratorConfig, get_config
from itom_orchestrator.health import AgentHealthChecker
from itom_orchestrator.logging_config import get_structured_logger, setup_logging
from itom_orchestrator.persistence import StatePersistence, get_persistence, reset_persistence
from itom_orchestrator.registry import AgentRegistry
from itom_orchestrator.server import mcp

__all__ = [
    # Configuration
    "OrchestratorConfig",
    "get_config",
    # Logging
    "get_structured_logger",
    "setup_logging",
    # Persistence
    "StatePersistence",
    "get_persistence",
    "reset_persistence",
    # Registry
    "AgentRegistry",
    # Health
    "AgentHealthChecker",
    # MCP server
    "mcp",
]
