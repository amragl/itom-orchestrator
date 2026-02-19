"""
Task Router for the ITOM Orchestrator.

Implements domain-based routing, capability matching, agent availability
checks, explicit agent targeting, and configurable routing rules. Routes
incoming tasks to the most appropriate agent based on task attributes and
agent capabilities.

This module implements ORCH-008: Task Router -- domain-based routing engine.
SE-010: Adds ambiguity detection that returns a ClarificationContext when
two domains match at equal priority.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from itom_orchestrator.error_codes import (
    ORCH_2001_NO_ROUTE_FOUND,
    ORCH_2002_AGENT_UNAVAILABLE,
    ORCH_2005_AMBIGUOUS_ROUTE,
)
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain, AgentRegistration, AgentStatus
from itom_orchestrator.models.tasks import Task, TaskStatus
from itom_orchestrator.registry import AgentNotFoundError, AgentRegistry

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class RoutingError(Exception):
    """Base exception for routing operations.

    Attributes:
        error_code: Machine-readable error code from error_codes.py.
        message: Human-readable error description.
    """

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class NoRouteFoundError(RoutingError):
    """Raised when no agent can handle the given task."""

    def __init__(self, task_id: str, reason: str) -> None:
        super().__init__(
            ORCH_2001_NO_ROUTE_FOUND,
            f"No route found for task '{task_id}': {reason}",
        )


class AgentUnavailableError(RoutingError):
    """Raised when the target agent exists but is not available."""

    def __init__(self, agent_id: str, status: str) -> None:
        super().__init__(
            ORCH_2002_AGENT_UNAVAILABLE,
            f"Agent '{agent_id}' is unavailable (status: {status}).",
        )


class AmbiguousRouteError(RoutingError):
    """Raised when multiple agents match with equal priority."""

    def __init__(self, task_id: str, agents: list[str]) -> None:
        super().__init__(
            ORCH_2005_AMBIGUOUS_ROUTE,
            f"Ambiguous route for task '{task_id}': "
            f"multiple agents matched: {agents}",
        )


# Statuses that indicate an agent is available to receive tasks
_AVAILABLE_STATUSES = frozenset({AgentStatus.ONLINE, AgentStatus.DEGRADED})


@dataclass
class ClarificationContext:
    """Describes an ambiguous routing situation that requires user clarification.

    Returned by TaskRouter._detect_ambiguity() when two or more domains
    match at the same rule priority.  The chat endpoint uses this to return
    a ClarificationResponse instead of attempting to route.

    Attributes:
        competing_domains: The domain values that tied (e.g. ["cmdb", "csa"]).
        question: The question to present to the user.
        options: Clickable option strings the user can choose from.
    """

    competing_domains: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)


class RoutingDecision:
    """Result of a routing decision.

    Captures the selected agent, the reason for the selection, and
    metadata about the routing process for audit logging.

    Attributes:
        agent: The selected agent registration.
        reason: Human-readable explanation of why this agent was chosen.
        method: The routing method used (domain, capability, explicit, fallback).
        candidates_evaluated: Number of agents considered during routing.
        timestamp: When the routing decision was made.
    """

    def __init__(
        self,
        agent: AgentRegistration,
        reason: str,
        method: str,
        candidates_evaluated: int,
    ) -> None:
        self.agent = agent
        self.reason = reason
        self.method = method
        self.candidates_evaluated = candidates_evaluated
        self.timestamp = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "agent_id": self.agent.agent_id,
            "agent_name": self.agent.name,
            "domain": self.agent.domain.value,
            "reason": self.reason,
            "method": self.method,
            "candidates_evaluated": self.candidates_evaluated,
            "timestamp": self.timestamp.isoformat(),
        }


class RoutingRule:
    """A configurable routing rule that maps keywords/patterns to agents.

    Rules are evaluated in priority order. The first matching rule determines
    the routing destination.

    Attributes:
        name: Human-readable rule name.
        priority: Lower numbers are evaluated first.
        domain: Target domain for matching tasks.
        keywords: Keywords in task title/description that trigger this rule.
        target_agent: Explicit agent ID to route to when this rule matches.
        capability: Required capability name for matching.
    """

    def __init__(
        self,
        name: str,
        priority: int = 100,
        domain: AgentDomain | None = None,
        keywords: list[str] | None = None,
        target_agent: str | None = None,
        capability: str | None = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.domain = domain
        self.keywords = keywords or []
        self.target_agent = target_agent
        self.capability = capability

    def matches(self, task: Task) -> bool:
        """Check if this rule matches the given task.

        A rule matches if ANY of its criteria match the task:
        - domain matches task.domain
        - any keyword appears in task.title or task.description
        - capability requirement (evaluated separately during routing)

        Args:
            task: The task to evaluate.

        Returns:
            True if the rule matches.
        """
        # Domain match
        if self.domain and task.domain and task.domain == self.domain:
            return True

        # Keyword match in title or description
        if self.keywords:
            text = f"{task.title} {task.description}".lower()
            for keyword in self.keywords:
                if keyword.lower() in text:
                    return True

        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "priority": self.priority,
            "domain": self.domain.value if self.domain else None,
            "keywords": self.keywords,
            "target_agent": self.target_agent,
            "capability": self.capability,
        }


def _build_default_routing_rules() -> list[RoutingRule]:
    """Build the default set of routing rules for ITOM domains.

    Returns:
        List of RoutingRule objects covering standard ITOM routing patterns.
    """
    return [
        # Higher-priority CMDB rule for CI-specific compliance checks.
        # "compliance check" in a CI/database/server context is a CMDB health
        # operation, distinct from governance compliance reports (audit-domain).
        # Priority 5 < 10 ensures cmdb wins and no ambiguity is triggered.
        RoutingRule(
            name="cmdb-ci-compliance",
            priority=5,
            domain=AgentDomain.CMDB,
            keywords=[
                "compliance check on",
                "check compliance for",
                "check compliance of",
            ],
        ),
        RoutingRule(
            name="cmdb-domain",
            priority=10,
            domain=AgentDomain.CMDB,
            keywords=[
                "cmdb", "configuration item", "ci ", "relationship",
                "server", "database", "application",
                "infrastructure", "duplicate", "stale", "health",
                "dashboard", "metrics", "operational",
                "impact", "dependency", "dependencies",
                "ire", "reconcile", "remediate", "history of",
                "change history", "get history", "ci history",
                "ci type", "ci class", "data quality",
                "eol", "end of life", "lifecycle", "criticality", "production",
                "missing serial", "without serial", "missing owner",
            ],
        ),
        RoutingRule(
            name="discovery-domain",
            priority=10,
            domain=AgentDomain.DISCOVERY,
            keywords=["discovery", "scan", "discover", "ip range"],
        ),
        RoutingRule(
            name="asset-domain",
            priority=10,
            domain=AgentDomain.ASSET,
            keywords=[
                "asset", "asset inventory", "asset management",
                "hardware asset", "hardware inventory", "hardware list",
                "software asset", "software inventory",
                "license inventory", "license management", "license compliance",
            ],
        ),
        # CSA handles service catalog, request creation, and workflow diagrams.
        # Priority 9 (higher than asset at 10) so "create/submit/open + request"
        # beats the bare "hardware" keyword in asset-domain.
        RoutingRule(
            name="csa-domain",
            priority=9,
            domain=AgentDomain.CSA,
            keywords=[
                # Catalog / request creation (bare "request" is safe since
                # asset-domain no longer has bare "hardware" to conflict)
                "request", "service catalog", "catalog item", "catalog request",
                "create a request", "create request", "new request",
                "submit a request", "submit request", "open a request", "open request",
                "raise a request", "raise request", "service request",
                "catalog", "remediation",
                # Workflow
                "workflow", "fulfillment workflow", "approval workflow",
                "request approval", "approval process",
                # Diagrams
                "flowchart", "flow chart", "pipeline flow", "request flow",
                "workflow diagram", "process diagram", "show me how",
                "how does the", "explain the process",
            ],
        ),
        RoutingRule(
            name="audit-domain",
            priority=10,
            domain=AgentDomain.AUDIT,
            keywords=["audit", "compliance", "drift", "policy"],
        ),
        RoutingRule(
            name="documentation-domain",
            priority=10,
            domain=AgentDomain.DOCUMENTATION,
            keywords=["document", "runbook", "knowledge base", "architecture diagram"],
        ),
        # Fallback: route generic search/query messages to CMDB as the
        # default data-lookup agent in the ITOM suite.
        RoutingRule(
            name="cmdb-search-fallback",
            priority=50,
            domain=AgentDomain.CMDB,
            keywords=[
                "search", "find", "look up", "query", "show me", "list", "count", "how many",
                # conversational follow-ups that imply data lookup
                "which ones", "which of", "filter", "filter to", "sort by", "group by",
                "only show", "just show", "now show", "also show",
                "missing", "without", "no owner", "no serial", "no os",
                "production only", "dev only", "staging only",
                "more details", "tell me more", "what about",
            ],
        ),
    ]


class TaskRouter:
    """Routes tasks to the appropriate ITOM agent.

    The router evaluates tasks against multiple criteria in this order:
    1. Explicit agent targeting (task.target_agent)
    2. Configurable routing rules (keyword and domain matching)
    3. Domain-based routing (task.domain -> agent.domain)
    4. Capability-based routing (match required capability)

    The first method that produces a valid, available agent wins.

    Args:
        registry: The AgentRegistry containing agent registrations.
        rules: Optional list of custom routing rules. If None, uses defaults.
        require_available: If True (default), only route to agents with
            status ONLINE or DEGRADED. If False, route to any registered agent.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        rules: list[RoutingRule] | None = None,
        require_available: bool = True,
    ) -> None:
        self._registry = registry
        self._rules = sorted(
            rules or _build_default_routing_rules(),
            key=lambda r: r.priority,
        )
        self._require_available = require_available
        self._routing_history: list[dict[str, Any]] = []

    def detect_ambiguity(self, task: Task) -> "ClarificationContext | None":
        """Check whether a task is ambiguous (two domains tie at same priority).

        Evaluates all routing rules against the task and collects the set of
        distinct domains that have at least one matching rule.  If two or
        more domains match at the *same* minimum priority value, the query
        is considered ambiguous.

        No-ops when the task has an explicit target_agent set (the caller
        already knows the destination).

        Args:
            task: The incoming task.

        Returns:
            ClarificationContext if ambiguous, None otherwise.
        """
        if task.target_agent:
            return None

        from itom_orchestrator.routing_config import CLARIFICATION_TEMPLATES

        # Collect (priority, domain) pairs for all matching rules
        matched: list[tuple[int, str]] = []
        for rule in self._rules:
            if rule.matches(task) and rule.domain:
                matched.append((rule.priority, rule.domain.value))

        if len(matched) < 2:
            return None

        # Find minimum priority (highest precedence)
        min_priority = min(p for p, _ in matched)

        # Collect distinct domains at that priority
        top_domains = list({d for p, d in matched if p == min_priority})

        if len(top_domains) < 2:
            return None

        # Look up clarification template
        domain_pair = frozenset(top_domains[:2])
        template = CLARIFICATION_TEMPLATES.get(domain_pair) or CLARIFICATION_TEMPLATES.get(None)
        if template is None:
            return None

        logger.info(
            "Ambiguous routing detected",
            extra={
                "extra_data": {
                    "task_id": task.task_id,
                    "competing_domains": top_domains,
                }
            },
        )

        return ClarificationContext(
            competing_domains=top_domains,
            question=str(template["question"]),
            options=list(template["options"]),  # type: ignore[arg-type]
        )

    def route(self, task: Task) -> RoutingDecision:
        """Route a task to the most appropriate agent.

        Evaluates routing criteria in priority order:
        1. Explicit targeting (task.target_agent)
        2. Routing rules (keywords + domain matching)
        3. Domain routing (task.domain -> agent.domain)
        4. Capability routing (if task parameters specify a capability)

        Args:
            task: The task to route.

        Returns:
            RoutingDecision with the selected agent and routing metadata.

        Raises:
            NoRouteFoundError: If no agent can handle the task.
            AgentUnavailableError: If the target agent is offline.
            AmbiguousRouteError: If multiple agents match equally.
        """
        logger.info(
            "Routing task",
            extra={
                "extra_data": {
                    "task_id": task.task_id,
                    "domain": task.domain.value if task.domain else None,
                    "target_agent": task.target_agent,
                    "priority": task.priority.value,
                }
            },
        )

        # 1. Explicit agent targeting
        if task.target_agent:
            decision = self._route_explicit(task)
            self._record_routing(task, decision)
            return decision

        # 2. Routing rules
        decision = self._route_by_rules(task)
        if decision:
            self._record_routing(task, decision)
            return decision

        # 3. Domain-based routing
        if task.domain:
            decision = self._route_by_domain(task)
            if decision:
                self._record_routing(task, decision)
                return decision

        # 4. Capability-based routing
        required_capability = task.parameters.get("required_capability")
        if required_capability:
            decision = self._route_by_capability(task, required_capability)
            if decision:
                self._record_routing(task, decision)
                return decision

        # 5. Session-continuity fallback: if the message has no routing signals
        # but the session has a previously successful agent, re-use it.
        last_agent_id = task.parameters.get("context", {}).get("last_agent_id")
        if last_agent_id:
            try:
                agent = self._registry.get(last_agent_id)
                if not self._require_available or agent.status in _AVAILABLE_STATUSES:
                    decision = RoutingDecision(
                        agent=agent,
                        reason=f"Session continuity: re-routing to last agent '{last_agent_id}' from session context.",
                        method="session",
                        candidates_evaluated=1,
                    )
                    self._record_routing(task, decision)
                    return decision
            except AgentNotFoundError:
                pass  # Agent gone, fall through to NoRouteFoundError

        # No route found
        raise NoRouteFoundError(
            task.task_id,
            f"No matching agent for domain={task.domain}, "
            f"target_agent={task.target_agent}, "
            f"keywords in title/description did not match any routing rule.",
        )

    def _route_explicit(self, task: Task) -> RoutingDecision:
        """Route to an explicitly targeted agent.

        Args:
            task: The task with target_agent set.

        Returns:
            RoutingDecision for the targeted agent.

        Raises:
            NoRouteFoundError: If the targeted agent does not exist.
            AgentUnavailableError: If the targeted agent is not available.
        """
        assert task.target_agent is not None

        try:
            agent = self._registry.get(task.target_agent)
        except AgentNotFoundError:
            raise NoRouteFoundError(
                task.task_id,
                f"Explicitly targeted agent '{task.target_agent}' not found in registry.",
            )

        if self._require_available and agent.status not in _AVAILABLE_STATUSES:
            raise AgentUnavailableError(task.target_agent, agent.status.value)

        return RoutingDecision(
            agent=agent,
            reason=f"Explicitly targeted agent '{task.target_agent}'.",
            method="explicit",
            candidates_evaluated=1,
        )

    def _route_by_rules(self, task: Task) -> RoutingDecision | None:
        """Route using configurable routing rules.

        Evaluates rules in priority order. The first matching rule with
        an available target agent wins.

        Args:
            task: The task to route.

        Returns:
            RoutingDecision if a rule matches, None otherwise.
        """
        for rule in self._rules:
            if not rule.matches(task):
                continue

            # Rule matched -- find the target agent
            if rule.target_agent:
                try:
                    agent = self._registry.get(rule.target_agent)
                    if self._require_available and agent.status not in _AVAILABLE_STATUSES:
                        continue  # Agent unavailable, try next rule
                    return RoutingDecision(
                        agent=agent,
                        reason=f"Routing rule '{rule.name}' matched -> agent '{rule.target_agent}'.",
                        method="rule",
                        candidates_evaluated=1,
                    )
                except AgentNotFoundError:
                    continue  # Agent not in registry, try next rule

            # Rule matched by domain -- find agents in that domain
            if rule.domain:
                candidates = self._registry.search_by_domain(rule.domain)
                available = self._filter_available(candidates)
                if len(available) == 1:
                    return RoutingDecision(
                        agent=available[0],
                        reason=(
                            f"Routing rule '{rule.name}' matched domain "
                            f"'{rule.domain.value}' -> agent '{available[0].agent_id}'."
                        ),
                        method="rule",
                        candidates_evaluated=len(candidates),
                    )
                if len(available) > 1:
                    # Multiple agents -- pick the first (sorted by agent_id)
                    return RoutingDecision(
                        agent=available[0],
                        reason=(
                            f"Routing rule '{rule.name}' matched domain "
                            f"'{rule.domain.value}'. Selected '{available[0].agent_id}' "
                            f"from {len(available)} candidates (first by agent_id)."
                        ),
                        method="rule",
                        candidates_evaluated=len(candidates),
                    )

            # Rule matched by capability
            if rule.capability:
                candidates = self._registry.search_by_capability(rule.capability)
                available = self._filter_available(candidates)
                if available:
                    return RoutingDecision(
                        agent=available[0],
                        reason=(
                            f"Routing rule '{rule.name}' matched capability "
                            f"'{rule.capability}' -> agent '{available[0].agent_id}'."
                        ),
                        method="rule",
                        candidates_evaluated=len(candidates),
                    )

        return None

    def _route_by_domain(self, task: Task) -> RoutingDecision | None:
        """Route by matching task domain to agent domain.

        Args:
            task: The task with a domain set.

        Returns:
            RoutingDecision if a domain-matching agent is found, None otherwise.
        """
        assert task.domain is not None

        candidates = self._registry.search_by_domain(task.domain)
        if not candidates:
            return None

        available = self._filter_available(candidates)
        if not available:
            return None

        if len(available) == 1:
            return RoutingDecision(
                agent=available[0],
                reason=f"Domain routing: task domain '{task.domain.value}' matched agent '{available[0].agent_id}'.",
                method="domain",
                candidates_evaluated=len(candidates),
            )

        # Multiple agents in the same domain -- pick first by agent_id
        return RoutingDecision(
            agent=available[0],
            reason=(
                f"Domain routing: task domain '{task.domain.value}' matched "
                f"{len(available)} agents. Selected '{available[0].agent_id}' (first by agent_id)."
            ),
            method="domain",
            candidates_evaluated=len(candidates),
        )

    def _route_by_capability(
        self, task: Task, capability: str
    ) -> RoutingDecision | None:
        """Route by matching a required capability to agent capabilities.

        Args:
            task: The task being routed.
            capability: The required capability name.

        Returns:
            RoutingDecision if a capability-matching agent is found, None otherwise.
        """
        candidates = self._registry.search_by_capability(capability)
        if not candidates:
            return None

        available = self._filter_available(candidates)
        if not available:
            return None

        return RoutingDecision(
            agent=available[0],
            reason=(
                f"Capability routing: required capability '{capability}' "
                f"matched agent '{available[0].agent_id}'."
            ),
            method="capability",
            candidates_evaluated=len(candidates),
        )

    def _filter_available(
        self, agents: list[AgentRegistration]
    ) -> list[AgentRegistration]:
        """Filter agents to only those with available status.

        If require_available is False, returns all agents unchanged.

        Args:
            agents: List of agents to filter.

        Returns:
            Filtered list of available agents.
        """
        if not self._require_available:
            return agents
        return [a for a in agents if a.status in _AVAILABLE_STATUSES]

    def _record_routing(self, task: Task, decision: RoutingDecision) -> None:
        """Record a routing decision in the history.

        Args:
            task: The routed task.
            decision: The routing decision made.
        """
        record = {
            "task_id": task.task_id,
            "agent_id": decision.agent.agent_id,
            "method": decision.method,
            "reason": decision.reason,
            "timestamp": decision.timestamp.isoformat(),
        }
        self._routing_history.append(record)

        logger.info(
            "Task routed",
            extra={"extra_data": record},
        )

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule and re-sort by priority.

        Args:
            rule: The routing rule to add.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)
        logger.info(
            "Routing rule added",
            extra={
                "extra_data": {
                    "name": rule.name,
                    "priority": rule.priority,
                    "total_rules": len(self._rules),
                }
            },
        )

    def remove_rule(self, name: str) -> bool:
        """Remove a routing rule by name.

        Args:
            name: The name of the rule to remove.

        Returns:
            True if the rule was found and removed, False otherwise.
        """
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        removed = len(self._rules) < before
        if removed:
            logger.info(
                "Routing rule removed",
                extra={"extra_data": {"name": name}},
            )
        return removed

    def get_rules(self) -> list[dict[str, Any]]:
        """Return all routing rules as serialized dictionaries.

        Returns:
            List of rule dictionaries sorted by priority.
        """
        return [r.to_dict() for r in self._rules]

    def get_routing_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent routing decisions.

        Args:
            limit: Maximum number of records to return (most recent first).

        Returns:
            List of routing decision records, newest first.
        """
        recent = self._routing_history[-limit:] if limit < len(self._routing_history) else self._routing_history
        return list(reversed(recent))

    @property
    def rule_count(self) -> int:
        """Return the number of configured routing rules."""
        return len(self._rules)


class RoutingRulesLoader:
    """Loader for routing rules from JSON configuration files.

    Supports loading, validating, caching, and hot-reloading of routing
    configuration. Validates configuration against schema and enforces
    consistency across domains, capabilities, and rules.

    Attributes:
        config_path: Path to routing-rules.json file.
        validate_on_load: If True, validate config against schema on load.
        cache_config: If True, cache loaded config in memory.
        enable_hot_reload: If True, watch file for changes and reload.
    """

    def __init__(
        self,
        config_path: str,
        validate_on_load: bool = True,
        cache_config: bool = True,
        enable_hot_reload: bool = True,
    ) -> None:
        """Initialize the RoutingRulesLoader.

        Args:
            config_path: Path to routing-rules.json file.
            validate_on_load: Whether to validate on initial load.
            cache_config: Whether to cache loaded config.
            enable_hot_reload: Whether to enable hot-reload watching.
        """
        self.config_path = config_path
        self.validate_on_load = validate_on_load
        self.cache_config = cache_config
        self.enable_hot_reload = enable_hot_reload
        self._cached_config: dict[str, Any] | None = None
        self._last_modified: float | None = None
        self._validation_errors: list[str] = []

    def load(self) -> dict[str, Any]:
        """Load routing rules configuration from file.

        Returns:
            Dictionary containing the routing rules configuration.

        Raises:
            FileNotFoundError: If config file does not exist.
            ValueError: If config is invalid and validate_on_load is True.
        """
        import json
        from pathlib import Path

        path = Path(self.config_path)
        if not path.exists():
            raise FileNotFoundError(f"Routing rules config not found: {self.config_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in routing rules config: {e}")

        if self.validate_on_load:
            errors = self.validate(config)
            if errors:
                self._validation_errors = errors
                raise ValueError(f"Routing rules config validation failed: {errors}")

        if self.cache_config:
            self._cached_config = config
            self._last_modified = path.stat().st_mtime

        logger.info(
            "Loaded routing rules configuration",
            extra={
                "extra_data": {
                    "config_path": self.config_path,
                    "domains": len(config.get("domains", {})),
                    "rules": len(config.get("routing_rules", [])),
                    "capabilities": len(config.get("capability_mappings", {})),
                }
            },
        )

        return config

    def validate(self, config: dict[str, Any]) -> list[str]:
        """Validate routing rules configuration against schema.

        Args:
            config: The routing rules configuration to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        # Check required top-level fields
        required_fields = ["version", "domains", "routing_rules", "capability_mappings"]
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")

        if errors:
            self._validation_errors = errors
            return errors

        # Validate domains
        domains = config.get("domains", {})
        for domain_id, domain_config in domains.items():
            if "id" not in domain_config:
                errors.append(f"Domain '{domain_id}' missing 'id' field")
            if "name" not in domain_config:
                errors.append(f"Domain '{domain_id}' missing 'name' field")
            if "keywords" not in domain_config or not isinstance(domain_config["keywords"], list):
                errors.append(f"Domain '{domain_id}' missing or invalid 'keywords' field")

        # Validate routing rules
        rules = config.get("routing_rules", [])
        for rule in rules:
            if "id" not in rule:
                errors.append("Routing rule missing 'id' field")
            if "name" not in rule:
                errors.append("Routing rule missing 'name' field")
            if "priority" not in rule or not isinstance(rule["priority"], int):
                errors.append(f"Routing rule '{rule.get('id')}' has invalid 'priority'")

            # Validate target agent if specified
            if "target_agent" in rule and rule["target_agent"]:
                # Target agent should be one of the known agents
                valid_agents = ["cmdb-agent", "discovery-agent", "asset-agent", "csa-agent", "itom-auditor", "itom-documentator"]
                if rule["target_agent"] not in valid_agents:
                    logger.warning(
                        f"Routing rule '{rule.get('id')}' targets unknown agent: {rule['target_agent']}"
                    )

        # Validate capability mappings
        capabilities = config.get("capability_mappings", {})
        for cap_name, cap_config in capabilities.items():
            if "domain" not in cap_config:
                errors.append(f"Capability '{cap_name}' missing 'domain' field")
            if "agents" not in cap_config or not isinstance(cap_config["agents"], list):
                errors.append(f"Capability '{cap_name}' missing or invalid 'agents' field")

        # Check domain consistency: verify domains in rules and capabilities exist
        for rule in rules:
            if "domain" in rule and rule["domain"]:
                if rule["domain"] not in domains:
                    errors.append(f"Routing rule '{rule.get('id')}' references undefined domain: {rule['domain']}")

        for cap_name, cap_config in capabilities.items():
            domain = cap_config.get("domain")
            if domain and domain not in domains:
                errors.append(f"Capability '{cap_name}' references undefined domain: {domain}")

        self._validation_errors = errors
        return errors

    def needs_reload(self) -> bool:
        """Check if config file has been modified since last load.

        Returns:
            True if file modification time is newer than cached timestamp.
        """
        if not self.enable_hot_reload or self._last_modified is None:
            return False

        from pathlib import Path

        path = Path(self.config_path)
        if not path.exists():
            return False

        return path.stat().st_mtime > self._last_modified

    def get_cached_config(self) -> dict[str, Any] | None:
        """Return cached configuration if available.

        Returns:
            Cached configuration dict, or None if not cached.
        """
        return self._cached_config

    def clear_cache(self) -> None:
        """Clear the cached configuration."""
        self._cached_config = None
        self._last_modified = None
        self._validation_errors = []

    @property
    def validation_errors(self) -> list[str]:
        """Return list of validation errors from last validation."""
        return self._validation_errors.copy()
