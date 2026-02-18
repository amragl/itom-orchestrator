"""
Tests for routing configuration management (ORCH-010).
"""

import json
from pathlib import Path

import pytest

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.routing_config import (
    RoutingConfig,
    RoutingRuleConfig,
    load_routing_config,
    validate_routing_config,
)


class TestRoutingRuleConfig:
    """Tests for the RoutingRuleConfig model."""

    def test_create_valid_rule(self):
        rule = RoutingRuleConfig(
            rule_id="test-rule",
            name="Test Rule",
            priority=10,
            domain=AgentDomain.CMDB,
            keywords=["cmdb", "ci"],
        )
        assert rule.rule_id == "test-rule"
        assert rule.priority == 10
        assert rule.domain == AgentDomain.CMDB
        assert rule.enabled is True

    def test_empty_rule_id_rejected(self):
        with pytest.raises(ValueError, match="rule_id must not be empty"):
            RoutingRuleConfig(rule_id="  ", name="Test")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            RoutingRuleConfig(rule_id="test", name="  ")

    def test_negative_priority_rejected(self):
        with pytest.raises(ValueError, match="priority must be >= 0"):
            RoutingRuleConfig(rule_id="test", name="Test", priority=-1)

    def test_defaults(self):
        rule = RoutingRuleConfig(rule_id="test", name="Test")
        assert rule.priority == 100
        assert rule.domain is None
        assert rule.keywords == []
        assert rule.target_agent is None
        assert rule.capability is None
        assert rule.enabled is True


class TestRoutingConfig:
    """Tests for the RoutingConfig model."""

    def test_create_valid_config(self):
        config = RoutingConfig(
            version="1.0.0",
            rules=[
                RoutingRuleConfig(
                    rule_id="r1",
                    name="Rule 1",
                    domain=AgentDomain.CMDB,
                    keywords=["cmdb"],
                )
            ],
        )
        assert config.version == "1.0.0"
        assert len(config.rules) == 1

    def test_empty_config(self):
        config = RoutingConfig()
        assert config.version == "1.0.0"
        assert config.rules == []
        assert config.default_domain is None

    def test_empty_version_rejected(self):
        with pytest.raises(ValueError, match="version must not be empty"):
            RoutingConfig(version="  ")


class TestLoadRoutingConfig:
    """Tests for load_routing_config function."""

    def test_load_valid_file(self, tmp_path):
        config_data = {
            "version": "2.0.0",
            "rules": [
                {
                    "rule_id": "r1",
                    "name": "Test Rule",
                    "priority": 10,
                    "domain": "cmdb",
                    "keywords": ["cmdb"],
                }
            ],
        }
        config_file = tmp_path / "routing.json"
        config_file.write_text(json.dumps(config_data))

        config = load_routing_config(config_file)
        assert config.version == "2.0.0"
        assert len(config.rules) == 1
        assert config.rules[0].rule_id == "r1"

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_routing_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path):
        config_file = tmp_path / "bad.json"
        config_file.write_text("{ not json }")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_routing_config(config_file)


class TestValidateRoutingConfig:
    """Tests for validate_routing_config function."""

    def test_valid_config_no_errors(self):
        config = RoutingConfig(
            rules=[
                RoutingRuleConfig(
                    rule_id="r1",
                    name="Rule 1",
                    domain=AgentDomain.CMDB,
                    keywords=["cmdb"],
                )
            ]
        )
        errors = validate_routing_config(config)
        assert errors == []

    def test_duplicate_rule_ids(self):
        config = RoutingConfig(
            rules=[
                RoutingRuleConfig(rule_id="r1", name="Rule 1", domain=AgentDomain.CMDB),
                RoutingRuleConfig(rule_id="r1", name="Rule 2", domain=AgentDomain.ASSET),
            ]
        )
        errors = validate_routing_config(config)
        assert any("Duplicate rule_id" in e for e in errors)

    def test_rule_with_no_criteria(self):
        config = RoutingConfig(
            rules=[
                RoutingRuleConfig(rule_id="r1", name="Rule 1"),
            ]
        )
        errors = validate_routing_config(config)
        assert any("no matching criteria" in e for e in errors)

    def test_all_rules_disabled(self):
        config = RoutingConfig(
            rules=[
                RoutingRuleConfig(
                    rule_id="r1",
                    name="Rule 1",
                    domain=AgentDomain.CMDB,
                    enabled=False,
                ),
            ]
        )
        errors = validate_routing_config(config)
        assert any("disabled" in e for e in errors)

    def test_empty_rules_no_disabled_warning(self):
        config = RoutingConfig(rules=[])
        errors = validate_routing_config(config)
        assert errors == []
