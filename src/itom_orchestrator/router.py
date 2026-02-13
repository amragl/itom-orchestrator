"""
Task Router for the ITOM Orchestrator.

Implements domain-based routing, capability matching, agent availability
checks, explicit agent targeting, and configurable routing rules. Routes
incoming tasks to the most appropriate agent based on task attributes and
agent capabilities.

This module implements ORCH-008: Task Router -- domain-based routing engine.
"""

import logging
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
        RoutingRule(
            name="cmdb-domain",
            priority=10,
            domain=AgentDomain.CMDB,
            keywords=["cmdb", "configuration item", "ci ", "relationship"],
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
            keywords=["asset", "inventory", "license", "hardware", "software asset"],
        ),
        RoutingRule(
            name="csa-domain",
            priority=10,
            domain=AgentDomain.CSA,
            keywords=["catalog", "workflow", "request", "remediation", "service catalog"],
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
