"""
Task-related Pydantic models for the ITOM Orchestrator.

Defines the data contracts for task creation, routing, execution,
and result reporting. Tasks are the primary unit of work dispatched
to agents by the orchestrator.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from itom_orchestrator.models.agents import AgentDomain


class TaskPriority(StrEnum):
    """Priority levels for task scheduling.

    Higher-priority tasks are routed and executed before lower-priority ones.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(StrEnum):
    """Lifecycle status of a task.

    Tasks progress through these states: PENDING -> ROUTED -> EXECUTING ->
    one of {COMPLETED, FAILED, CANCELLED, TIMED_OUT}.
    """

    PENDING = "pending"
    ROUTED = "routed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# Terminal statuses -- a task in one of these states will not change again.
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMED_OUT})


class Task(BaseModel):
    """A task to be routed to an agent for execution.

    Tasks are created by MCP clients or workflow steps and routed to the
    appropriate agent based on domain, capabilities, and routing rules.

    Attributes:
        task_id: Unique identifier (UUID format recommended).
        title: Short human-readable title for the task.
        description: Detailed description of what the task should accomplish.
        domain: Routing hint -- the domain this task relates to.
        target_agent: Explicit agent ID override (bypasses domain routing).
        priority: Task priority for scheduling.
        status: Current lifecycle status.
        parameters: Arbitrary input parameters passed to the executing agent.
        created_at: When the task was created.
        timeout_seconds: Maximum execution time before the task times out.
        retry_count: Number of retry attempts already made.
        max_retries: Maximum number of retry attempts allowed.
        metadata: Arbitrary key-value metadata for extensibility.
    """

    task_id: str
    title: str
    description: str
    domain: AgentDomain | None = None
    target_agent: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    timeout_seconds: float = 300.0
    retry_count: int = 0
    max_retries: int = 3
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id")
    @classmethod
    def task_id_must_be_non_empty(cls, v: str) -> str:
        """Task ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("task_id must not be empty")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def timeout_must_be_positive(cls, v: float) -> float:
        """Timeout must be a positive number."""
        if v <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {v}")
        return v

    @model_validator(mode="after")
    def retry_count_within_bounds(self) -> "Task":
        """Retry count must not exceed max retries."""
        if self.retry_count > self.max_retries:
            raise ValueError(
                f"retry_count ({self.retry_count}) must not exceed "
                f"max_retries ({self.max_retries})"
            )
        return self


class TaskResult(BaseModel):
    """Result of a task execution.

    Produced by the agent that handled the task. Contains the outcome,
    any result data, and execution timing information.

    Attributes:
        task_id: ID of the task that was executed.
        agent_id: ID of the agent that handled the task.
        status: Terminal status of the task (COMPLETED, FAILED, or TIMED_OUT).
        result_data: Arbitrary output data from the execution.
        error_message: Human-readable error description (for FAILED/TIMED_OUT).
        started_at: When execution began.
        completed_at: When execution finished.
        duration_seconds: Wall-clock duration of the execution.
    """

    task_id: str
    agent_id: str
    status: TaskStatus
    result_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_seconds: float

    @field_validator("status")
    @classmethod
    def status_must_be_terminal(cls, v: TaskStatus) -> TaskStatus:
        """TaskResult status must be a terminal state (COMPLETED, FAILED, or TIMED_OUT)."""
        if v not in _TERMINAL_STATUSES:
            raise ValueError(
                f"TaskResult status must be a terminal state "
                f"({', '.join(s.value for s in _TERMINAL_STATUSES)}), got '{v.value}'"
            )
        return v

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_non_negative(cls, v: float) -> float:
        """Duration must be zero or positive."""
        if v < 0:
            raise ValueError(f"duration_seconds must be >= 0, got {v}")
        return v
