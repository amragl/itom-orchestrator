"""
Agent-related Pydantic models for the ITOM Orchestrator.

Defines the data contracts for agent registration, capabilities,
domains, and runtime status tracking. These models are used by the
Agent Registry, Task Router, and Role Enforcer.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentDomain(StrEnum):
    """Domains an agent can operate in.

    Each ITOM agent has a primary domain that determines which types
    of tasks it can handle and what ServiceNow tables/APIs it accesses.
    """

    CMDB = "cmdb"
    DISCOVERY = "discovery"
    ASSET = "asset"
    CSA = "csa"
    AUDIT = "audit"
    DOCUMENTATION = "documentation"
    ORCHESTRATION = "orchestration"


class AgentStatus(StrEnum):
    """Runtime status of an agent.

    Tracks whether an agent is available to receive tasks.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"


class AgentCapability(BaseModel):
    """A specific capability an agent provides.

    Capabilities are used by the Task Router to match incoming tasks
    to agents that can handle them. Each capability declares its
    domain, a human-readable description, and optional JSON Schema
    definitions for inputs and outputs.

    Attributes:
        name: Machine-readable capability name (e.g., ``"query_cis"``).
        domain: The domain this capability belongs to.
        description: Human-readable description of what the capability does.
        input_schema: Optional JSON Schema defining expected input parameters.
        output_schema: Optional JSON Schema defining the output structure.
    """

    name: str
    domain: AgentDomain
    description: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        """Capability name must be a non-empty string."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Capability name must not be empty")
        return stripped

    @field_validator("description")
    @classmethod
    def description_must_be_non_empty(cls, v: str) -> str:
        """Capability description must be a non-empty string."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Capability description must not be empty")
        return stripped


_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class AgentRegistration(BaseModel):
    """Complete registration info for an ITOM agent.

    This is the primary model stored in the Agent Registry. It contains
    everything the orchestrator needs to know about an agent: identity,
    capabilities, connection details, and health status.

    Attributes:
        agent_id: Unique identifier (e.g., ``"cmdb-agent"``). Must be
            lowercase alphanumeric with hyphens, starting with a letter.
        name: Human-readable display name.
        description: What this agent does.
        domain: Primary domain the agent operates in.
        capabilities: List of capabilities the agent provides.
        mcp_server_url: MCP endpoint URL if the agent runs as a remote server.
        status: Current runtime status.
        registered_at: When this agent was first registered.
        last_health_check: When the last health check was performed.
        metadata: Arbitrary key-value metadata for extensibility.
    """

    agent_id: str
    name: str
    description: str
    domain: AgentDomain
    capabilities: list[AgentCapability]
    mcp_server_url: str | None = None
    status: AgentStatus = AgentStatus.OFFLINE
    registered_at: datetime
    last_health_check: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("agent_id")
    @classmethod
    def agent_id_must_be_valid(cls, v: str) -> str:
        """Agent ID must be non-empty, lowercase, alphanumeric with hyphens only.

        Must start with a letter. Examples of valid IDs: ``"cmdb-agent"``,
        ``"discovery-agent"``, ``"csa-agent"``.
        """
        if not v:
            raise ValueError("agent_id must not be empty")
        if not _AGENT_ID_PATTERN.match(v):
            raise ValueError(
                f"agent_id '{v}' is invalid. Must be lowercase alphanumeric with "
                f"hyphens, starting with a letter (pattern: {_AGENT_ID_PATTERN.pattern})"
            )
        return v

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        """Agent name must be a non-empty string."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Agent name must not be empty")
        return stripped

    @field_validator("description")
    @classmethod
    def description_must_be_non_empty(cls, v: str) -> str:
        """Agent description must be a non-empty string."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Agent description must not be empty")
        return stripped
