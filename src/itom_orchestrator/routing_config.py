"""
Routing rules configuration management for the ITOM Orchestrator.

Provides externalized routing rules via a Pydantic model and
JSON file persistence. Allows routing behaviour to be changed
without modifying code.

This module implements ORCH-010: Routing Rules Configuration.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class RoutingRuleConfig(BaseModel):
    """A single routing rule definition loaded from configuration.

    Attributes:
        rule_id: Unique identifier for the rule.
        name: Human-readable rule name.
        priority: Evaluation order (lower = higher priority).
        domain: Target agent domain for matching.
        keywords: Keywords that trigger this rule.
        target_agent: Explicit agent ID to route to.
        capability: Capability name to match.
        enabled: Whether the rule is active.
    """

    rule_id: str
    name: str
    priority: int = 100
    domain: AgentDomain | None = None
    keywords: list[str] = Field(default_factory=list)
    target_agent: str | None = None
    capability: str | None = None
    enabled: bool = True

    @field_validator("rule_id")
    @classmethod
    def rule_id_must_be_non_empty(cls, v: str) -> str:
        """Rule ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("rule_id must not be empty")
        return v

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        """Rule name must be a non-empty string."""
        if not v.strip():
            raise ValueError("name must not be empty")
        return v

    @field_validator("priority")
    @classmethod
    def priority_must_be_positive(cls, v: int) -> int:
        """Priority must be a positive integer."""
        if v < 0:
            raise ValueError(f"priority must be >= 0, got {v}")
        return v


class RoutingConfig(BaseModel):
    """Externalized routing configuration.

    Holds the complete set of routing rules loaded from a JSON
    configuration file. Rules are validated on load.

    Attributes:
        version: Configuration schema version.
        rules: List of routing rule configurations.
        default_domain: Fallback domain when no rule matches.
        metadata: Arbitrary key-value metadata.
    """

    version: str = "1.0.0"
    rules: list[RoutingRuleConfig] = Field(default_factory=list)
    default_domain: AgentDomain | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def version_must_be_non_empty(cls, v: str) -> str:
        """Version must be a non-empty string."""
        if not v.strip():
            raise ValueError("version must not be empty")
        return v


def load_routing_config(path: Path) -> RoutingConfig:
    """Load routing configuration from a JSON file.

    Args:
        path: Path to the routing configuration JSON file.

    Returns:
        Parsed and validated RoutingConfig instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the JSON is malformed or validation fails.
    """
    if not path.exists():
        raise FileNotFoundError(f"Routing config not found: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in routing config: {exc}") from exc

    config = RoutingConfig.model_validate(raw)

    logger.info(
        "Routing configuration loaded",
        extra={
            "extra_data": {
                "path": str(path),
                "version": config.version,
                "rule_count": len(config.rules),
            }
        },
    )
    return config


def validate_routing_config(config: RoutingConfig) -> list[str]:
    """Validate a routing configuration for consistency.

    Checks for:
    - Duplicate rule IDs
    - Rules with no matching criteria (no domain, keywords, or capability)
    - Enabled rule count

    Args:
        config: The RoutingConfig to validate.

    Returns:
        List of validation error messages. Empty if valid.
    """
    errors: list[str] = []

    # Check for duplicate rule IDs
    seen_ids: set[str] = set()
    for rule in config.rules:
        if rule.rule_id in seen_ids:
            errors.append(f"Duplicate rule_id: '{rule.rule_id}'")
        seen_ids.add(rule.rule_id)

    # Check rules have at least one matching criterion
    for rule in config.rules:
        if not rule.domain and not rule.keywords and not rule.capability and not rule.target_agent:
            errors.append(
                f"Rule '{rule.rule_id}' has no matching criteria "
                f"(no domain, keywords, capability, or target_agent)"
            )

    # Warn if no enabled rules
    enabled_count = sum(1 for r in config.rules if r.enabled)
    if config.rules and enabled_count == 0:
        errors.append("All routing rules are disabled")

    if errors:
        logger.warning(
            "Routing config validation issues",
            extra={"extra_data": {"error_count": len(errors), "errors": errors}},
        )
    else:
        logger.info(
            "Routing config validation passed",
            extra={"extra_data": {"rule_count": len(config.rules), "enabled": enabled_count}},
        )

    return errors
