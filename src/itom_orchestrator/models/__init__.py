"""
Pydantic models for the ITOM Orchestrator.

All foundational data contracts are defined here and re-exported
for convenient access via ``from itom_orchestrator.models import ...``.

Modules:
    agents -- Agent registration, capabilities, domains, and status.
    tasks -- Task creation, routing, execution, and results.
    workflows -- Workflow definitions, steps, and execution instances.
    messages -- Inter-agent messaging and event communication.
"""

from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.models.messages import AgentMessage, MessageType
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskResult, TaskStatus
from itom_orchestrator.models.workflows import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepType,
)

__all__ = [
    # Agent models
    "AgentCapability",
    "AgentDomain",
    "AgentRegistration",
    "AgentStatus",
    # Task models
    "Task",
    "TaskPriority",
    "TaskResult",
    "TaskStatus",
    # Workflow models
    "WorkflowDefinition",
    "WorkflowExecution",
    "WorkflowStatus",
    "WorkflowStep",
    "WorkflowStepType",
    # Message models
    "AgentMessage",
    "MessageType",
]
