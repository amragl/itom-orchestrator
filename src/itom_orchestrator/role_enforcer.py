"""
Role boundary definitions and enforcement for the ITOM Orchestrator.

Provides role-based access control (RBAC) for agent actions. Each
agent has a role that defines which domains and actions it can access.

This module implements ORCH-018 and ORCH-020.
"""

import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class Permission(StrEnum):
    """Permission levels for agent actions."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class RolePolicy(BaseModel):
    """A role boundary policy for an agent.

    Defines what domains, actions, and permission levels a role
    is allowed to access.

    Attributes:
        role_id: Unique identifier for the role.
        name: Human-readable role name.
        description: What this role allows.
        allowed_domains: Domains this role can operate in.
        allowed_actions: Specific action patterns (e.g., 'cmdb.query').
        permissions: Permission levels granted.
    """

    role_id: str
    name: str
    description: str
    allowed_domains: list[AgentDomain] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)

    @field_validator("role_id")
    @classmethod
    def role_id_must_be_non_empty(cls, v: str) -> str:
        """Role ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("role_id must not be empty")
        return v

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        """Role name must be a non-empty string."""
        if not v.strip():
            raise ValueError("name must not be empty")
        return v


def _build_default_policies() -> list[RolePolicy]:
    """Build the default set of role policies for ITOM agents.

    Returns:
        List of RolePolicy objects covering all default ITOM roles.
    """
    all_domains = list(AgentDomain)

    return [
        RolePolicy(
            role_id="orchestrator",
            name="Orchestrator",
            description="Full admin access on all domains.",
            allowed_domains=all_domains,
            allowed_actions=["*"],
            permissions=[Permission.READ, Permission.WRITE, Permission.EXECUTE, Permission.ADMIN],
        ),
        RolePolicy(
            role_id="cmdb-agent",
            name="CMDB Agent",
            description="Read, write, and execute on CMDB domain only.",
            allowed_domains=[AgentDomain.CMDB],
            allowed_actions=[
                "cmdb.query", "cmdb.update", "cmdb.create", "cmdb.delete",
                "cmdb.health_audit", "cmdb.bulk_operations", "cmdb.map_relationships",
            ],
            permissions=[Permission.READ, Permission.WRITE, Permission.EXECUTE],
        ),
        RolePolicy(
            role_id="discovery-agent",
            name="Discovery Agent",
            description="Read, write, and execute on DISCOVERY domain only.",
            allowed_domains=[AgentDomain.DISCOVERY],
            allowed_actions=[
                "discovery.scan", "discovery.status", "discovery.reconcile",
                "discovery.schedule",
            ],
            permissions=[Permission.READ, Permission.WRITE, Permission.EXECUTE],
        ),
        RolePolicy(
            role_id="asset-agent",
            name="Asset Agent",
            description="Read, write, and execute on ASSET domain only.",
            allowed_domains=[AgentDomain.ASSET],
            allowed_actions=[
                "asset.query", "asset.lifecycle", "asset.reconcile",
                "asset.license_check",
            ],
            permissions=[Permission.READ, Permission.WRITE, Permission.EXECUTE],
        ),
        RolePolicy(
            role_id="itom-auditor",
            name="ITOM Auditor",
            description="Read and execute on all domains (read-only audit).",
            allowed_domains=all_domains,
            allowed_actions=[
                "audit.compliance", "audit.drift", "audit.report", "audit.policy",
            ],
            permissions=[Permission.READ, Permission.EXECUTE],
        ),
        RolePolicy(
            role_id="itom-documentator",
            name="ITOM Documentator",
            description="Read on all domains, write on DOCUMENTATION.",
            allowed_domains=all_domains,
            allowed_actions=[
                "documentation.generate", "documentation.runbook",
                "documentation.knowledge_base", "documentation.diagram",
            ],
            permissions=[Permission.READ, Permission.WRITE],
        ),
    ]


class RoleEnforcer:
    """Enforces role-based access control for agent actions.

    Checks whether a given role is permitted to perform a specific
    action on a specific domain. Returns False for unknown roles
    rather than raising exceptions.

    Args:
        policies: Optional list of role policies. If None, uses defaults.
    """

    def __init__(self, policies: list[RolePolicy] | None = None) -> None:
        self._policies: dict[str, RolePolicy] = {}
        effective_policies = policies if policies is not None else _build_default_policies()
        for policy in effective_policies:
            self._policies[policy.role_id] = policy

    def add_policy(self, policy: RolePolicy) -> None:
        """Add or replace a role policy.

        Args:
            policy: The policy to add.
        """
        self._policies[policy.role_id] = policy
        logger.info(
            "Role policy added",
            extra={
                "extra_data": {
                    "role_id": policy.role_id,
                    "domains": [d.value for d in policy.allowed_domains],
                    "permissions": [p.value for p in policy.permissions],
                }
            },
        )

    def check_permission(
        self,
        role_id: str,
        action: str,
        domain: AgentDomain | None = None,
    ) -> bool:
        """Check if a role is permitted to perform an action.

        Returns False for unknown roles (does not raise).

        Args:
            role_id: The role to check.
            action: The action to check (e.g., 'cmdb.query').
            domain: Optional domain to check against.

        Returns:
            True if the action is permitted, False otherwise.
        """
        policy = self._policies.get(role_id)
        if policy is None:
            logger.debug(
                "Permission check: unknown role",
                extra={"extra_data": {"role_id": role_id, "action": action}},
            )
            return False

        # Admin wildcard
        if "*" in policy.allowed_actions:
            return True

        # Check domain restriction
        if domain is not None and policy.allowed_domains:
            if domain not in policy.allowed_domains:
                logger.debug(
                    "Permission denied: domain not allowed",
                    extra={
                        "extra_data": {
                            "role_id": role_id,
                            "action": action,
                            "domain": domain.value,
                        }
                    },
                )
                return False

        # Check action
        if action in policy.allowed_actions:
            return True

        # Check action prefix matching (e.g., 'cmdb.*' matches 'cmdb.query')
        action_prefix = action.split(".")[0] + ".*" if "." in action else ""
        if action_prefix and action_prefix in policy.allowed_actions:
            return True

        logger.debug(
            "Permission denied: action not allowed",
            extra={
                "extra_data": {
                    "role_id": role_id,
                    "action": action,
                    "allowed_actions": policy.allowed_actions,
                }
            },
        )
        return False

    def get_allowed_domains(self, role_id: str) -> list[AgentDomain]:
        """Get the domains a role is allowed to access.

        Args:
            role_id: The role to look up.

        Returns:
            List of allowed domains (empty for unknown roles).
        """
        policy = self._policies.get(role_id)
        if policy is None:
            return []
        return list(policy.allowed_domains)

    def get_policy(self, role_id: str) -> RolePolicy | None:
        """Get the policy for a role.

        Args:
            role_id: The role to look up.

        Returns:
            The RolePolicy, or None if the role is not found.
        """
        return self._policies.get(role_id)

    def list_policies(self) -> list[RolePolicy]:
        """List all registered policies.

        Returns:
            List of RolePolicy objects sorted by role_id.
        """
        return sorted(self._policies.values(), key=lambda p: p.role_id)

    @property
    def policy_count(self) -> int:
        """Return the number of registered policies."""
        return len(self._policies)


def load_role_config(path: Path) -> list[RolePolicy]:
    """Load role policies from a JSON configuration file.

    Args:
        path: Path to the role configuration JSON file.

    Returns:
        List of parsed RolePolicy objects.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the JSON is malformed or validation fails.
    """
    if not path.exists():
        raise FileNotFoundError(f"Role config not found: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in role config: {exc}") from exc

    policies_data = raw.get("policies", raw) if isinstance(raw, dict) else raw
    if not isinstance(policies_data, list):
        raise ValueError("Role config must contain a list of policies")

    policies: list[RolePolicy] = []
    for item in policies_data:
        policies.append(RolePolicy.model_validate(item))

    logger.info(
        "Role configuration loaded",
        extra={"extra_data": {"path": str(path), "policy_count": len(policies)}},
    )
    return policies


def save_role_config(policies: list[RolePolicy], path: Path) -> None:
    """Save role policies to a JSON configuration file.

    Args:
        policies: The policies to save.
        path: Path to write the configuration to.

    Raises:
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"policies": [p.model_dump(mode="json") for p in policies]}

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    logger.info(
        "Role configuration saved",
        extra={"extra_data": {"path": str(path), "policy_count": len(policies)}},
    )


def validate_role_config(policies: list[RolePolicy]) -> list[str]:
    """Validate a list of role policies for consistency.

    Checks for:
    - Duplicate role IDs
    - Policies with no permissions
    - Policies with no allowed domains or actions

    Args:
        policies: The policies to validate.

    Returns:
        List of validation error messages. Empty if valid.
    """
    errors: list[str] = []

    # Check for duplicate role IDs
    seen_ids: set[str] = set()
    for policy in policies:
        if policy.role_id in seen_ids:
            errors.append(f"Duplicate role_id: '{policy.role_id}'")
        seen_ids.add(policy.role_id)

    # Check for empty policies
    for policy in policies:
        if not policy.permissions:
            errors.append(f"Policy '{policy.role_id}' has no permissions")
        if not policy.allowed_domains and not policy.allowed_actions:
            errors.append(
                f"Policy '{policy.role_id}' has no allowed domains or actions"
            )

    return errors


def get_default_enforcer() -> RoleEnforcer:
    """Create a RoleEnforcer pre-loaded with default policies.

    Returns:
        RoleEnforcer with all default ITOM role policies.
    """
    return RoleEnforcer(policies=_build_default_policies())
