"""
ITOM Orchestrator -- Central coordinator for all ITOM agents.

Routes tasks, manages workflows, enforces role boundaries,
handles cross-agent communication, and maintains execution state.
"""

__version__ = "0.1.0"
__author__ = "Cesar Garcia Lopez"

from itom_orchestrator.config import OrchestratorConfig, get_config
from itom_orchestrator.logging_config import get_structured_logger, setup_logging

__all__ = [
    # Configuration
    "OrchestratorConfig",
    "get_config",
    # Logging
    "get_structured_logger",
    "setup_logging",
]
