"""
Configuration management for the ITOM Orchestrator.

Uses Pydantic BaseSettings for type-safe configuration loaded from
environment variables with the ``ORCH_`` prefix, ``.env`` files,
and sensible defaults.
"""

from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorConfig(BaseSettings):
    """Main orchestrator configuration.

    All settings can be overridden via environment variables prefixed with ``ORCH_``.
    For example, ``ORCH_DATA_DIR`` sets :pyattr:`data_dir`.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Core directories
    data_dir: str = Field(
        default=".itom-orchestrator",
        description="Root data directory for orchestrator state and configuration files.",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    log_dir: str = Field(
        default="",
        description=("Directory for log files. If empty, defaults to ``<data_dir>/logs``."),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state_dir(self) -> str:
        """Computed state directory derived from :pyattr:`data_dir`."""
        return str(Path(self.data_dir) / "state")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_log_dir(self) -> str:
        """Resolved log directory -- uses ``log_dir`` if set, otherwise ``<data_dir>/logs``."""
        if self.log_dir:
            return self.log_dir
        return str(Path(self.data_dir) / "logs")


# ---------------------------------------------------------------------------
# Global configuration singleton
# ---------------------------------------------------------------------------

_config: OrchestratorConfig | None = None


def get_config() -> OrchestratorConfig:
    """Return the global :class:`OrchestratorConfig` singleton.

    Creates the instance on first call.  Subsequent calls return the same
    instance.  Call :func:`reset_config` in tests to clear the singleton.
    """
    global _config
    if _config is None:
        _config = OrchestratorConfig()
    return _config


def reset_config() -> None:
    """Reset the global config singleton.

    Intended for use in test fixtures to ensure a clean config per test.
    """
    global _config
    _config = None
