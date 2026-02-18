"""
Tests for workflow template registry and instantiation (ORCH-011, ORCH-014).
"""

import pytest

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.workflows import WorkflowStep, WorkflowStepType
from itom_orchestrator.workflow_templates import (
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    get_default_registry,
)


class TestWorkflowTemplate:
    """Tests for the WorkflowTemplate model."""

    def test_create_valid_template(self):
        template = WorkflowTemplate(
            template_id="test-template",
            name="Test Template",
            description="A test workflow template",
            domain=AgentDomain.CMDB,
            steps=[
                WorkflowStep(
                    step_id="step-1",
                    name="Step 1",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CMDB,
                )
            ],
            tags=["test"],
        )
        assert template.template_id == "test-template"
        assert template.domain == AgentDomain.CMDB
        assert len(template.steps) == 1
        assert template.tags == ["test"]

    def test_empty_template_id_rejected(self):
        with pytest.raises(ValueError, match="template_id must not be empty"):
            WorkflowTemplate(
                template_id="  ",
                name="Test",
                description="test",
                domain=AgentDomain.CMDB,
                steps=[
                    WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB)
                ],
            )

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            WorkflowTemplate(
                template_id="test",
                name="  ",
                description="test",
                domain=AgentDomain.CMDB,
                steps=[
                    WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB)
                ],
            )


class TestWorkflowTemplateRegistry:
    """Tests for the WorkflowTemplateRegistry."""

    def _make_template(self, template_id="t1", domain=AgentDomain.CMDB):
        return WorkflowTemplate(
            template_id=template_id,
            name=f"Template {template_id}",
            description="test",
            domain=domain,
            steps=[
                WorkflowStep(step_id="s1", name="S1", agent_domain=domain)
            ],
        )

    def test_register_and_get(self):
        registry = WorkflowTemplateRegistry()
        template = self._make_template("t1")
        registry.register(template)

        result = registry.get("t1")
        assert result.template_id == "t1"
        assert registry.template_count == 1

    def test_register_duplicate_rejected(self):
        registry = WorkflowTemplateRegistry()
        template = self._make_template("t1")
        registry.register(template)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(template)

    def test_get_missing_raises_key_error(self):
        registry = WorkflowTemplateRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.get("nonexistent")

    def test_list_all(self):
        registry = WorkflowTemplateRegistry()
        registry.register(self._make_template("t2", AgentDomain.DISCOVERY))
        registry.register(self._make_template("t1", AgentDomain.CMDB))

        templates = registry.list_templates()
        assert len(templates) == 2
        # Sorted by template_id
        assert templates[0].template_id == "t1"
        assert templates[1].template_id == "t2"

    def test_list_by_domain(self):
        registry = WorkflowTemplateRegistry()
        registry.register(self._make_template("t1", AgentDomain.CMDB))
        registry.register(self._make_template("t2", AgentDomain.DISCOVERY))
        registry.register(self._make_template("t3", AgentDomain.CMDB))

        cmdb_templates = registry.list_templates(domain=AgentDomain.CMDB)
        assert len(cmdb_templates) == 2
        assert all(t.domain == AgentDomain.CMDB for t in cmdb_templates)

    def test_instantiate(self):
        registry = WorkflowTemplateRegistry()
        template = self._make_template("t1")
        registry.register(template)

        definition = registry.instantiate("t1", parameters={"key": "value"})
        assert definition.workflow_id.startswith("t1-")
        assert definition.name == "Template t1"
        assert len(definition.steps) == 1
        assert definition.metadata["template_id"] == "t1"
        assert definition.metadata["parameters"] == {"key": "value"}
        # Parameters should be merged into step parameters
        assert "key" in definition.steps[0].parameters

    def test_instantiate_without_parameters(self):
        registry = WorkflowTemplateRegistry()
        template = self._make_template("t1")
        registry.register(template)

        definition = registry.instantiate("t1")
        assert definition.workflow_id.startswith("t1-")
        assert definition.metadata["parameters"] == {}

    def test_instantiate_missing_template(self):
        registry = WorkflowTemplateRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.instantiate("nonexistent")


class TestDefaultRegistry:
    """Tests for the pre-built default templates (ORCH-014)."""

    def test_default_registry_has_templates(self):
        registry = get_default_registry()
        assert registry.template_count == 4

    def test_default_cmdb_health_check(self):
        registry = get_default_registry()
        template = registry.get("cmdb-health-check")
        assert template.domain == AgentDomain.CMDB
        assert len(template.steps) == 3
        assert "cmdb" in template.tags

    def test_default_incident_response(self):
        registry = get_default_registry()
        template = registry.get("incident-response")
        assert template.domain == AgentDomain.CSA
        assert len(template.steps) == 3

    def test_default_discovery_audit(self):
        registry = get_default_registry()
        template = registry.get("discovery-audit")
        assert template.domain == AgentDomain.DISCOVERY
        assert len(template.steps) == 3

    def test_default_asset_lifecycle(self):
        registry = get_default_registry()
        template = registry.get("asset-lifecycle")
        assert template.domain == AgentDomain.ASSET
        assert len(template.steps) == 3

    def test_instantiate_all_defaults(self):
        registry = get_default_registry()
        for template in registry.list_templates():
            definition = registry.instantiate(template.template_id)
            assert definition.workflow_id.startswith(template.template_id)
            assert len(definition.steps) > 0
