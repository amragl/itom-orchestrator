"""
Workflow execution engine for the ITOM Orchestrator.

Executes workflow definitions step by step, managing dependency
ordering, state transitions, step results, and failure handling.

This module implements ORCH-012: Workflow State Machine and Execution Engine.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskResult, TaskStatus
from itom_orchestrator.models.workflows import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
    WorkflowStep,
)

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class WorkflowEngineError(Exception):
    """Base exception for workflow engine errors."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class WorkflowStepFailedError(WorkflowEngineError):
    """Raised when a workflow step fails and on_failure is 'stop'."""

    def __init__(self, step_id: str, reason: str) -> None:
        super().__init__(
            "ORCH_3003",
            f"Workflow step '{step_id}' failed: {reason}",
        )


class WorkflowEngine:
    """Executes workflow definitions step by step.

    The engine builds a dependency graph from WorkflowStep.depends_on,
    executes steps in topological order, tracks step results in the
    WorkflowExecution, and handles step failures according to the
    step's on_failure policy.

    Args:
        executor: Optional TaskExecutor for dispatching steps. When None,
            steps produce a default acknowledgment result (useful for
            orchestrator-level tracking before MCP transport is connected).
        registry: Optional AgentRegistry for agent lookups during step dispatch.
    """

    def __init__(
        self,
        executor: Any = None,
        registry: Any = None,
    ) -> None:
        self._executor = executor
        self._registry = registry
        self._executions: dict[str, WorkflowExecution] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}

    def start_workflow(
        self,
        definition: WorkflowDefinition,
        context: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Start a new workflow execution from a definition.

        Creates a WorkflowExecution in RUNNING state with all steps
        marked as remaining.

        Args:
            definition: The workflow definition to execute.
            context: Optional initial context data passed between steps.

        Returns:
            A new WorkflowExecution in RUNNING state.
        """
        execution_id = str(uuid4())
        step_ids = [step.step_id for step in definition.steps]

        execution = WorkflowExecution(
            execution_id=execution_id,
            workflow_id=definition.workflow_id,
            status=WorkflowStatus.RUNNING,
            steps_remaining=list(step_ids),
            context=context or {},
            started_at=datetime.now(UTC),
        )

        self._executions[execution_id] = execution
        self._definitions[execution_id] = definition

        logger.info(
            "Workflow started",
            extra={
                "extra_data": {
                    "execution_id": execution_id,
                    "workflow_id": definition.workflow_id,
                    "step_count": len(step_ids),
                }
            },
        )
        return execution

    def advance_workflow(self, execution: WorkflowExecution) -> WorkflowExecution:
        """Advance the workflow by executing the next ready step(s).

        Finds steps whose dependencies are satisfied and executes them.
        Updates the execution state after each step completes.

        Args:
            execution: The current workflow execution state.

        Returns:
            Updated WorkflowExecution reflecting the step results.

        Raises:
            WorkflowStepFailedError: If a step fails and its on_failure is 'stop'.
        """
        if execution.status not in (WorkflowStatus.RUNNING, WorkflowStatus.STEP_COMPLETED):
            logger.warning(
                "Cannot advance workflow in current state",
                extra={
                    "extra_data": {
                        "execution_id": execution.execution_id,
                        "status": execution.status.value,
                    }
                },
            )
            return execution

        definition = self._definitions.get(execution.execution_id)
        if definition is None:
            execution.status = WorkflowStatus.FAILED
            execution.error_message = "Workflow definition not found for execution"
            execution.completed_at = datetime.now(UTC)
            return execution

        ready_step_ids = self.get_ready_steps(execution)

        if not ready_step_ids:
            # No more steps to execute
            if not execution.steps_remaining:
                execution.status = WorkflowStatus.COMPLETED
                execution.completed_at = datetime.now(UTC)
                logger.info(
                    "Workflow completed",
                    extra={
                        "extra_data": {
                            "execution_id": execution.execution_id,
                            "workflow_id": execution.workflow_id,
                        }
                    },
                )
            return execution

        # Execute each ready step
        step_map = {step.step_id: step for step in definition.steps}
        for step_id in ready_step_ids:
            step = step_map.get(step_id)
            if step is None:
                continue

            execution.current_step_id = step_id
            execution.status = WorkflowStatus.STEP_EXECUTING

            try:
                result = self._execute_step(step, execution)

                # Record success
                execution.step_results[step_id] = result
                execution.steps_completed.append(step_id)
                execution.steps_remaining.remove(step_id)
                execution.current_step_id = None
                execution.status = WorkflowStatus.STEP_COMPLETED

                # Merge result data into context for downstream steps
                if result.result_data:
                    execution.context[step_id] = result.result_data

                logger.info(
                    "Workflow step completed",
                    extra={
                        "extra_data": {
                            "execution_id": execution.execution_id,
                            "step_id": step_id,
                            "remaining": len(execution.steps_remaining),
                        }
                    },
                )

            except Exception as exc:
                error_msg = str(exc)
                logger.error(
                    "Workflow step failed",
                    extra={
                        "extra_data": {
                            "execution_id": execution.execution_id,
                            "step_id": step_id,
                            "error": error_msg,
                            "on_failure": step.on_failure,
                        }
                    },
                )

                if step.on_failure == "stop":
                    execution.status = WorkflowStatus.FAILED
                    execution.error_message = (
                        f"Step '{step_id}' failed: {error_msg}"
                    )
                    execution.completed_at = datetime.now(UTC)
                    execution.current_step_id = None
                    self._executions[execution.execution_id] = execution
                    raise WorkflowStepFailedError(step_id, error_msg) from exc

                elif step.on_failure == "skip":
                    # Mark step as failed but continue
                    failed_result = TaskResult(
                        task_id=step_id,
                        agent_id="workflow-engine",
                        status=TaskStatus.FAILED,
                        error_message=error_msg,
                        started_at=datetime.now(UTC),
                        completed_at=datetime.now(UTC),
                        duration_seconds=0.0,
                    )
                    execution.step_results[step_id] = failed_result
                    execution.steps_completed.append(step_id)
                    execution.steps_remaining.remove(step_id)
                    execution.current_step_id = None
                    execution.status = WorkflowStatus.STEP_COMPLETED

        # Check if all steps are done
        if not execution.steps_remaining:
            execution.status = WorkflowStatus.COMPLETED
            execution.completed_at = datetime.now(UTC)
            logger.info(
                "Workflow completed",
                extra={
                    "extra_data": {
                        "execution_id": execution.execution_id,
                        "workflow_id": execution.workflow_id,
                    }
                },
            )

        self._executions[execution.execution_id] = execution
        return execution

    def cancel_workflow(self, execution_id: str) -> WorkflowExecution:
        """Cancel a running workflow execution.

        Args:
            execution_id: The execution to cancel.

        Returns:
            Updated WorkflowExecution in CANCELLED state.

        Raises:
            KeyError: If the execution is not found.
        """
        execution = self._executions.get(execution_id)
        if execution is None:
            raise KeyError(f"Execution '{execution_id}' not found")

        execution.status = WorkflowStatus.CANCELLED
        execution.completed_at = datetime.now(UTC)
        execution.current_step_id = None

        logger.info(
            "Workflow cancelled",
            extra={
                "extra_data": {
                    "execution_id": execution_id,
                    "workflow_id": execution.workflow_id,
                }
            },
        )
        return execution

    def get_ready_steps(self, execution: WorkflowExecution) -> list[str]:
        """Determine which steps are ready to execute.

        A step is ready when all its dependencies have been completed.

        Args:
            execution: The current execution state.

        Returns:
            List of step IDs whose dependencies are all satisfied.
        """
        definition = self._definitions.get(execution.execution_id)
        if definition is None:
            return []

        step_map = {step.step_id: step for step in definition.steps}
        completed = set(execution.steps_completed)
        ready: list[str] = []

        for step_id in execution.steps_remaining:
            step = step_map.get(step_id)
            if step is None:
                continue
            # Step is ready if all dependencies are completed
            if all(dep in completed for dep in step.depends_on):
                ready.append(step_id)

        return ready

    def _execute_step(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> TaskResult:
        """Execute a single workflow step.

        If an executor is configured, creates a Task and dispatches it.
        Otherwise, produces a default acknowledgment result.

        Args:
            step: The workflow step to execute.
            execution: The parent workflow execution for context.

        Returns:
            TaskResult from the step execution.
        """
        started_at = datetime.now(UTC)

        if self._executor is not None and self._registry is not None:
            # Create a task for this step and dispatch via the executor
            task = Task(
                task_id=f"{execution.execution_id}-{step.step_id}",
                title=step.name,
                description=f"Workflow step: {step.name}",
                domain=step.agent_domain,
                target_agent=step.target_agent,
                priority=TaskPriority.MEDIUM,
                status=TaskStatus.PENDING,
                parameters={**step.parameters, **execution.context},
                created_at=started_at,
                timeout_seconds=step.timeout_seconds,
            )

            from itom_orchestrator.router import TaskRouter

            router = TaskRouter(
                registry=self._registry, require_available=False
            )
            decision = router.route(task)
            result = self._executor.execute(task, decision)
            return result

        # Default: produce an acknowledgment result
        completed_at = datetime.now(UTC)
        duration = (completed_at - started_at).total_seconds()

        return TaskResult(
            task_id=step.step_id,
            agent_id=step.target_agent or "workflow-engine",
            status=TaskStatus.COMPLETED,
            result_data={
                "step_id": step.step_id,
                "step_name": step.name,
                "agent_domain": step.agent_domain.value if step.agent_domain else None,
                "parameters": step.parameters,
                "acknowledged": True,
            },
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
        )

    def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        """Look up an execution by ID.

        Args:
            execution_id: The execution ID to look up.

        Returns:
            The WorkflowExecution, or None if not found.
        """
        return self._executions.get(execution_id)

    def list_executions(
        self, status: WorkflowStatus | None = None
    ) -> list[WorkflowExecution]:
        """List all tracked workflow executions.

        Args:
            status: If provided, filter to executions in this state.

        Returns:
            List of WorkflowExecution objects.
        """
        if status is not None:
            return [e for e in self._executions.values() if e.status == status]
        return list(self._executions.values())
