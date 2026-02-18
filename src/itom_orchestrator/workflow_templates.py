"""
Workflow template registry for the ITOM Orchestrator.

Provides pre-built workflow templates that can be instantiated as
WorkflowDefinition objects with custom parameters. Templates serve
as reusable blueprints for common multi-step operations.

This module implements ORCH-011 and ORCH-014.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.workflows import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowStepType,
)

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class WorkflowTemplate(BaseModel):
    """A reusable workflow template.

    Templates define the structure of a workflow without binding it
    to a specific execution. Use ``WorkflowTemplateRegistry.instantiate``
    to create a runnable ``WorkflowDefinition`` from a template.

    Attributes:
        template_id: Unique identifier for the template.
        name: Human-readable template name.
        description: What this template workflow accomplishes.
        domain: Primary agent domain for this workflow.
        steps: Ordered list of workflow step definitions.
        tags: Searchable tags for template discovery.
        metadata: Arbitrary key-value metadata.
    """

    template_id: str
    name: str
    description: str
    domain: AgentDomain
    steps: list[WorkflowStep]
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template_id")
    @classmethod
    def template_id_must_be_non_empty(cls, v: str) -> str:
        """Template ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("template_id must not be empty")
        return v

    @field_validator("name")
    @classmethod
    def name_must_be_non_empty(cls, v: str) -> str:
        """Template name must be a non-empty string."""
        if not v.strip():
            raise ValueError("name must not be empty")
        return v


class WorkflowTemplateRegistry:
    """Registry of pre-built workflow templates.

    Provides template registration, lookup, listing, and instantiation
    into concrete WorkflowDefinition objects.
    """

    def __init__(self) -> None:
        self._templates: dict[str, WorkflowTemplate] = {}

    def register(self, template: WorkflowTemplate) -> None:
        """Register a workflow template.

        Args:
            template: The template to register.

        Raises:
            ValueError: If a template with the same ID is already registered.
        """
        if template.template_id in self._templates:
            raise ValueError(
                f"Template '{template.template_id}' is already registered"
            )
        self._templates[template.template_id] = template
        logger.info(
            "Workflow template registered",
            extra={
                "extra_data": {
                    "template_id": template.template_id,
                    "name": template.name,
                    "domain": template.domain.value,
                    "step_count": len(template.steps),
                }
            },
        )

    def get(self, template_id: str) -> WorkflowTemplate:
        """Look up a template by ID.

        Args:
            template_id: The template ID to look up.

        Returns:
            The matching WorkflowTemplate.

        Raises:
            KeyError: If no template with the given ID exists.
        """
        if template_id not in self._templates:
            raise KeyError(f"Template '{template_id}' not found")
        return self._templates[template_id]

    def list_templates(self, domain: AgentDomain | None = None) -> list[WorkflowTemplate]:
        """List all registered templates, optionally filtered by domain.

        Args:
            domain: If provided, only return templates for this domain.

        Returns:
            List of matching templates sorted by template_id.
        """
        if domain is not None:
            templates = [
                t for t in self._templates.values() if t.domain == domain
            ]
        else:
            templates = list(self._templates.values())
        return sorted(templates, key=lambda t: t.template_id)

    def instantiate(
        self,
        template_id: str,
        parameters: dict[str, Any] | None = None,
    ) -> WorkflowDefinition:
        """Create a WorkflowDefinition from a template.

        Generates a unique workflow_id and creates a concrete definition
        from the template's steps. Parameters are stored in the
        definition's metadata for reference.

        Args:
            template_id: The template to instantiate.
            parameters: Optional parameters to customize the workflow.

        Returns:
            A new WorkflowDefinition ready for execution.

        Raises:
            KeyError: If the template is not found.
        """
        template = self.get(template_id)
        params = parameters or {}

        # Apply parameter substitution to step parameters
        steps: list[WorkflowStep] = []
        for step in template.steps:
            merged_params = {**step.parameters, **params}
            new_step = step.model_copy(update={"parameters": merged_params})
            steps.append(new_step)

        workflow_id = f"{template.template_id}-{uuid4().hex[:8]}"
        definition = WorkflowDefinition(
            workflow_id=workflow_id,
            name=template.name,
            description=template.description,
            steps=steps,
            created_at=datetime.now(UTC),
            metadata={
                "template_id": template.template_id,
                "parameters": params,
                "domain": template.domain.value,
                "tags": template.tags,
            },
        )

        logger.info(
            "Workflow instantiated from template",
            extra={
                "extra_data": {
                    "template_id": template_id,
                    "workflow_id": workflow_id,
                    "step_count": len(steps),
                }
            },
        )
        return definition

    @property
    def template_count(self) -> int:
        """Return the number of registered templates."""
        return len(self._templates)


def _build_default_templates() -> list[WorkflowTemplate]:
    """Build pre-built workflow templates for common ITOM operations.

    Returns:
        List of default WorkflowTemplate objects.
    """
    return [
        WorkflowTemplate(
            template_id="cmdb-health-check",
            name="CMDB Health Check Workflow",
            description=(
                "CMDB CI health check with discovery scan verification "
                "and report generation."
            ),
            domain=AgentDomain.CMDB,
            steps=[
                WorkflowStep(
                    step_id="scan-cis",
                    name="Scan Configuration Items",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CMDB,
                    parameters={"action": "cmdb_health_audit"},
                ),
                WorkflowStep(
                    step_id="verify-discovery",
                    name="Verify Discovery Data",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DISCOVERY,
                    parameters={"action": "get_discovery_status"},
                    depends_on=["scan-cis"],
                ),
                WorkflowStep(
                    step_id="generate-report",
                    name="Generate Health Report",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DOCUMENTATION,
                    parameters={"action": "generate_documentation", "report_type": "health"},
                    depends_on=["verify-discovery"],
                ),
            ],
            tags=["cmdb", "health", "audit"],
        ),
        WorkflowTemplate(
            template_id="incident-response",
            name="Incident Response Workflow",
            description=(
                "Detect incident, assess impact via CMDB, route to "
                "the appropriate agent, and create a remediation ticket."
            ),
            domain=AgentDomain.CSA,
            steps=[
                WorkflowStep(
                    step_id="assess-impact",
                    name="Assess Incident Impact",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CMDB,
                    parameters={"action": "map_relationships"},
                ),
                WorkflowStep(
                    step_id="route-to-agent",
                    name="Route to Responsible Agent",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CSA,
                    parameters={"action": "fulfill_requests"},
                    depends_on=["assess-impact"],
                ),
                WorkflowStep(
                    step_id="create-ticket",
                    name="Create Remediation Ticket",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CSA,
                    parameters={"action": "run_remediation"},
                    depends_on=["route-to-agent"],
                ),
            ],
            tags=["incident", "response", "remediation"],
        ),
        WorkflowTemplate(
            template_id="discovery-audit",
            name="Discovery Audit Workflow",
            description=(
                "Run a discovery scan, audit the results for compliance, "
                "and generate an audit report."
            ),
            domain=AgentDomain.DISCOVERY,
            steps=[
                WorkflowStep(
                    step_id="run-scan",
                    name="Run Discovery Scan",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DISCOVERY,
                    parameters={"action": "run_discovery_scan"},
                ),
                WorkflowStep(
                    step_id="audit-results",
                    name="Audit Discovery Results",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.AUDIT,
                    parameters={"action": "run_compliance_audit"},
                    depends_on=["run-scan"],
                ),
                WorkflowStep(
                    step_id="generate-report",
                    name="Generate Audit Report",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DOCUMENTATION,
                    parameters={"action": "generate_documentation", "report_type": "audit"},
                    depends_on=["audit-results"],
                ),
            ],
            tags=["discovery", "audit", "compliance"],
        ),
        WorkflowTemplate(
            template_id="asset-lifecycle",
            name="Asset Lifecycle Workflow",
            description=(
                "Scan IT assets, check compliance against policies, "
                "and generate lifecycle documentation."
            ),
            domain=AgentDomain.ASSET,
            steps=[
                WorkflowStep(
                    step_id="scan-assets",
                    name="Scan IT Assets",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.ASSET,
                    parameters={"action": "query_assets"},
                ),
                WorkflowStep(
                    step_id="check-compliance",
                    name="Check Asset Compliance",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.ASSET,
                    parameters={"action": "license_compliance_check"},
                    depends_on=["scan-assets"],
                ),
                WorkflowStep(
                    step_id="generate-docs",
                    name="Generate Lifecycle Documentation",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DOCUMENTATION,
                    parameters={"action": "generate_documentation", "report_type": "lifecycle"},
                    depends_on=["check-compliance"],
                ),
            ],
            tags=["asset", "lifecycle", "compliance"],
        ),
    ]


def get_default_registry() -> WorkflowTemplateRegistry:
    """Create a WorkflowTemplateRegistry pre-loaded with default templates.

    Returns:
        Registry populated with all pre-built ITOM workflow templates.
    """
    registry = WorkflowTemplateRegistry()
    for template in _build_default_templates():
        registry.register(template)
    logger.info(
        "Default template registry created",
        extra={"extra_data": {"template_count": registry.template_count}},
    )
    return registry
