"""
Tests for role boundary enforcement (ORCH-018, ORCH-020).
"""

import json
from pathlib import Path

import pytest

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.role_enforcer import (
    Permission,
    RoleEnforcer,
    RolePolicy,
    get_default_enforcer,
    load_role_config,
    save_role_config,
    validate_role_config,
)


class TestRolePolicy:
    """Tests for the RolePolicy model."""

    def test_create_valid_policy(self):
        policy = RolePolicy(
            role_id="test-role",
            name="Test Role",
            description="A test role",
            allowed_domains=[AgentDomain.CMDB],
            allowed_actions=["cmdb.query"],
            permissions=[Permission.READ],
        )
        assert policy.role_id == "test-role"
        assert len(policy.allowed_domains) == 1
        assert len(policy.permissions) == 1

    def test_empty_role_id_rejected(self):
        with pytest.raises(ValueError, match="role_id must not be empty"):
            RolePolicy(role_id="  ", name="Test", description="test")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            RolePolicy(role_id="test", name="  ", description="test")


class TestRoleEnforcer:
    """Tests for the RoleEnforcer."""

    def test_default_policies_loaded(self):
        enforcer = get_default_enforcer()
        assert enforcer.policy_count == 6

    def test_orchestrator_has_admin(self):
        enforcer = get_default_enforcer()
        assert enforcer.check_permission("orchestrator", "any.action") is True
        assert enforcer.check_permission("orchestrator", "anything", AgentDomain.CMDB) is True

    def test_cmdb_agent_allowed_cmdb_actions(self):
        enforcer = get_default_enforcer()
        assert enforcer.check_permission("cmdb-agent", "cmdb.query", AgentDomain.CMDB) is True
        assert enforcer.check_permission("cmdb-agent", "cmdb.update", AgentDomain.CMDB) is True

    def test_cmdb_agent_denied_discovery_domain(self):
        enforcer = get_default_enforcer()
        assert enforcer.check_permission("cmdb-agent", "cmdb.query", AgentDomain.DISCOVERY) is False

    def test_unknown_role_returns_false(self):
        enforcer = get_default_enforcer()
        assert enforcer.check_permission("nonexistent-role", "some.action") is False

    def test_auditor_read_only_all_domains(self):
        enforcer = get_default_enforcer()
        # Auditor should have read access on all domains
        assert enforcer.check_permission("itom-auditor", "audit.compliance", AgentDomain.CMDB) is True
        assert enforcer.check_permission("itom-auditor", "audit.compliance", AgentDomain.DISCOVERY) is True

    def test_documentator_write_on_documentation(self):
        enforcer = get_default_enforcer()
        assert enforcer.check_permission(
            "itom-documentator", "documentation.generate", AgentDomain.DOCUMENTATION
        ) is True

    def test_get_allowed_domains(self):
        enforcer = get_default_enforcer()

        cmdb_domains = enforcer.get_allowed_domains("cmdb-agent")
        assert cmdb_domains == [AgentDomain.CMDB]

        orchestrator_domains = enforcer.get_allowed_domains("orchestrator")
        assert len(orchestrator_domains) == len(list(AgentDomain))

    def test_get_allowed_domains_unknown_role(self):
        enforcer = get_default_enforcer()
        assert enforcer.get_allowed_domains("nonexistent") == []

    def test_get_policy(self):
        enforcer = get_default_enforcer()
        policy = enforcer.get_policy("cmdb-agent")
        assert policy is not None
        assert policy.role_id == "cmdb-agent"

    def test_get_policy_missing(self):
        enforcer = get_default_enforcer()
        assert enforcer.get_policy("nonexistent") is None

    def test_add_policy(self):
        enforcer = RoleEnforcer(policies=[])
        policy = RolePolicy(
            role_id="custom",
            name="Custom Role",
            description="A custom role",
            allowed_domains=[AgentDomain.CMDB],
            allowed_actions=["custom.action"],
            permissions=[Permission.READ],
        )
        enforcer.add_policy(policy)

        assert enforcer.policy_count == 1
        assert enforcer.check_permission("custom", "custom.action") is True

    def test_list_policies(self):
        enforcer = get_default_enforcer()
        policies = enforcer.list_policies()
        assert len(policies) == 6
        # Should be sorted by role_id
        role_ids = [p.role_id for p in policies]
        assert role_ids == sorted(role_ids)

    def test_empty_enforcer(self):
        enforcer = RoleEnforcer(policies=[])
        assert enforcer.policy_count == 0
        assert enforcer.check_permission("anything", "any.action") is False


class TestRoleConfigPersistence:
    """Tests for role config load/save (ORCH-020)."""

    def test_save_and_load(self, tmp_path):
        policies = [
            RolePolicy(
                role_id="test-role",
                name="Test",
                description="test",
                allowed_domains=[AgentDomain.CMDB],
                permissions=[Permission.READ],
            )
        ]

        config_path = tmp_path / "roles.json"
        save_role_config(policies, config_path)
        assert config_path.exists()

        loaded = load_role_config(config_path)
        assert len(loaded) == 1
        assert loaded[0].role_id == "test-role"

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_role_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_role_config(bad_file)


class TestValidateRoleConfig:
    """Tests for role config validation."""

    def test_valid_config(self):
        policies = [
            RolePolicy(
                role_id="r1",
                name="Role 1",
                description="test",
                allowed_domains=[AgentDomain.CMDB],
                permissions=[Permission.READ],
            )
        ]
        errors = validate_role_config(policies)
        assert errors == []

    def test_duplicate_role_ids(self):
        policies = [
            RolePolicy(role_id="r1", name="Role 1", description="test",
                        allowed_domains=[AgentDomain.CMDB], permissions=[Permission.READ]),
            RolePolicy(role_id="r1", name="Role 2", description="test",
                        allowed_domains=[AgentDomain.ASSET], permissions=[Permission.WRITE]),
        ]
        errors = validate_role_config(policies)
        assert any("Duplicate role_id" in e for e in errors)

    def test_policy_no_permissions(self):
        policies = [
            RolePolicy(
                role_id="r1",
                name="Role 1",
                description="test",
                allowed_domains=[AgentDomain.CMDB],
                permissions=[],
            )
        ]
        errors = validate_role_config(policies)
        assert any("no permissions" in e for e in errors)

    def test_policy_no_domains_or_actions(self):
        policies = [
            RolePolicy(
                role_id="r1",
                name="Role 1",
                description="test",
                permissions=[Permission.READ],
            )
        ]
        errors = validate_role_config(policies)
        assert any("no allowed domains or actions" in e for e in errors)
