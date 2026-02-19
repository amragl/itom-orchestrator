"""
Agent Registry for the ITOM Orchestrator.

Manages agent registration, lookup, capability search, and domain filtering.
Pre-configured with definitions for all 6 ITOM agents. Registry state is
persisted via the StatePersistence layer using atomic writes.

This module implements ORCH-005: Agent Registry -- registration and
capability declaration.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from itom_orchestrator.error_codes import (
    ORCH_1001_AGENT_NOT_FOUND,
    ORCH_1002_AGENT_ALREADY_REGISTERED,
    ORCH_1003_AGENT_REGISTRATION_INVALID,
    ORCH_1004_REGISTRY_LOAD_FAILED,
    ORCH_1005_REGISTRY_SAVE_FAILED,
)
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.persistence import StatePersistence

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Persistence key for registry state
REGISTRY_STATE_KEY = "agent-registry"


class RegistryError(Exception):
    """Base exception for registry operations.

    Attributes:
        error_code: Machine-readable error code from error_codes.py.
        message: Human-readable error description.
    """

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class AgentNotFoundError(RegistryError):
    """Raised when an agent ID is not found in the registry."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            ORCH_1001_AGENT_NOT_FOUND,
            f"Agent '{agent_id}' not found in registry.",
        )


class AgentAlreadyRegisteredError(RegistryError):
    """Raised when attempting to register an agent with a duplicate ID."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            ORCH_1002_AGENT_ALREADY_REGISTERED,
            f"Agent '{agent_id}' is already registered.",
        )


class AgentRegistrationInvalidError(RegistryError):
    """Raised when agent registration data fails validation."""

    def __init__(self, details: str) -> None:
        super().__init__(
            ORCH_1003_AGENT_REGISTRATION_INVALID,
            f"Invalid agent registration: {details}",
        )


class RegistryLoadError(RegistryError):
    """Raised when registry state cannot be loaded."""

    def __init__(self, details: str) -> None:
        super().__init__(
            ORCH_1004_REGISTRY_LOAD_FAILED,
            f"Failed to load agent registry: {details}",
        )


class RegistrySaveError(RegistryError):
    """Raised when registry state cannot be saved."""

    def __init__(self, details: str) -> None:
        super().__init__(
            ORCH_1005_REGISTRY_SAVE_FAILED,
            f"Failed to save agent registry: {details}",
        )


def _build_default_agents() -> list[AgentRegistration]:
    """Build the pre-configured registration definitions for all 6 ITOM agents.

    These definitions describe the agents that the orchestrator coordinates.
    Each agent has a unique ID, domain, and set of capabilities that the
    Task Router uses for intelligent routing.

    Returns:
        List of AgentRegistration objects for cmdb-agent, discovery-agent,
        asset-agent, csa-agent, itom-auditor, and itom-documentator.
    """
    now = datetime.now(UTC)

    return [
        AgentRegistration(
            agent_id="cmdb-agent",
            name="CMDB Agent",
            description=(
                "Autonomous CMDB management agent (snow-cmdb-agent). "
                "Full CMDB domain owner: CI queries across all cmdb_ci* types, "
                "health metrics, duplicate/stale detection, IRE rules, relationship "
                "mapping, impact analysis, remediation lifecycle, and autonomous workflows. "
                "Runs on streamable-HTTP at http://localhost:8002/mcp."
            ),
            domain=AgentDomain.CMDB,
            capabilities=[
                AgentCapability(
                    name="cmdb_read",
                    domain=AgentDomain.CMDB,
                    description=(
                        "Query and analyse configuration items across the full cmdb_ci hierarchy: "
                        "server, linux_server, win_server, database, application, network_gear, "
                        "storage_device, computer, service."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ci_type": {"type": "string"},
                            "query": {"type": "string"},
                            "environment": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["ci_type"],
                    },
                ),
                AgentCapability(
                    name="cmdb_write",
                    domain=AgentDomain.CMDB,
                    description=(
                        "Remediate CMDB issues: create/monitor/execute/complete remediation "
                        "requests, run maintenance workflows, reconcile CI data."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "remediation_type": {"type": "string"},
                            "risk_level": {"type": "string"},
                            "affected_ci_sys_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["remediation_type", "risk_level"],
                    },
                ),
                AgentCapability(
                    name="query_cis",
                    domain=AgentDomain.CMDB,
                    description="Query configuration items with filtering and pagination.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ci_type": {"type": "string"},
                            "query": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["ci_type"],
                    },
                ),
                AgentCapability(
                    name="map_relationships",
                    domain=AgentDomain.CMDB,
                    description="Map and traverse CI relationships, including dependency trees and impact analysis.",
                ),
                AgentCapability(
                    name="cmdb_health_audit",
                    domain=AgentDomain.CMDB,
                    description=(
                        "Run health checks on CMDB data quality, staleness, duplicates, "
                        "orphaned CIs, and IRE rules across all CI types."
                    ),
                ),
                AgentCapability(
                    name="bulk_ci_operations",
                    domain=AgentDomain.CMDB,
                    description="Perform bulk maintenance operations on CIs via autonomous workflows.",
                ),
            ],
            mcp_server_url="http://localhost:8002/mcp",
            status=AgentStatus.ONLINE,
            registered_at=now,
            metadata={"project": "snow-cmdb-agent", "version": "2.0.0", "port": 8002},
        ),
        AgentRegistration(
            agent_id="discovery-agent",
            name="Discovery Agent",
            description=(
                "ServiceNow Discovery automation agent. Manages discovery schedules, "
                "scans, CI reconciliation, credential management, and pattern-based "
                "classification of discovered infrastructure."
            ),
            domain=AgentDomain.DISCOVERY,
            capabilities=[
                AgentCapability(
                    name="run_discovery_scan",
                    domain=AgentDomain.DISCOVERY,
                    description="Trigger a discovery scan for a specific IP range or schedule.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ip_range": {"type": "string"},
                            "schedule_id": {"type": "string"},
                            "scan_type": {"type": "string", "enum": ["full", "incremental"]},
                        },
                    },
                ),
                AgentCapability(
                    name="get_discovery_status",
                    domain=AgentDomain.DISCOVERY,
                    description="Check the status and results of a running or completed discovery scan.",
                ),
                AgentCapability(
                    name="reconcile_discovered_cis",
                    domain=AgentDomain.DISCOVERY,
                    description="Reconcile discovered CIs with existing CMDB records.",
                ),
                AgentCapability(
                    name="manage_discovery_schedules",
                    domain=AgentDomain.DISCOVERY,
                    description="Create, update, or delete discovery schedules.",
                ),
            ],
            mcp_server_url=None,
            status=AgentStatus.OFFLINE,
            registered_at=now,
            metadata={"project": "snow-discovery-agent", "version": "0.1.0"},
        ),
        AgentRegistration(
            agent_id="asset-agent",
            name="Asset Agent",
            description=(
                "ServiceNow IT Asset Management agent. Handles asset lifecycle, "
                "inventory tracking, contract and license management, hardware "
                "and software asset reconciliation."
            ),
            domain=AgentDomain.ASSET,
            capabilities=[
                AgentCapability(
                    name="query_assets",
                    domain=AgentDomain.ASSET,
                    description="Query IT assets with filtering by type, status, assignment, and location.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "asset_type": {"type": "string", "enum": ["hardware", "software", "consumable"]},
                            "status": {"type": "string"},
                            "assigned_to": {"type": "string"},
                        },
                    },
                ),
                AgentCapability(
                    name="manage_asset_lifecycle",
                    domain=AgentDomain.ASSET,
                    description="Track and manage asset lifecycle from procurement to retirement.",
                ),
                AgentCapability(
                    name="reconcile_assets",
                    domain=AgentDomain.ASSET,
                    description="Reconcile asset records with CMDB CIs and discovery data.",
                ),
                AgentCapability(
                    name="license_compliance_check",
                    domain=AgentDomain.ASSET,
                    description="Check software license compliance and usage against entitlements.",
                ),
            ],
            mcp_server_url=None,
            status=AgentStatus.OFFLINE,
            registered_at=now,
            metadata={"project": "snow-asset-agent", "version": "0.1.0"},
        ),
        AgentRegistration(
            agent_id="csa-agent",
            name="CSA Agent",
            description=(
                "ServiceNow Certified System Administrator agent. Manages service "
                "catalog items, workflows, request fulfillment, and system "
                "administration remediation tasks."
            ),
            domain=AgentDomain.CSA,
            capabilities=[
                AgentCapability(
                    name="manage_catalog_items",
                    domain=AgentDomain.CSA,
                    description="Create, update, and configure service catalog items and categories.",
                ),
                AgentCapability(
                    name="manage_workflows",
                    domain=AgentDomain.CSA,
                    description="Create, update, and monitor workflow definitions and executions.",
                ),
                AgentCapability(
                    name="fulfill_requests",
                    domain=AgentDomain.CSA,
                    description="Process and fulfill service requests through the request pipeline.",
                ),
                AgentCapability(
                    name="run_remediation",
                    domain=AgentDomain.CSA,
                    description="Execute system administration remediation tasks from the catalog.",
                ),
            ],
            mcp_server_url=None,
            status=AgentStatus.OFFLINE,
            registered_at=now,
            metadata={"project": "snow-csa-agent", "version": "0.1.0"},
        ),
        AgentRegistration(
            agent_id="itom-auditor",
            name="ITOM Auditor",
            description=(
                "Read-only governance and compliance auditor for the ITOM suite. "
                "Performs cross-agent audits, compliance checks, configuration "
                "drift detection, and generates audit reports."
            ),
            domain=AgentDomain.AUDIT,
            capabilities=[
                AgentCapability(
                    name="run_compliance_audit",
                    domain=AgentDomain.AUDIT,
                    description="Run a comprehensive compliance audit across ITOM components.",
                ),
                AgentCapability(
                    name="detect_configuration_drift",
                    domain=AgentDomain.AUDIT,
                    description="Detect configuration drift between expected and actual states.",
                ),
                AgentCapability(
                    name="generate_audit_report",
                    domain=AgentDomain.AUDIT,
                    description="Generate structured audit reports in markdown or JSON format.",
                ),
                AgentCapability(
                    name="check_policy_compliance",
                    domain=AgentDomain.AUDIT,
                    description="Validate actions and configurations against defined policies.",
                ),
            ],
            mcp_server_url=None,
            status=AgentStatus.OFFLINE,
            registered_at=now,
            metadata={"project": "snow-itom-auditor", "version": "0.1.0"},
        ),
        AgentRegistration(
            agent_id="itom-documentator",
            name="ITOM Documentator",
            description=(
                "Read-only documentation and knowledge management agent. "
                "Generates technical documentation, runbooks, architecture "
                "diagrams, and maintains the ITOM knowledge base."
            ),
            domain=AgentDomain.DOCUMENTATION,
            capabilities=[
                AgentCapability(
                    name="generate_documentation",
                    domain=AgentDomain.DOCUMENTATION,
                    description="Generate technical documentation for ITOM components and workflows.",
                ),
                AgentCapability(
                    name="create_runbook",
                    domain=AgentDomain.DOCUMENTATION,
                    description="Create operational runbooks for common ITOM procedures.",
                ),
                AgentCapability(
                    name="update_knowledge_base",
                    domain=AgentDomain.DOCUMENTATION,
                    description="Update the ITOM knowledge base with new findings and procedures.",
                ),
                AgentCapability(
                    name="generate_architecture_diagram",
                    domain=AgentDomain.DOCUMENTATION,
                    description="Generate architecture and relationship diagrams for ITOM infrastructure.",
                ),
            ],
            mcp_server_url=None,
            status=AgentStatus.OFFLINE,
            registered_at=now,
            metadata={"project": "snow-itom-documentator", "version": "0.1.0"},
        ),
    ]


class AgentRegistry:
    """Central registry for all ITOM agents.

    Manages agent registration, lookup, search by capability and domain,
    and persistence of registry state through the StatePersistence layer.

    The registry is initialized with pre-configured definitions for all 6
    ITOM agents. Custom agents can be registered at runtime.

    Args:
        persistence: StatePersistence instance for saving/loading registry state.
        load_defaults: If True, populate with default ITOM agent definitions
            when no persisted state exists. Defaults to True.
    """

    def __init__(
        self,
        persistence: StatePersistence,
        load_defaults: bool = True,
    ) -> None:
        self._persistence = persistence
        self._agents: dict[str, AgentRegistration] = {}
        self._load_defaults = load_defaults
        self._initialized = False

    def initialize(self) -> None:
        """Load registry from persistence or populate with defaults.

        Call this after construction to load persisted state. If no state
        exists and ``load_defaults`` is True, the registry is populated
        with the 6 default ITOM agent definitions and persisted.

        Raises:
            RegistryLoadError: If persisted state exists but cannot be parsed.
        """
        loaded = self._persistence.load(REGISTRY_STATE_KEY)

        if loaded is not None:
            try:
                agents_data = loaded.get("agents", [])
                for agent_dict in agents_data:
                    agent = AgentRegistration.model_validate(agent_dict)
                    self._agents[agent.agent_id] = agent
                logger.info(
                    "Registry loaded from persistence",
                    extra={"extra_data": {"agent_count": len(self._agents)}},
                )
            except Exception as exc:
                raise RegistryLoadError(str(exc)) from exc
        elif self._load_defaults:
            for agent in _build_default_agents():
                self._agents[agent.agent_id] = agent
            self._save()
            logger.info(
                "Registry initialized with default agents",
                extra={"extra_data": {"agent_count": len(self._agents)}},
            )
        else:
            logger.info("Registry initialized empty (no defaults)")

        self._initialized = True

    def _save(self) -> None:
        """Persist current registry state.

        Raises:
            RegistrySaveError: If the state cannot be written.
        """
        data = {
            "agents": [
                agent.model_dump(mode="json") for agent in self._agents.values()
            ],
            "agent_count": len(self._agents),
            "last_updated": datetime.now(UTC).isoformat(),
        }
        try:
            self._persistence.save(REGISTRY_STATE_KEY, data)
        except OSError as exc:
            raise RegistrySaveError(str(exc)) from exc

    def register(self, agent: AgentRegistration) -> AgentRegistration:
        """Register a new agent in the registry.

        Args:
            agent: Validated AgentRegistration to add.

        Returns:
            The registered AgentRegistration.

        Raises:
            AgentAlreadyRegisteredError: If an agent with the same ID exists.
            RegistrySaveError: If the registry cannot be persisted after registration.
        """
        if agent.agent_id in self._agents:
            raise AgentAlreadyRegisteredError(agent.agent_id)

        self._agents[agent.agent_id] = agent
        self._save()

        logger.info(
            "Agent registered",
            extra={
                "extra_data": {
                    "agent_id": agent.agent_id,
                    "domain": agent.domain.value,
                    "capabilities": [c.name for c in agent.capabilities],
                }
            },
        )
        return agent

    def unregister(self, agent_id: str) -> AgentRegistration:
        """Remove an agent from the registry.

        Args:
            agent_id: The ID of the agent to remove.

        Returns:
            The removed AgentRegistration.

        Raises:
            AgentNotFoundError: If no agent with the given ID is registered.
            RegistrySaveError: If the registry cannot be persisted after removal.
        """
        if agent_id not in self._agents:
            raise AgentNotFoundError(agent_id)

        removed = self._agents.pop(agent_id)
        self._save()

        logger.info(
            "Agent unregistered",
            extra={"extra_data": {"agent_id": agent_id}},
        )
        return removed

    def get(self, agent_id: str) -> AgentRegistration:
        """Look up an agent by ID.

        Args:
            agent_id: The ID of the agent to retrieve.

        Returns:
            The AgentRegistration for the given ID.

        Raises:
            AgentNotFoundError: If no agent with the given ID is registered.
        """
        if agent_id not in self._agents:
            raise AgentNotFoundError(agent_id)
        return self._agents[agent_id]

    def list_all(self) -> list[AgentRegistration]:
        """Return all registered agents.

        Returns:
            List of all AgentRegistration objects, sorted by agent_id.
        """
        return sorted(self._agents.values(), key=lambda a: a.agent_id)

    def search_by_domain(self, domain: AgentDomain) -> list[AgentRegistration]:
        """Find agents that operate in the specified domain.

        Args:
            domain: The AgentDomain to filter by.

        Returns:
            List of agents whose primary domain matches, sorted by agent_id.
        """
        results = [a for a in self._agents.values() if a.domain == domain]
        return sorted(results, key=lambda a: a.agent_id)

    def search_by_capability(self, capability_name: str) -> list[AgentRegistration]:
        """Find agents that declare a specific capability.

        Args:
            capability_name: Exact capability name to search for.

        Returns:
            List of agents that have a capability with the given name,
            sorted by agent_id.
        """
        results = [
            a
            for a in self._agents.values()
            if any(c.name == capability_name for c in a.capabilities)
        ]
        return sorted(results, key=lambda a: a.agent_id)

    def search_by_status(self, status: AgentStatus) -> list[AgentRegistration]:
        """Find agents with the specified runtime status.

        Args:
            status: The AgentStatus to filter by.

        Returns:
            List of agents with the given status, sorted by agent_id.
        """
        results = [a for a in self._agents.values() if a.status == status]
        return sorted(results, key=lambda a: a.agent_id)

    def update_status(
        self,
        agent_id: str,
        status: AgentStatus,
        last_health_check: datetime | None = None,
    ) -> AgentRegistration:
        """Update an agent's runtime status.

        Args:
            agent_id: The ID of the agent to update.
            status: The new status to set.
            last_health_check: Optional timestamp of the most recent health check.
                If None, the current UTC time is used.

        Returns:
            The updated AgentRegistration.

        Raises:
            AgentNotFoundError: If no agent with the given ID is registered.
            RegistrySaveError: If the registry cannot be persisted after the update.
        """
        if agent_id not in self._agents:
            raise AgentNotFoundError(agent_id)

        agent = self._agents[agent_id]
        check_time = last_health_check or datetime.now(UTC)

        # Create an updated copy -- Pydantic models are immutable by default
        updated = agent.model_copy(
            update={"status": status, "last_health_check": check_time}
        )
        self._agents[agent_id] = updated
        self._save()

        logger.info(
            "Agent status updated",
            extra={
                "extra_data": {
                    "agent_id": agent_id,
                    "old_status": agent.status.value,
                    "new_status": status.value,
                }
            },
        )
        return updated

    def update_metadata(
        self,
        agent_id: str,
        metadata: dict[str, Any],
        merge: bool = True,
    ) -> AgentRegistration:
        """Update an agent's metadata.

        Args:
            agent_id: The ID of the agent to update.
            metadata: New metadata key-value pairs.
            merge: If True, merge with existing metadata. If False, replace entirely.

        Returns:
            The updated AgentRegistration.

        Raises:
            AgentNotFoundError: If no agent with the given ID is registered.
            RegistrySaveError: If the registry cannot be persisted after the update.
        """
        if agent_id not in self._agents:
            raise AgentNotFoundError(agent_id)

        agent = self._agents[agent_id]
        if merge:
            new_metadata = {**agent.metadata, **metadata}
        else:
            new_metadata = metadata

        updated = agent.model_copy(update={"metadata": new_metadata})
        self._agents[agent_id] = updated
        self._save()

        logger.info(
            "Agent metadata updated",
            extra={"extra_data": {"agent_id": agent_id, "merge": merge}},
        )
        return updated

    def get_capabilities_for_domain(self, domain: AgentDomain) -> list[AgentCapability]:
        """Get all capabilities available in a domain.

        Aggregates capabilities from all agents in the specified domain.

        Args:
            domain: The AgentDomain to get capabilities for.

        Returns:
            Flat list of all capabilities from agents in the domain.
        """
        capabilities: list[AgentCapability] = []
        for agent in self.search_by_domain(domain):
            capabilities.extend(agent.capabilities)
        return capabilities

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the current registry state.

        Returns:
            Dictionary with total_agents, agents_by_domain, agents_by_status,
            total_capabilities, and agent_ids.
        """
        by_domain: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_capabilities = 0

        for agent in self._agents.values():
            domain_key = agent.domain.value
            by_domain[domain_key] = by_domain.get(domain_key, 0) + 1

            status_key = agent.status.value
            by_status[status_key] = by_status.get(status_key, 0) + 1

            total_capabilities += len(agent.capabilities)

        return {
            "total_agents": len(self._agents),
            "agents_by_domain": by_domain,
            "agents_by_status": by_status,
            "total_capabilities": total_capabilities,
            "agent_ids": sorted(self._agents.keys()),
        }

    @property
    def agent_count(self) -> int:
        """Return the number of registered agents."""
        return len(self._agents)

    @property
    def is_initialized(self) -> bool:
        """Whether the registry has been initialized."""
        return self._initialized
