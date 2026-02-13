"""
Tests for the Task Router (ORCH-008).

Validates that:
- Domain-based routing matches tasks to agents by domain
- Capability-based routing matches by required_capability parameter
- Explicit agent targeting bypasses routing logic
- Routing rules with keywords match task title/description
- Availability checks skip offline agents
- NoRouteFoundError raised when no agent matches
- AgentUnavailableError raised when target agent is offline
- Routing history is recorded
- Rules can be added and removed dynamically
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from itom_orchestrator.models.agents import AgentDomain, AgentStatus
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskStatus
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentRegistry
from itom_orchestrator.router import (
    AgentUnavailableError,
    NoRouteFoundError,
    RoutingDecision,
    RoutingRule,
    TaskRouter,
)


@pytest.fixture()
def persistence(tmp_data_dir: Path) -> StatePersistence:
    """Create a StatePersistence instance for tests."""
    return StatePersistence(state_dir=str(tmp_data_dir / "state"))


@pytest.fixture()
def registry(persistence: StatePersistence) -> AgentRegistry:
    """Create an AgentRegistry with default agents."""
    reg = AgentRegistry(persistence=persistence, load_defaults=True)
    reg.initialize()
    return reg


@pytest.fixture()
def registry_with_online_agents(registry: AgentRegistry) -> AgentRegistry:
    """Registry with all agents set to ONLINE status."""
    for agent in registry.list_all():
        registry.update_status(agent.agent_id, AgentStatus.ONLINE)
    return registry


@pytest.fixture()
def router(registry_with_online_agents: AgentRegistry) -> TaskRouter:
    """Create a TaskRouter with default rules and all agents online."""
    return TaskRouter(registry=registry_with_online_agents)


@pytest.fixture()
def router_no_availability(registry: AgentRegistry) -> TaskRouter:
    """Create a TaskRouter that does not require agent availability."""
    return TaskRouter(registry=registry, require_available=False)


def _make_task(
    task_id: str = "test-task-1",
    title: str = "Test task",
    description: str = "A test task",
    domain: AgentDomain | None = None,
    target_agent: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    parameters: dict | None = None,
) -> Task:
    """Helper to create a Task for testing."""
    return Task(
        task_id=task_id,
        title=title,
        description=description,
        domain=domain,
        target_agent=target_agent,
        priority=priority,
        status=TaskStatus.PENDING,
        parameters=parameters or {},
        created_at=datetime.now(UTC),
    )


class TestDomainRouting:
    """Tests for domain-based routing."""

    def test_route_cmdb_task_by_domain(self, router: TaskRouter) -> None:
        """Task with CMDB domain should route to cmdb-agent."""
        task = _make_task(domain=AgentDomain.CMDB)
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"
        assert decision.method == "rule"  # Rules match before domain

    def test_route_discovery_task_by_domain(self, router: TaskRouter) -> None:
        """Task with DISCOVERY domain should route to discovery-agent."""
        task = _make_task(domain=AgentDomain.DISCOVERY)
        decision = router.route(task)
        assert decision.agent.agent_id == "discovery-agent"

    def test_route_asset_task_by_domain(self, router: TaskRouter) -> None:
        """Task with ASSET domain should route to asset-agent."""
        task = _make_task(domain=AgentDomain.ASSET)
        decision = router.route(task)
        assert decision.agent.agent_id == "asset-agent"

    def test_route_csa_task_by_domain(self, router: TaskRouter) -> None:
        """Task with CSA domain should route to csa-agent."""
        task = _make_task(domain=AgentDomain.CSA)
        decision = router.route(task)
        assert decision.agent.agent_id == "csa-agent"

    def test_route_audit_task_by_domain(self, router: TaskRouter) -> None:
        """Task with AUDIT domain should route to itom-auditor."""
        task = _make_task(domain=AgentDomain.AUDIT)
        decision = router.route(task)
        assert decision.agent.agent_id == "itom-auditor"

    def test_route_documentation_task_by_domain(self, router: TaskRouter) -> None:
        """Task with DOCUMENTATION domain should route to itom-documentator."""
        task = _make_task(domain=AgentDomain.DOCUMENTATION)
        decision = router.route(task)
        assert decision.agent.agent_id == "itom-documentator"


class TestKeywordRouting:
    """Tests for keyword-based routing via rules."""

    def test_route_by_cmdb_keyword_in_title(self, router: TaskRouter) -> None:
        """Task with 'cmdb' in title should route to cmdb-agent."""
        task = _make_task(title="Query CMDB for servers")
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"

    def test_route_by_discovery_keyword_in_description(self, router: TaskRouter) -> None:
        """Task with 'discovery' in description should route to discovery-agent."""
        task = _make_task(
            title="Run scan",
            description="Run a discovery scan on the network",
        )
        decision = router.route(task)
        assert decision.agent.agent_id == "discovery-agent"

    def test_route_by_audit_keyword(self, router: TaskRouter) -> None:
        """Task with 'compliance' in title should route to itom-auditor."""
        task = _make_task(title="Run compliance check")
        decision = router.route(task)
        assert decision.agent.agent_id == "itom-auditor"

    def test_route_by_documentation_keyword(self, router: TaskRouter) -> None:
        """Task with 'runbook' in title should route to itom-documentator."""
        task = _make_task(title="Create a runbook for deployment")
        decision = router.route(task)
        assert decision.agent.agent_id == "itom-documentator"

    def test_keyword_match_is_case_insensitive(self, router: TaskRouter) -> None:
        """Keyword matching should be case-insensitive."""
        task = _make_task(title="Query CMDB Tables")
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"


class TestExplicitTargeting:
    """Tests for explicit agent targeting."""

    def test_explicit_target_bypasses_routing(self, router: TaskRouter) -> None:
        """task.target_agent should bypass all routing logic."""
        task = _make_task(
            domain=AgentDomain.CMDB,  # Would route to cmdb-agent
            target_agent="discovery-agent",  # But explicitly targets discovery
        )
        decision = router.route(task)
        assert decision.agent.agent_id == "discovery-agent"
        assert decision.method == "explicit"

    def test_explicit_target_nonexistent_agent_raises(self, router: TaskRouter) -> None:
        """Targeting a non-existent agent should raise NoRouteFoundError."""
        task = _make_task(target_agent="nonexistent-agent")
        with pytest.raises(NoRouteFoundError) as exc_info:
            router.route(task)
        assert "nonexistent-agent" in str(exc_info.value)

    def test_explicit_target_offline_agent_raises(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Targeting an offline agent should raise AgentUnavailableError."""
        registry_with_online_agents.update_status("cmdb-agent", AgentStatus.OFFLINE)
        router = TaskRouter(registry=registry_with_online_agents)
        task = _make_task(target_agent="cmdb-agent")
        with pytest.raises(AgentUnavailableError) as exc_info:
            router.route(task)
        assert "cmdb-agent" in str(exc_info.value)

    def test_explicit_target_degraded_agent_succeeds(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Targeting a DEGRADED agent should succeed (degraded = available)."""
        registry_with_online_agents.update_status("cmdb-agent", AgentStatus.DEGRADED)
        router = TaskRouter(registry=registry_with_online_agents)
        task = _make_task(target_agent="cmdb-agent")
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"


class TestCapabilityRouting:
    """Tests for capability-based routing."""

    def test_route_by_capability(self, router: TaskRouter) -> None:
        """Task with required_capability should find the agent with that capability."""
        task = _make_task(
            title="Check license compliance",
            description="Need to check licenses",
            parameters={"required_capability": "license_compliance_check"},
        )
        decision = router.route(task)
        assert decision.agent.agent_id == "asset-agent"
        assert decision.method in ("rule", "capability")

    def test_route_by_unique_capability(self, router: TaskRouter) -> None:
        """A capability unique to one agent should route to that agent."""
        task = _make_task(
            title="Generate docs",
            parameters={"required_capability": "generate_architecture_diagram"},
        )
        decision = router.route(task)
        assert decision.agent.agent_id == "itom-documentator"


class TestAvailabilityFiltering:
    """Tests for agent availability checks."""

    def test_offline_agent_skipped_in_domain_routing(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Offline agents should be skipped during domain routing."""
        registry_with_online_agents.update_status("cmdb-agent", AgentStatus.OFFLINE)
        router = TaskRouter(registry=registry_with_online_agents)
        task = _make_task(domain=AgentDomain.CMDB)
        # CMDB domain only has one agent (cmdb-agent), which is now offline
        with pytest.raises(NoRouteFoundError):
            router.route(task)

    def test_require_available_false_routes_to_offline(
        self, registry: AgentRegistry
    ) -> None:
        """With require_available=False, offline agents can be routed to."""
        router = TaskRouter(registry=registry, require_available=False)
        task = _make_task(domain=AgentDomain.CMDB)
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"

    def test_maintenance_agent_skipped(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Agents in MAINTENANCE status should be skipped."""
        registry_with_online_agents.update_status("cmdb-agent", AgentStatus.MAINTENANCE)
        router = TaskRouter(registry=registry_with_online_agents)
        task = _make_task(domain=AgentDomain.CMDB)
        with pytest.raises(NoRouteFoundError):
            router.route(task)


class TestNoRouteFound:
    """Tests for NoRouteFoundError scenarios."""

    def test_no_domain_no_keywords_no_target(self, router: TaskRouter) -> None:
        """Task with no routing hints should raise NoRouteFoundError."""
        task = _make_task(
            title="Do something generic",
            description="No routing hints here",
        )
        with pytest.raises(NoRouteFoundError):
            router.route(task)

    def test_unknown_domain_raises(self, router: TaskRouter) -> None:
        """Task with ORCHESTRATION domain (no agents) should raise."""
        task = _make_task(domain=AgentDomain.ORCHESTRATION)
        # Orchestration domain has no agents registered
        with pytest.raises(NoRouteFoundError):
            router.route(task)


class TestRoutingRules:
    """Tests for configurable routing rules."""

    def test_custom_rule_with_keyword(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Custom rules with keywords should match."""
        custom_rule = RoutingRule(
            name="ci-update-to-cmdb",
            priority=1,
            keywords=["update ci", "modify ci"],
            target_agent="cmdb-agent",
        )
        router = TaskRouter(
            registry=registry_with_online_agents,
            rules=[custom_rule],
        )
        task = _make_task(title="Update CI attributes for servers")
        decision = router.route(task)
        assert decision.agent.agent_id == "cmdb-agent"
        assert decision.method == "rule"

    def test_rules_sorted_by_priority(
        self, registry_with_online_agents: AgentRegistry
    ) -> None:
        """Rules should be evaluated in priority order (lower number first)."""
        low_priority = RoutingRule(
            name="low-priority",
            priority=100,
            keywords=["server"],
            target_agent="asset-agent",
        )
        high_priority = RoutingRule(
            name="high-priority",
            priority=1,
            keywords=["server"],
            target_agent="cmdb-agent",
        )
        router = TaskRouter(
            registry=registry_with_online_agents,
            rules=[low_priority, high_priority],
        )
        task = _make_task(title="Check server status")
        decision = router.route(task)
        # High priority rule should win
        assert decision.agent.agent_id == "cmdb-agent"

    def test_add_rule(self, router: TaskRouter) -> None:
        """add_rule() should insert and re-sort rules."""
        initial_count = router.rule_count
        router.add_rule(RoutingRule(name="new-rule", priority=5))
        assert router.rule_count == initial_count + 1

    def test_remove_rule(self, router: TaskRouter) -> None:
        """remove_rule() should remove the named rule."""
        initial_count = router.rule_count
        removed = router.remove_rule("cmdb-domain")
        assert removed is True
        assert router.rule_count == initial_count - 1

    def test_remove_nonexistent_rule(self, router: TaskRouter) -> None:
        """Removing a non-existent rule should return False."""
        removed = router.remove_rule("nonexistent-rule")
        assert removed is False

    def test_get_rules_returns_serialized(self, router: TaskRouter) -> None:
        """get_rules() should return serialized rule dictionaries."""
        rules = router.get_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0
        for rule in rules:
            assert "name" in rule
            assert "priority" in rule


class TestRoutingDecision:
    """Tests for RoutingDecision dataclass."""

    def test_to_dict(self, router: TaskRouter) -> None:
        """RoutingDecision.to_dict() should return all required fields."""
        task = _make_task(domain=AgentDomain.CMDB)
        decision = router.route(task)
        d = decision.to_dict()
        assert "agent_id" in d
        assert "agent_name" in d
        assert "domain" in d
        assert "reason" in d
        assert "method" in d
        assert "candidates_evaluated" in d
        assert "timestamp" in d


class TestRoutingHistory:
    """Tests for routing history tracking."""

    def test_routing_recorded_in_history(self, router: TaskRouter) -> None:
        """Each routing decision should be recorded in history."""
        task = _make_task(domain=AgentDomain.CMDB)
        router.route(task)
        history = router.get_routing_history()
        assert len(history) == 1
        assert history[0]["task_id"] == "test-task-1"

    def test_multiple_routings_in_history(self, router: TaskRouter) -> None:
        """Multiple routings should all appear in history."""
        for i in range(3):
            task = _make_task(
                task_id=f"task-{i}",
                domain=AgentDomain.CMDB,
            )
            router.route(task)
        history = router.get_routing_history()
        assert len(history) == 3

    def test_history_limited_by_parameter(self, router: TaskRouter) -> None:
        """get_routing_history(limit) should respect the limit."""
        for i in range(5):
            task = _make_task(task_id=f"task-{i}", domain=AgentDomain.CMDB)
            router.route(task)
        history = router.get_routing_history(limit=3)
        assert len(history) == 3

    def test_history_newest_first(self, router: TaskRouter) -> None:
        """History should be returned newest first."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}", domain=AgentDomain.CMDB)
            router.route(task)
        history = router.get_routing_history()
        assert history[0]["task_id"] == "task-2"
        assert history[-1]["task_id"] == "task-0"


class TestRoutingRuleModel:
    """Tests for the RoutingRule class."""

    def test_rule_matches_by_domain(self) -> None:
        """Rule should match when domain matches task domain."""
        rule = RoutingRule(name="cmdb", domain=AgentDomain.CMDB)
        task = _make_task(domain=AgentDomain.CMDB)
        assert rule.matches(task) is True

    def test_rule_does_not_match_different_domain(self) -> None:
        """Rule should not match when domains differ."""
        rule = RoutingRule(name="cmdb", domain=AgentDomain.CMDB)
        task = _make_task(domain=AgentDomain.DISCOVERY)
        assert rule.matches(task) is False

    def test_rule_matches_by_keyword(self) -> None:
        """Rule should match when keyword appears in task title."""
        rule = RoutingRule(name="cmdb", keywords=["cmdb"])
        task = _make_task(title="Query CMDB for CIs")
        assert rule.matches(task) is True

    def test_rule_no_match_without_criteria(self) -> None:
        """Rule with no criteria should not match any task."""
        rule = RoutingRule(name="empty")
        task = _make_task(title="Test task")
        assert rule.matches(task) is False

    def test_rule_to_dict(self) -> None:
        """to_dict() should serialize all fields."""
        rule = RoutingRule(
            name="test-rule",
            priority=5,
            domain=AgentDomain.CMDB,
            keywords=["cmdb"],
            target_agent="cmdb-agent",
        )
        d = rule.to_dict()
        assert d["name"] == "test-rule"
        assert d["priority"] == 5
        assert d["domain"] == "cmdb"
        assert d["keywords"] == ["cmdb"]
        assert d["target_agent"] == "cmdb-agent"
