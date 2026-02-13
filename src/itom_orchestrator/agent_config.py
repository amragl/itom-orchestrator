"""
Agent configuration file management for the ITOM Orchestrator.

Provides JSON-based configuration for agent registrations with Pydantic
validation, default config generation for all 6 ITOM agents, and runtime
reload support. Config files are stored in the orchestrator data directory.

This module implements ORCH-007: Agent configuration file and dynamic loading.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.registry import AgentRegistry, _build_default_agents

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Default config filename
DEFAULT_CONFIG_FILENAME = "agents.json"


class AgentConfigEntry(BaseModel):
    """Configuration entry for a single agent.

    This is the schema for entries in the ``agents`` array of the config file.
    It matches the AgentRegistration model but allows optional fields that
    will be filled with defaults during loading.

    Attributes:
        agent_id: Unique agent identifier.
        name: Human-readable display name.
        description: What this agent does.
        domain: Primary operational domain.
        capabilities: List of capabilities the agent provides.
        mcp_server_url: Optional MCP server endpoint URL.
        status: Initial runtime status (defaults to OFFLINE).
        metadata: Arbitrary key-value metadata.
        enabled: Whether this agent should be loaded into the registry.
    """

    agent_id: str
    name: str
    description: str
    domain: AgentDomain
    capabilities: list[AgentCapability]
    mcp_server_url: str | None = None
    status: AgentStatus = AgentStatus.OFFLINE
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("agent_id")
    @classmethod
    def agent_id_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent_id must not be empty")
        return v.strip()

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()


class AgentConfigFile(BaseModel):
    """Schema for the agents.json configuration file.

    Attributes:
        version: Config file schema version for forward compatibility.
        description: Human-readable description of this config file.
        agents: List of agent configuration entries.
        created_at: When this config was first created.
        updated_at: When this config was last modified.
    """

    version: str = "1.0.0"
    description: str = "ITOM Orchestrator agent configuration"
    agents: list[AgentConfigEntry]
    created_at: str = ""
    updated_at: str = ""

    @field_validator("version")
    @classmethod
    def version_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("version must not be empty")
        return v.strip()


class AgentConfigError(Exception):
    """Error in agent configuration operations."""

    def __init__(self, message: str, path: str | None = None) -> None:
        self.path = path
        super().__init__(message)


def _default_agents_to_config_entries() -> list[AgentConfigEntry]:
    """Convert default AgentRegistration objects to AgentConfigEntry objects.

    Returns:
        List of config entries for all 6 default ITOM agents.
    """
    entries: list[AgentConfigEntry] = []
    for agent in _build_default_agents():
        entry = AgentConfigEntry(
            agent_id=agent.agent_id,
            name=agent.name,
            description=agent.description,
            domain=agent.domain,
            capabilities=agent.capabilities,
            mcp_server_url=agent.mcp_server_url,
            status=agent.status,
            metadata=agent.metadata,
            enabled=True,
        )
        entries.append(entry)
    return entries


def generate_default_config() -> AgentConfigFile:
    """Generate the default agents.json configuration.

    Returns:
        AgentConfigFile containing all 6 default ITOM agents.
    """
    now = datetime.now(UTC).isoformat()
    return AgentConfigFile(
        version="1.0.0",
        description="ITOM Orchestrator agent configuration -- default setup with all 6 ITOM agents.",
        agents=_default_agents_to_config_entries(),
        created_at=now,
        updated_at=now,
    )


class AgentConfigLoader:
    """Manages loading, saving, and reloading of agent configuration files.

    The loader handles:
    - Writing default config when no file exists
    - Validating config files against the Pydantic schema
    - Loading config entries into the AgentRegistry
    - Runtime reload with diff detection (additions, removals, updates)

    Args:
        config_dir: Directory where config files are stored.
        registry: The AgentRegistry to populate from config.
        config_filename: Name of the config file (default: agents.json).
    """

    def __init__(
        self,
        config_dir: str | Path,
        registry: AgentRegistry,
        config_filename: str = DEFAULT_CONFIG_FILENAME,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._registry = registry
        self._config_filename = config_filename
        self._config_path = self._config_dir / config_filename
        self._current_config: AgentConfigFile | None = None
        self._last_loaded_at: datetime | None = None
        self._file_mtime: float | None = None

    @property
    def config_path(self) -> Path:
        """Path to the config file."""
        return self._config_path

    @property
    def current_config(self) -> AgentConfigFile | None:
        """The currently loaded config, or None if not yet loaded."""
        return self._current_config

    @property
    def last_loaded_at(self) -> datetime | None:
        """When the config was last loaded."""
        return self._last_loaded_at

    def ensure_config_exists(self) -> Path:
        """Create the config file with defaults if it does not exist.

        Returns:
            Path to the config file.
        """
        self._config_dir.mkdir(parents=True, exist_ok=True)

        if not self._config_path.exists():
            default_config = generate_default_config()
            self._write_config(default_config)
            logger.info(
                "Default agent config created",
                extra={
                    "extra_data": {
                        "path": str(self._config_path),
                        "agent_count": len(default_config.agents),
                    }
                },
            )

        return self._config_path

    def _write_config(self, config: AgentConfigFile) -> None:
        """Write config to file atomically.

        Args:
            config: The configuration to write.

        Raises:
            AgentConfigError: If the file cannot be written.
        """
        tmp_path = self._config_path.with_suffix(".json.tmp")
        try:
            data = config.model_dump(mode="json")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.write("\n")
            os.replace(tmp_path, self._config_path)
        except OSError as exc:
            if tmp_path.exists():
                tmp_path.unlink()
            raise AgentConfigError(
                f"Failed to write config: {exc}", path=str(self._config_path)
            ) from exc

    def load(self) -> AgentConfigFile:
        """Load and validate the agent configuration file.

        If the file does not exist, creates it with defaults first.

        Returns:
            Validated AgentConfigFile.

        Raises:
            AgentConfigError: If the file cannot be read or parsed.
        """
        self.ensure_config_exists()

        try:
            with open(self._config_path, encoding="utf-8") as f:
                raw_data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise AgentConfigError(
                f"Failed to read config file: {exc}", path=str(self._config_path)
            ) from exc

        try:
            config = AgentConfigFile.model_validate(raw_data)
        except Exception as exc:
            raise AgentConfigError(
                f"Config validation failed: {exc}", path=str(self._config_path)
            ) from exc

        self._current_config = config
        self._last_loaded_at = datetime.now(UTC)
        self._file_mtime = self._config_path.stat().st_mtime

        logger.info(
            "Agent config loaded",
            extra={
                "extra_data": {
                    "path": str(self._config_path),
                    "version": config.version,
                    "agent_count": len(config.agents),
                    "enabled_count": sum(1 for a in config.agents if a.enabled),
                }
            },
        )

        return config

    def apply_to_registry(self) -> dict[str, Any]:
        """Apply the loaded config to the AgentRegistry.

        Registers enabled agents that are not already in the registry.
        Does not remove agents that exist in the registry but not in the config
        (preserves runtime-registered agents).

        Returns:
            Summary of applied changes: agents_added, agents_skipped, agents_disabled.

        Raises:
            AgentConfigError: If no config has been loaded.
        """
        if self._current_config is None:
            raise AgentConfigError("No config loaded. Call load() first.")

        added = 0
        skipped = 0
        disabled = 0

        for entry in self._current_config.agents:
            if not entry.enabled:
                disabled += 1
                logger.debug(
                    "Agent disabled in config, skipping",
                    extra={"extra_data": {"agent_id": entry.agent_id}},
                )
                continue

            # Check if agent already exists in registry
            try:
                self._registry.get(entry.agent_id)
                skipped += 1
                logger.debug(
                    "Agent already registered, skipping",
                    extra={"extra_data": {"agent_id": entry.agent_id}},
                )
            except Exception:
                # Agent not in registry -- register it
                now = datetime.now(UTC)
                registration = AgentRegistration(
                    agent_id=entry.agent_id,
                    name=entry.name,
                    description=entry.description,
                    domain=entry.domain,
                    capabilities=entry.capabilities,
                    mcp_server_url=entry.mcp_server_url,
                    status=entry.status,
                    registered_at=now,
                    metadata=entry.metadata,
                )
                self._registry.register(registration)
                added += 1

        result = {
            "agents_added": added,
            "agents_skipped": skipped,
            "agents_disabled": disabled,
            "total_in_config": len(self._current_config.agents),
            "total_in_registry": self._registry.agent_count,
        }

        logger.info(
            "Config applied to registry",
            extra={"extra_data": result},
        )

        return result

    def has_file_changed(self) -> bool:
        """Check if the config file has been modified since last load.

        Returns:
            True if the file has been modified (or never loaded).
        """
        if self._file_mtime is None:
            return True
        if not self._config_path.exists():
            return True
        current_mtime = self._config_path.stat().st_mtime
        return current_mtime != self._file_mtime

    def reload(self) -> dict[str, Any]:
        """Reload the config file and apply changes to the registry.

        Detects what changed since the last load and applies differences:
        - New agents (in config but not in registry) are registered
        - Disabled agents (enabled=False) are unregistered if present
        - Modified agents have their metadata and status updated

        Returns:
            Summary of reload changes.

        Raises:
            AgentConfigError: If the file cannot be read or parsed.
        """
        old_config = self._current_config
        new_config = self.load()

        if old_config is None:
            # First load -- apply everything
            return self.apply_to_registry()

        # Build lookup maps
        old_agents = {e.agent_id: e for e in old_config.agents}
        new_agents = {e.agent_id: e for e in new_config.agents}

        added = 0
        removed = 0
        updated = 0
        unchanged = 0

        # Find new and updated agents
        for agent_id, new_entry in new_agents.items():
            if agent_id not in old_agents:
                # New agent
                if new_entry.enabled:
                    now = datetime.now(UTC)
                    registration = AgentRegistration(
                        agent_id=new_entry.agent_id,
                        name=new_entry.name,
                        description=new_entry.description,
                        domain=new_entry.domain,
                        capabilities=new_entry.capabilities,
                        mcp_server_url=new_entry.mcp_server_url,
                        status=new_entry.status,
                        registered_at=now,
                        metadata=new_entry.metadata,
                    )
                    try:
                        self._registry.register(registration)
                        added += 1
                    except Exception:
                        logger.warning(
                            "Failed to register new agent from config reload",
                            extra={"extra_data": {"agent_id": agent_id}},
                        )
            else:
                old_entry = old_agents[agent_id]
                if not new_entry.enabled and old_entry.enabled:
                    # Agent was disabled -- unregister
                    try:
                        self._registry.unregister(agent_id)
                        removed += 1
                    except Exception:
                        logger.debug(
                            "Agent not in registry during disable",
                            extra={"extra_data": {"agent_id": agent_id}},
                        )
                elif new_entry.enabled and not old_entry.enabled:
                    # Agent was re-enabled -- register
                    now = datetime.now(UTC)
                    registration = AgentRegistration(
                        agent_id=new_entry.agent_id,
                        name=new_entry.name,
                        description=new_entry.description,
                        domain=new_entry.domain,
                        capabilities=new_entry.capabilities,
                        mcp_server_url=new_entry.mcp_server_url,
                        status=new_entry.status,
                        registered_at=now,
                        metadata=new_entry.metadata,
                    )
                    try:
                        self._registry.register(registration)
                        added += 1
                    except Exception:
                        logger.debug(
                            "Agent already in registry during re-enable",
                            extra={"extra_data": {"agent_id": agent_id}},
                        )
                elif new_entry != old_entry and new_entry.enabled:
                    # Agent was modified -- update metadata
                    try:
                        self._registry.update_metadata(
                            agent_id, new_entry.metadata, merge=False
                        )
                        updated += 1
                    except Exception:
                        logger.debug(
                            "Failed to update agent metadata during reload",
                            extra={"extra_data": {"agent_id": agent_id}},
                        )
                else:
                    unchanged += 1

        # Find removed agents (in old but not in new)
        for agent_id in old_agents:
            if agent_id not in new_agents:
                try:
                    self._registry.unregister(agent_id)
                    removed += 1
                except Exception:
                    logger.debug(
                        "Agent not in registry during removal",
                        extra={"extra_data": {"agent_id": agent_id}},
                    )

        result = {
            "agents_added": added,
            "agents_removed": removed,
            "agents_updated": updated,
            "agents_unchanged": unchanged,
            "total_in_config": len(new_config.agents),
            "total_in_registry": self._registry.agent_count,
            "file_changed": True,
        }

        logger.info(
            "Config reloaded",
            extra={"extra_data": result},
        )

        return result

    def save_current(self) -> None:
        """Save the current config back to disk.

        This is useful after programmatic modifications to the config.

        Raises:
            AgentConfigError: If no config is loaded or write fails.
        """
        if self._current_config is None:
            raise AgentConfigError("No config loaded. Call load() first.")

        self._current_config.updated_at = datetime.now(UTC).isoformat()
        self._write_config(self._current_config)
        self._file_mtime = self._config_path.stat().st_mtime

        logger.info(
            "Config saved",
            extra={"extra_data": {"path": str(self._config_path)}},
        )

    def add_agent_to_config(self, entry: AgentConfigEntry) -> None:
        """Add a new agent entry to the current config.

        Does not modify the registry -- call :meth:`apply_to_registry`
        or :meth:`reload` to apply changes.

        Args:
            entry: The agent config entry to add.

        Raises:
            AgentConfigError: If no config is loaded or agent ID already exists.
        """
        if self._current_config is None:
            raise AgentConfigError("No config loaded. Call load() first.")

        existing_ids = {e.agent_id for e in self._current_config.agents}
        if entry.agent_id in existing_ids:
            raise AgentConfigError(
                f"Agent '{entry.agent_id}' already exists in config."
            )

        self._current_config.agents.append(entry)

    def remove_agent_from_config(self, agent_id: str) -> AgentConfigEntry:
        """Remove an agent entry from the current config.

        Does not modify the registry -- call :meth:`reload` to apply changes.

        Args:
            agent_id: The ID of the agent to remove.

        Returns:
            The removed config entry.

        Raises:
            AgentConfigError: If no config is loaded or agent ID not found.
        """
        if self._current_config is None:
            raise AgentConfigError("No config loaded. Call load() first.")

        for i, entry in enumerate(self._current_config.agents):
            if entry.agent_id == agent_id:
                return self._current_config.agents.pop(i)

        raise AgentConfigError(f"Agent '{agent_id}' not found in config.")

    def get_config_summary(self) -> dict[str, Any]:
        """Get a summary of the current config state.

        Returns:
            Summary dictionary with config version, agent count, and file info.
        """
        if self._current_config is None:
            return {
                "loaded": False,
                "path": str(self._config_path),
                "exists": self._config_path.exists(),
            }

        return {
            "loaded": True,
            "path": str(self._config_path),
            "version": self._current_config.version,
            "total_agents": len(self._current_config.agents),
            "enabled_agents": sum(1 for a in self._current_config.agents if a.enabled),
            "disabled_agents": sum(1 for a in self._current_config.agents if not a.enabled),
            "last_loaded_at": self._last_loaded_at.isoformat() if self._last_loaded_at else None,
            "file_changed_since_load": self.has_file_changed(),
            "created_at": self._current_config.created_at,
            "updated_at": self._current_config.updated_at,
        }
