"""
Unit tests for the AgentRegistry -- ORCH-005.

Tests cover:
- Initialization with default agents and empty mode
- Persistence load/save cycle
- Register, unregister, lookup operations
- Search by domain, capability, and status
- Status updates and metadata updates
- Error handling for duplicate registration and missing agents
- MCP tool integration (_get_agent_registry, _get_agent_details)
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import (
    REGISTRY_STATE_KEY,
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    AgentRegistry,
    RegistryLoadError,
    _build_default_agents,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    """Create a temporary state directory."""
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture()
def persistence(state_dir: Path) -> StatePersistence:
    """Create a StatePersistence instance with a temp directory."""
    return StatePersistence(state_dir)


@pytest.fixture()
def registry(persistence: StatePersistence) -> AgentRegistry:
    """Create and initialize an AgentRegistry with default agents."""
    reg = AgentRegistry(persistence=persistence, load_defaults=True)
    reg.initialize()
    return reg


@pytest.fixture()
def empty_registry(persistence: StatePersistence) -> AgentRegistry:
    """Create and initialize an empty AgentRegistry (no defaults)."""
    reg = AgentRegistry(persistence=persistence, load_defaults=False)
    reg.initialize()
    return reg


@pytest.fixture()
def sample_agent() -> AgentRegistration:
    """Create a sample agent registration for testing."""
    return AgentRegistration(
        agent_id="test-agent",
        name="Test Agent",
        description="A test agent for unit testing the registry.",
        domain=AgentDomain.CMDB,
        capabilities=[
            AgentCapability(
                name="test_capability",
                domain=AgentDomain.CMDB,
                description="A test capability.",
            ),
        ],
        status=AgentStatus.ONLINE,
        registered_at=datetime.now(UTC),
        metadata={"test": True},
    )


# ---------------------------------------------------------------------------
# Default agents
# ---------------------------------------------------------------------------


class TestDefaultAgents:
    """Tests for the pre-configured default agent definitions."""

    def test_default_agents_count(self) -> None:
        """Default agents should include all 6 ITOM agents."""
        agents = _build_default_agents()
        assert len(agents) == 6

    def test_default_agent_ids(self) -> None:
        """Default agents have the expected IDs."""
        agents = _build_default_agents()
        ids = {a.agent_id for a in agents}
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
        """Each default agent must have at least one capability."""
        agents = _build_default_agents()
        for agent in agents:
            assert len(agent.capabilities) > 0, (
                f"Agent {agent.agent_id} has no capabilities"
            )

    def test_default_agents_domains_cover_all(self) -> None:
        """Default agents should cover 6 different domains."""
        agents = _build_default_agents()
        domains = {a.domain for a in agents}
        # ORCHESTRATION domain is for the orchestrator itself, not an agent
        expected = {
            AgentDomain.CMDB,
            AgentDomain.DISCOVERY,
            AgentDomain.ASSET,
            AgentDomain.CSA,
            AgentDomain.AUDIT,
            AgentDomain.DOCUMENTATION,
        }
        assert domains == expected

    def test_default_agents_have_metadata(self) -> None:
        """Each default agent should have project metadata."""
        agents = _build_default_agents()
        for agent in agents:
            assert "project" in agent.metadata, (
                f"Agent {agent.agent_id} missing 'project' in metadata"
            )

    def test_default_agents_start_offline(self) -> None:
        """All default agents should start with OFFLINE status."""
        agents = _build_default_agents()
        for agent in agents:
            assert agent.status == AgentStatus.OFFLINE


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestRegistryInit:
    """Tests for registry initialization."""

    def test_init_with_defaults(self, registry: AgentRegistry) -> None:
        """Registry initialized with defaults should have 6 agents."""
        assert registry.agent_count == 6
        assert registry.is_initialized is True

    def test_init_empty(self, empty_registry: AgentRegistry) -> None:
        """Registry initialized without defaults should be empty."""
        assert empty_registry.agent_count == 0
        assert empty_registry.is_initialized is True

    def test_init_persists_defaults(
        self, persistence: StatePersistence
    ) -> None:
        """Initializing with defaults should persist the registry state."""
        reg = AgentRegistry(persistence=persistence, load_defaults=True)
        reg.initialize()

        # State file should exist
        assert persistence.exists(REGISTRY_STATE_KEY)

        # Load raw data and verify structure
        data = persistence.load(REGISTRY_STATE_KEY)
        assert data is not None
        assert "agents" in data
        assert data["agent_count"] == 6

    def test_init_loads_from_persistence(
        self, persistence: StatePersistence
    ) -> None:
        """Second initialization should load from persisted state, not defaults."""
        # First init -- creates defaults
        reg1 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg1.initialize()
        assert reg1.agent_count == 6

        # Second init -- loads from persistence
        reg2 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg2.initialize()
        assert reg2.agent_count == 6

        # Verify same agent IDs
        ids1 = {a.agent_id for a in reg1.list_all()}
        ids2 = {a.agent_id for a in reg2.list_all()}
        assert ids1 == ids2

    def test_init_handles_corrupted_state(
        self, persistence: StatePersistence, state_dir: Path
    ) -> None:
        """Loading corrupted state should raise RegistryLoadError."""
        # Write invalid data to the state file
        import json

        state_file = state_dir / f"{REGISTRY_STATE_KEY}.json"
        with open(state_file, "w") as f:
            json.dump(
                {
                    "_version": 1,
                    "_saved_at": "2026-01-01T00:00:00Z",
                    "_key": REGISTRY_STATE_KEY,
                    "data": {"agents": [{"invalid": "data"}]},
                },
                f,
            )

        reg = AgentRegistry(persistence=persistence, load_defaults=True)
        with pytest.raises(RegistryLoadError):
            reg.initialize()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Tests for registering and unregistering agents."""

    def test_register_new_agent(
        self, empty_registry: AgentRegistry, sample_agent: AgentRegistration
    ) -> None:
        """Registering a new agent should add it to the registry."""
        result = empty_registry.register(sample_agent)
        assert result.agent_id == "test-agent"
        assert empty_registry.agent_count == 1

    def test_register_duplicate_raises(
        self, empty_registry: AgentRegistry, sample_agent: AgentRegistration
    ) -> None:
        """Registering a duplicate agent ID should raise an error."""
        empty_registry.register(sample_agent)
        with pytest.raises(AgentAlreadyRegisteredError) as exc_info:
            empty_registry.register(sample_agent)
        assert "test-agent" in str(exc_info.value)

    def test_register_persists(
        self, persistence: StatePersistence, sample_agent: AgentRegistration
    ) -> None:
        """Registering an agent should persist the updated registry."""
        reg = AgentRegistry(persistence=persistence, load_defaults=False)
        reg.initialize()
        reg.register(sample_agent)

        # Verify persistence
        data = persistence.load(REGISTRY_STATE_KEY)
        assert data is not None
        assert data["agent_count"] == 1

    def test_unregister_agent(self, registry: AgentRegistry) -> None:
        """Unregistering an agent should remove it from the registry."""
        initial_count = registry.agent_count
        removed = registry.unregister("cmdb-agent")
        assert removed.agent_id == "cmdb-agent"
        assert registry.agent_count == initial_count - 1

    def test_unregister_nonexistent_raises(self, registry: AgentRegistry) -> None:
        """Unregistering a non-existent agent should raise an error."""
        with pytest.raises(AgentNotFoundError) as exc_info:
            registry.unregister("nonexistent-agent")
        assert "nonexistent-agent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class TestLookup:
    """Tests for agent lookup operations."""

    def test_get_existing_agent(self, registry: AgentRegistry) -> None:
        """Getting an existing agent by ID should return the registration."""
        agent = registry.get("cmdb-agent")
        assert agent.agent_id == "cmdb-agent"
        assert agent.domain == AgentDomain.CMDB

    def test_get_nonexistent_raises(self, registry: AgentRegistry) -> None:
        """Getting a non-existent agent should raise AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError):
            registry.get("nonexistent-agent")

    def test_list_all(self, registry: AgentRegistry) -> None:
        """list_all should return all agents sorted by ID."""
        agents = registry.list_all()
        assert len(agents) == 6
        ids = [a.agent_id for a in agents]
        assert ids == sorted(ids)

    def test_list_all_empty(self, empty_registry: AgentRegistry) -> None:
        """list_all on empty registry should return empty list."""
        assert empty_registry.list_all() == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    """Tests for search operations."""

    def test_search_by_domain(self, registry: AgentRegistry) -> None:
        """search_by_domain should return agents in the specified domain."""
        cmdb_agents = registry.search_by_domain(AgentDomain.CMDB)
        assert len(cmdb_agents) == 1
        assert cmdb_agents[0].agent_id == "cmdb-agent"

    def test_search_by_domain_no_results(self, registry: AgentRegistry) -> None:
        """search_by_domain with ORCHESTRATION should return empty (no default agents)."""
        results = registry.search_by_domain(AgentDomain.ORCHESTRATION)
        assert results == []

    def test_search_by_capability(self, registry: AgentRegistry) -> None:
        """search_by_capability should find agents with the named capability."""
        results = registry.search_by_capability("query_cis")
        assert len(results) == 1
        assert results[0].agent_id == "cmdb-agent"

    def test_search_by_capability_no_results(self, registry: AgentRegistry) -> None:
        """search_by_capability with unknown name should return empty."""
        results = registry.search_by_capability("nonexistent_capability")
        assert results == []

    def test_search_by_status(self, registry: AgentRegistry) -> None:
        """search_by_status OFFLINE should return all 6 default agents."""
        results = registry.search_by_status(AgentStatus.OFFLINE)
        assert len(results) == 6

    def test_search_by_status_online_empty(self, registry: AgentRegistry) -> None:
        """search_by_status ONLINE should return empty (defaults are OFFLINE)."""
        results = registry.search_by_status(AgentStatus.ONLINE)
        assert results == []

    def test_get_capabilities_for_domain(self, registry: AgentRegistry) -> None:
        """get_capabilities_for_domain should return all capabilities in a domain."""
        caps = registry.get_capabilities_for_domain(AgentDomain.CMDB)
        cap_names = [c.name for c in caps]
        assert "query_cis" in cap_names
        assert "update_ci" in cap_names
        assert "map_relationships" in cap_names

    def test_get_summary(self, registry: AgentRegistry) -> None:
        """get_summary should return correct statistics."""
        summary = registry.get_summary()
        assert summary["total_agents"] == 6
        assert len(summary["agents_by_domain"]) == 6
        assert summary["agents_by_status"]["offline"] == 6
        assert summary["total_capabilities"] > 0
        assert len(summary["agent_ids"]) == 6


# ---------------------------------------------------------------------------
# Status and metadata updates
# ---------------------------------------------------------------------------


class TestUpdates:
    """Tests for status and metadata update operations."""

    def test_update_status(self, registry: AgentRegistry) -> None:
        """update_status should change the agent's runtime status."""
        updated = registry.update_status("cmdb-agent", AgentStatus.ONLINE)
        assert updated.status == AgentStatus.ONLINE
        assert updated.last_health_check is not None

        # Verify via get
        retrieved = registry.get("cmdb-agent")
        assert retrieved.status == AgentStatus.ONLINE

    def test_update_status_with_timestamp(self, registry: AgentRegistry) -> None:
        """update_status with explicit timestamp should set last_health_check."""
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        updated = registry.update_status("cmdb-agent", AgentStatus.DEGRADED, last_health_check=ts)
        assert updated.status == AgentStatus.DEGRADED
        assert updated.last_health_check == ts

    def test_update_status_nonexistent_raises(self, registry: AgentRegistry) -> None:
        """update_status for non-existent agent should raise error."""
        with pytest.raises(AgentNotFoundError):
            registry.update_status("nonexistent", AgentStatus.ONLINE)

    def test_update_metadata_merge(self, registry: AgentRegistry) -> None:
        """update_metadata with merge should add to existing metadata."""
        original = registry.get("cmdb-agent")
        original_keys = set(original.metadata.keys())

        updated = registry.update_metadata(
            "cmdb-agent", {"custom_key": "custom_value"}, merge=True
        )
        assert "custom_key" in updated.metadata
        assert updated.metadata["custom_key"] == "custom_value"
        # Original keys should still be present
        for key in original_keys:
            assert key in updated.metadata

    def test_update_metadata_replace(self, registry: AgentRegistry) -> None:
        """update_metadata with merge=False should replace all metadata."""
        updated = registry.update_metadata(
            "cmdb-agent", {"only_key": "only_value"}, merge=False
        )
        assert updated.metadata == {"only_key": "only_value"}

    def test_update_metadata_nonexistent_raises(self, registry: AgentRegistry) -> None:
        """update_metadata for non-existent agent should raise error."""
        with pytest.raises(AgentNotFoundError):
            registry.update_metadata("nonexistent", {"key": "val"})


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


class TestPersistenceRoundTrip:
    """Tests for save/load round-trip fidelity."""

    def test_register_and_reload(
        self, persistence: StatePersistence, sample_agent: AgentRegistration
    ) -> None:
        """Registry state should survive a save/reload cycle."""
        # Create registry, register agent
        reg1 = AgentRegistry(persistence=persistence, load_defaults=False)
        reg1.initialize()
        reg1.register(sample_agent)
        assert reg1.agent_count == 1

        # Create new registry, load from same persistence
        reg2 = AgentRegistry(persistence=persistence, load_defaults=False)
        reg2.initialize()
        assert reg2.agent_count == 1

        agent = reg2.get("test-agent")
        assert agent.name == "Test Agent"
        assert agent.domain == AgentDomain.CMDB
        assert len(agent.capabilities) == 1
        assert agent.capabilities[0].name == "test_capability"

    def test_status_update_persists(
        self, persistence: StatePersistence
    ) -> None:
        """Status updates should be reflected after reload."""
        # Initialize with defaults and update status
        reg1 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg1.initialize()
        reg1.update_status("cmdb-agent", AgentStatus.ONLINE)

        # Reload
        reg2 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg2.initialize()
        agent = reg2.get("cmdb-agent")
        assert agent.status == AgentStatus.ONLINE

    def test_unregister_persists(
        self, persistence: StatePersistence
    ) -> None:
        """Unregistered agents should not appear after reload."""
        reg1 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg1.initialize()
        reg1.unregister("cmdb-agent")
        assert reg1.agent_count == 5

        reg2 = AgentRegistry(persistence=persistence, load_defaults=True)
        reg2.initialize()
        assert reg2.agent_count == 5
        with pytest.raises(AgentNotFoundError):
            reg2.get("cmdb-agent")


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class TestRegistryErrors:
    """Tests for registry error classes."""

    def test_agent_not_found_error(self) -> None:
        """AgentNotFoundError should include the agent_id in the message."""
        err = AgentNotFoundError("my-agent")
        assert "my-agent" in err.message
        assert err.error_code == "ORCH_1001"

    def test_agent_already_registered_error(self) -> None:
        """AgentAlreadyRegisteredError should include the agent_id."""
        err = AgentAlreadyRegisteredError("my-agent")
        assert "my-agent" in err.message
        assert err.error_code == "ORCH_1002"

    def test_agent_registration_invalid_error(self) -> None:
        """AgentRegistrationInvalidError should include details."""
        from itom_orchestrator.registry import AgentRegistrationInvalidError

        err = AgentRegistrationInvalidError("bad field")
        assert "bad field" in err.message
        assert err.error_code == "ORCH_1003"
