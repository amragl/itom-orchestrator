"""
Unit tests for the AgentConfigLoader -- ORCH-007.

Tests cover:
- Default config generation
- Config file creation and loading
- Pydantic validation of config entries
- Applying config to registry
- Runtime reload with diff detection
- Config modification (add/remove agents)
- File change detection
- Error handling for invalid configs
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from itom_orchestrator.agent_config import (
    AgentConfigEntry,
    AgentConfigError,
    AgentConfigFile,
    AgentConfigLoader,
    generate_default_config,
)
from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentStatus,
)
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentNotFoundError, AgentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


@pytest.fixture()
def persistence(state_dir: Path) -> StatePersistence:
    return StatePersistence(state_dir)


@pytest.fixture()
def empty_registry(persistence: StatePersistence) -> AgentRegistry:
    """Empty registry (no defaults) for testing config-driven registration."""
    reg = AgentRegistry(persistence=persistence, load_defaults=False)
    reg.initialize()
    return reg


@pytest.fixture()
def loader(config_dir: Path, empty_registry: AgentRegistry) -> AgentConfigLoader:
    return AgentConfigLoader(config_dir=config_dir, registry=empty_registry)


@pytest.fixture()
def sample_entry() -> AgentConfigEntry:
    return AgentConfigEntry(
        agent_id="custom-agent",
        name="Custom Agent",
        description="A custom test agent.",
        domain=AgentDomain.CMDB,
        capabilities=[
            AgentCapability(
                name="custom_op",
                domain=AgentDomain.CMDB,
                description="A custom operation.",
            )
        ],
        metadata={"custom": True},
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Default config generation
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """Tests for default config generation."""

    def test_generate_default_has_6_agents(self) -> None:
        config = generate_default_config()
        assert len(config.agents) == 6

    def test_default_version(self) -> None:
        config = generate_default_config()
        assert config.version == "1.0.0"

    def test_default_agents_all_enabled(self) -> None:
        config = generate_default_config()
        for entry in config.agents:
            assert entry.enabled is True

    def test_default_agent_ids(self) -> None:
        config = generate_default_config()
        ids = {e.agent_id for e in config.agents}
        expected = {
            "cmdb-agent",
            "discovery-agent",
            "asset-agent",
            "csa-agent",
            "itom-auditor",
            "itom-documentator",
        }
        assert ids == expected

    def test_default_agents_have_capabilities(self) -> None:
        config = generate_default_config()
        for entry in config.agents:
            assert len(entry.capabilities) > 0

    def test_default_timestamps_set(self) -> None:
        config = generate_default_config()
        assert config.created_at != ""
        assert config.updated_at != ""


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Tests for Pydantic validation of config entries."""

    def test_valid_entry(self, sample_entry: AgentConfigEntry) -> None:
        assert sample_entry.agent_id == "custom-agent"
        assert sample_entry.enabled is True

    def test_empty_agent_id_rejected(self) -> None:
        with pytest.raises(Exception):
            AgentConfigEntry(
                agent_id="",
                name="Test",
                description="Test",
                domain=AgentDomain.CMDB,
                capabilities=[],
            )

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(Exception):
            AgentConfigEntry(
                agent_id="test",
                name="  ",
                description="Test",
                domain=AgentDomain.CMDB,
                capabilities=[],
            )

    def test_default_status_is_offline(self) -> None:
        entry = AgentConfigEntry(
            agent_id="test",
            name="Test",
            description="Test",
            domain=AgentDomain.CMDB,
            capabilities=[],
        )
        assert entry.status == AgentStatus.OFFLINE

    def test_config_file_validation(self) -> None:
        config = AgentConfigFile(
            version="1.0.0",
            agents=[],
        )
        assert config.version == "1.0.0"
        assert config.agents == []

    def test_empty_version_rejected(self) -> None:
        with pytest.raises(Exception):
            AgentConfigFile(version="", agents=[])


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


class TestFileOperations:
    """Tests for config file creation and loading."""

    def test_ensure_config_creates_file(self, loader: AgentConfigLoader) -> None:
        path = loader.ensure_config_exists()
        assert path.exists()

    def test_ensure_config_idempotent(self, loader: AgentConfigLoader) -> None:
        path1 = loader.ensure_config_exists()
        path2 = loader.ensure_config_exists()
        assert path1 == path2

    def test_load_creates_default_if_missing(
        self, loader: AgentConfigLoader
    ) -> None:
        config = loader.load()
        assert config is not None
        assert len(config.agents) == 6
        assert config.version == "1.0.0"

    def test_load_reads_existing_file(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Loading an existing valid config file should work."""
        # Write a minimal config file
        config_path = config_dir / "agents.json"
        data = {
            "version": "1.0.0",
            "description": "Test config",
            "agents": [
                {
                    "agent_id": "test-agent",
                    "name": "Test Agent",
                    "description": "For testing.",
                    "domain": "cmdb",
                    "capabilities": [
                        {"name": "test_cap", "domain": "cmdb", "description": "Test capability."}
                    ],
                    "enabled": True,
                }
            ],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        with open(config_path, "w") as f:
            json.dump(data, f)

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        config = loader.load()
        assert len(config.agents) == 1
        assert config.agents[0].agent_id == "test-agent"

    def test_load_invalid_json_raises(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Loading a file with invalid JSON should raise AgentConfigError."""
        config_path = config_dir / "agents.json"
        config_path.write_text("{ invalid json }", encoding="utf-8")

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        with pytest.raises(AgentConfigError):
            loader.load()

    def test_load_invalid_schema_raises(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Loading a file with valid JSON but invalid schema should raise."""
        config_path = config_dir / "agents.json"
        with open(config_path, "w") as f:
            json.dump({"version": "1.0.0", "agents": [{"bad": "data"}]}, f)

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        with pytest.raises(AgentConfigError):
            loader.load()

    def test_save_current(self, loader: AgentConfigLoader) -> None:
        """save_current should write the config back to disk."""
        loader.load()
        loader.save_current()
        assert loader.config_path.exists()

    def test_save_without_load_raises(self, loader: AgentConfigLoader) -> None:
        with pytest.raises(AgentConfigError):
            loader.save_current()


# ---------------------------------------------------------------------------
# Apply to registry
# ---------------------------------------------------------------------------


class TestApplyToRegistry:
    """Tests for applying config to the AgentRegistry."""

    def test_apply_registers_agents(
        self, loader: AgentConfigLoader, empty_registry: AgentRegistry
    ) -> None:
        loader.load()
        result = loader.apply_to_registry()
        assert result["agents_added"] == 6
        assert result["agents_skipped"] == 0
        assert empty_registry.agent_count == 6

    def test_apply_skips_existing_agents(
        self, loader: AgentConfigLoader, empty_registry: AgentRegistry
    ) -> None:
        loader.load()
        # First apply
        loader.apply_to_registry()
        # Second apply should skip all
        result = loader.apply_to_registry()
        assert result["agents_added"] == 0
        assert result["agents_skipped"] == 6

    def test_apply_skips_disabled_agents(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Disabled agents should not be registered."""
        config_path = config_dir / "agents.json"
        data = {
            "version": "1.0.0",
            "agents": [
                {
                    "agent_id": "enabled-agent",
                    "name": "Enabled",
                    "description": "An enabled agent.",
                    "domain": "cmdb",
                    "capabilities": [
                        {"name": "cap1", "domain": "cmdb", "description": "Cap."}
                    ],
                    "enabled": True,
                },
                {
                    "agent_id": "disabled-agent",
                    "name": "Disabled",
                    "description": "A disabled agent.",
                    "domain": "asset",
                    "capabilities": [
                        {"name": "cap2", "domain": "asset", "description": "Cap."}
                    ],
                    "enabled": False,
                },
            ],
        }
        with open(config_path, "w") as f:
            json.dump(data, f)

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        loader.load()
        result = loader.apply_to_registry()
        assert result["agents_added"] == 1
        assert result["agents_disabled"] == 1
        assert empty_registry.agent_count == 1

    def test_apply_without_load_raises(self, loader: AgentConfigLoader) -> None:
        with pytest.raises(AgentConfigError):
            loader.apply_to_registry()


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


class TestReload:
    """Tests for runtime config reload."""

    def test_reload_detects_new_agent(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Reload should register new agents added to config."""
        config_path = config_dir / "agents.json"

        # Initial config with 1 agent
        data = {
            "version": "1.0.0",
            "agents": [
                {
                    "agent_id": "agent-a",
                    "name": "Agent A",
                    "description": "First agent.",
                    "domain": "cmdb",
                    "capabilities": [
                        {"name": "cap_a", "domain": "cmdb", "description": "Cap A."}
                    ],
                    "enabled": True,
                },
            ],
        }
        with open(config_path, "w") as f:
            json.dump(data, f)

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        loader.load()
        loader.apply_to_registry()
        assert empty_registry.agent_count == 1

        # Add a second agent to the config file
        data["agents"].append({
            "agent_id": "agent-b",
            "name": "Agent B",
            "description": "Second agent.",
            "domain": "asset",
            "capabilities": [
                {"name": "cap_b", "domain": "asset", "description": "Cap B."}
            ],
            "enabled": True,
        })
        with open(config_path, "w") as f:
            json.dump(data, f)

        result = loader.reload()
        assert result["agents_added"] == 1
        assert empty_registry.agent_count == 2

    def test_reload_detects_disabled_agent(
        self, config_dir: Path, empty_registry: AgentRegistry
    ) -> None:
        """Reload should unregister agents that are disabled."""
        config_path = config_dir / "agents.json"

        data = {
            "version": "1.0.0",
            "agents": [
                {
                    "agent_id": "agent-a",
                    "name": "Agent A",
                    "description": "Agent A.",
                    "domain": "cmdb",
                    "capabilities": [
                        {"name": "cap_a", "domain": "cmdb", "description": "Cap."}
                    ],
                    "enabled": True,
                },
            ],
        }
        with open(config_path, "w") as f:
            json.dump(data, f)

        loader = AgentConfigLoader(config_dir=config_dir, registry=empty_registry)
        loader.load()
        loader.apply_to_registry()
        assert empty_registry.agent_count == 1

        # Disable the agent
        data["agents"][0]["enabled"] = False
        with open(config_path, "w") as f:
            json.dump(data, f)

        result = loader.reload()
        assert result["agents_removed"] == 1
        assert empty_registry.agent_count == 0

    def test_has_file_changed_before_load(
        self, loader: AgentConfigLoader
    ) -> None:
        """Before loading, has_file_changed should return True."""
        assert loader.has_file_changed() is True

    def test_has_file_changed_after_load(
        self, loader: AgentConfigLoader
    ) -> None:
        """After loading, has_file_changed should return False."""
        loader.load()
        assert loader.has_file_changed() is False


# ---------------------------------------------------------------------------
# Config modification
# ---------------------------------------------------------------------------


class TestConfigModification:
    """Tests for adding/removing agents from config."""

    def test_add_agent_to_config(
        self, loader: AgentConfigLoader, sample_entry: AgentConfigEntry
    ) -> None:
        loader.load()
        initial_count = len(loader.current_config.agents)
        loader.add_agent_to_config(sample_entry)
        assert len(loader.current_config.agents) == initial_count + 1

    def test_add_duplicate_raises(
        self, loader: AgentConfigLoader
    ) -> None:
        loader.load()
        # cmdb-agent already in default config
        entry = AgentConfigEntry(
            agent_id="cmdb-agent",
            name="Dup",
            description="Dup.",
            domain=AgentDomain.CMDB,
            capabilities=[],
        )
        with pytest.raises(AgentConfigError, match="already exists"):
            loader.add_agent_to_config(entry)

    def test_remove_agent_from_config(self, loader: AgentConfigLoader) -> None:
        loader.load()
        initial_count = len(loader.current_config.agents)
        removed = loader.remove_agent_from_config("cmdb-agent")
        assert removed.agent_id == "cmdb-agent"
        assert len(loader.current_config.agents) == initial_count - 1

    def test_remove_nonexistent_raises(self, loader: AgentConfigLoader) -> None:
        loader.load()
        with pytest.raises(AgentConfigError, match="not found"):
            loader.remove_agent_from_config("nonexistent-agent")

    def test_add_without_load_raises(
        self, loader: AgentConfigLoader, sample_entry: AgentConfigEntry
    ) -> None:
        with pytest.raises(AgentConfigError):
            loader.add_agent_to_config(sample_entry)

    def test_remove_without_load_raises(self, loader: AgentConfigLoader) -> None:
        with pytest.raises(AgentConfigError):
            loader.remove_agent_from_config("test")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestConfigSummary:
    """Tests for config summary."""

    def test_summary_before_load(self, loader: AgentConfigLoader) -> None:
        summary = loader.get_config_summary()
        assert summary["loaded"] is False

    def test_summary_after_load(self, loader: AgentConfigLoader) -> None:
        loader.load()
        summary = loader.get_config_summary()
        assert summary["loaded"] is True
        assert summary["total_agents"] == 6
        assert summary["enabled_agents"] == 6
        assert summary["disabled_agents"] == 0
        assert summary["version"] == "1.0.0"
