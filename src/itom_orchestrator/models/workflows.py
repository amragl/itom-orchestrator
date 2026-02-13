"""
Workflow-related Pydantic models for the ITOM Orchestrator.

Defines the data contracts for workflow definitions, steps, and
execution instances. Workflows orchestrate multi-step, multi-agent
operations with dependency management and state tracking.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.tasks import TaskResult


class WorkflowStepType(StrEnum):
    """Type of a workflow step.

    Determines how the step is executed by the workflow engine.
    """

    TASK = "task"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"


class WorkflowStatus(StrEnum):
    """Lifecycle status of a workflow execution.

    The workflow engine enforces valid transitions between these states.
    """

    PENDING = "pending"
    RUNNING = "running"
    STEP_EXECUTING = "step_executing"
    STEP_COMPLETED = "step_completed"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkflowStep(BaseModel):
    """A single step in a workflow definition.

    Steps can depend on other steps (via ``depends_on``), which determines
    execution order. The workflow engine uses step dependencies to build
    a DAG and execute steps in the correct order.

    Attributes:
        step_id: Unique identifier within the workflow.
        name: Human-readable step name.
        step_type: How this step should be executed.
        agent_domain: Domain hint for routing the step's task.
        target_agent: Explicit agent ID override.
        parameters: Input parameters for the step.
        depends_on: List of step IDs that must complete before this step runs.
        timeout_seconds: Maximum execution time for this step.
        on_failure: What to do if this step fails: ``"stop"``, ``"skip"``, or ``"retry"``.
        max_retries: Maximum retry attempts for this step.
    """

    step_id: str
    name: str
    step_type: WorkflowStepType = WorkflowStepType.TASK
    agent_domain: AgentDomain | None = None
    target_agent: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: float = 300.0
    on_failure: str = "stop"
    max_retries: int = 2

    @field_validator("step_id")
    @classmethod
    def step_id_must_be_non_empty(cls, v: str) -> str:
        """Step ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("step_id must not be empty")
        return v

    @field_validator("on_failure")
    @classmethod
    def on_failure_must_be_valid(cls, v: str) -> str:
        """on_failure must be one of 'stop', 'skip', or 'retry'."""
        allowed = {"stop", "skip", "retry"}
        if v not in allowed:
            raise ValueError(f"on_failure must be one of {allowed}, got '{v}'")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def timeout_must_be_positive(cls, v: float) -> float:
        """Timeout must be a positive number."""
        if v <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {v}")
        return v


class WorkflowDefinition(BaseModel):
    """A reusable workflow template.

    Workflow definitions describe the structure of a multi-step operation.
    They are instantiated as :class:`WorkflowExecution` objects when
    executed. Definitions are validated to ensure step IDs are unique
    and all ``depends_on`` references point to valid step IDs within
    the same workflow.

    Attributes:
        workflow_id: Unique identifier (e.g., ``"full-discovery-scan"``).
        name: Human-readable workflow name.
        description: What this workflow accomplishes.
        version: Semantic version of the workflow definition.
        steps: Ordered list of workflow steps.
        created_at: When this definition was created.
        metadata: Arbitrary key-value metadata for extensibility.
    """

    workflow_id: str
    name: str
    description: str
    version: str = "1.0.0"
    steps: list[WorkflowStep]
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("workflow_id")
    @classmethod
    def workflow_id_must_be_non_empty(cls, v: str) -> str:
        """Workflow ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("workflow_id must not be empty")
        return v

    @field_validator("steps")
    @classmethod
    def must_have_at_least_one_step(cls, v: list[WorkflowStep]) -> list[WorkflowStep]:
        """A workflow must contain at least one step."""
        if not v:
            raise ValueError("Workflow must have at least one step")
        return v

    @model_validator(mode="after")
    def validate_step_references(self) -> "WorkflowDefinition":
        """Validate that step IDs are unique and depends_on references are valid."""
        step_ids = [step.step_id for step in self.steps]

        # Check for duplicate step IDs
        seen: set[str] = set()
        duplicates: list[str] = []
        for sid in step_ids:
            if sid in seen:
                duplicates.append(sid)
            seen.add(sid)
        if duplicates:
            raise ValueError(f"Duplicate step_ids found: {duplicates}")

        # Check that all depends_on references point to valid step IDs
        valid_ids = set(step_ids)
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in valid_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on '{dep}', "
                        f"which is not a valid step ID in this workflow. "
                        f"Valid IDs: {sorted(valid_ids)}"
                    )
                if dep == step.step_id:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on itself (circular dependency)"
                    )

        return self


class WorkflowExecution(BaseModel):
    """A running instance of a workflow.

    Created when a :class:`WorkflowDefinition` is executed. Tracks the
    current state, which steps have completed, which are remaining,
    accumulated results, and shared context data.

    Attributes:
        execution_id: Unique identifier (UUID format).
        workflow_id: Which workflow definition this execution is based on.
        status: Current lifecycle status.
        current_step_id: The step currently being executed (if any).
        steps_completed: List of step IDs that have finished successfully.
        steps_remaining: List of step IDs that have not yet been executed.
        step_results: Map of step_id to TaskResult for completed steps.
        context: Accumulated data passed between steps during execution.
        started_at: When execution began.
        completed_at: When execution finished (success, failure, or cancellation).
        error_message: Human-readable error description if execution failed.
        metadata: Arbitrary key-value metadata for extensibility.
    """

    execution_id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step_id: str | None = None
    steps_completed: list[str] = Field(default_factory=list)
    steps_remaining: list[str] = Field(default_factory=list)
    step_results: dict[str, TaskResult] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution_id")
    @classmethod
    def execution_id_must_be_non_empty(cls, v: str) -> str:
        """Execution ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("execution_id must not be empty")
        return v

    @field_validator("workflow_id")
    @classmethod
    def workflow_id_must_be_non_empty(cls, v: str) -> str:
        """Workflow ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("workflow_id must not be empty")
        return v
