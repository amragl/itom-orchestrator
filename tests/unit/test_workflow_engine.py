"""
Tests for the workflow execution engine (ORCH-012).
"""

from datetime import UTC, datetime

import pytest

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.tasks import TaskStatus
from itom_orchestrator.models.workflows import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepType,
)
from itom_orchestrator.workflow_engine import (
    WorkflowEngine,
    WorkflowStepFailedError,
)


def _make_definition(workflow_id="wf-1", steps=None):
    """Helper to create a WorkflowDefinition for tests."""
    if steps is None:
        steps = [
            WorkflowStep(
                step_id="step-1",
                name="Step One",
                step_type=WorkflowStepType.TASK,
                agent_domain=AgentDomain.CMDB,
                parameters={"action": "query"},
            ),
            WorkflowStep(
                step_id="step-2",
                name="Step Two",
                step_type=WorkflowStepType.TASK,
                agent_domain=AgentDomain.DISCOVERY,
                parameters={"action": "scan"},
                depends_on=["step-1"],
            ),
            WorkflowStep(
                step_id="step-3",
                name="Step Three",
                step_type=WorkflowStepType.TASK,
                agent_domain=AgentDomain.DOCUMENTATION,
                parameters={"action": "report"},
                depends_on=["step-2"],
            ),
        ]
    return WorkflowDefinition(
        workflow_id=workflow_id,
        name=f"Test Workflow {workflow_id}",
        description="Test workflow",
        steps=steps,
        created_at=datetime.now(UTC),
    )


class TestWorkflowEngineStartWorkflow:
    """Tests for starting a workflow."""

    def test_start_workflow_creates_execution(self):
        engine = WorkflowEngine()
        definition = _make_definition()

        execution = engine.start_workflow(definition)

        assert execution.execution_id
        assert execution.workflow_id == "wf-1"
        assert execution.status == WorkflowStatus.RUNNING
        assert execution.started_at is not None
        assert len(execution.steps_remaining) == 3
        assert execution.steps_completed == []

    def test_start_workflow_with_context(self):
        engine = WorkflowEngine()
        definition = _make_definition()

        execution = engine.start_workflow(definition, context={"key": "value"})

        assert execution.context == {"key": "value"}

    def test_start_multiple_workflows(self):
        engine = WorkflowEngine()
        d1 = _make_definition("wf-1")
        d2 = _make_definition("wf-2")

        e1 = engine.start_workflow(d1)
        e2 = engine.start_workflow(d2)

        assert e1.execution_id != e2.execution_id
        assert e1.workflow_id == "wf-1"
        assert e2.workflow_id == "wf-2"


class TestWorkflowEngineAdvance:
    """Tests for advancing workflow execution."""

    def test_advance_executes_first_step(self):
        engine = WorkflowEngine()
        definition = _make_definition()
        execution = engine.start_workflow(definition)

        execution = engine.advance_workflow(execution)

        assert "step-1" in execution.steps_completed
        assert "step-1" not in execution.steps_remaining
        assert "step-1" in execution.step_results

    def test_advance_through_all_steps(self):
        engine = WorkflowEngine()
        definition = _make_definition()
        execution = engine.start_workflow(definition)

        # Advance step by step
        execution = engine.advance_workflow(execution)  # step-1
        assert "step-1" in execution.steps_completed

        execution = engine.advance_workflow(execution)  # step-2
        assert "step-2" in execution.steps_completed

        execution = engine.advance_workflow(execution)  # step-3
        assert execution.status == WorkflowStatus.COMPLETED
        assert execution.completed_at is not None
        assert len(execution.steps_completed) == 3
        assert len(execution.steps_remaining) == 0

    def test_advance_completed_workflow_is_noop(self):
        engine = WorkflowEngine()
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="only-step",
                    name="Only",
                    agent_domain=AgentDomain.CMDB,
                )
            ]
        )
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)
        assert execution.status == WorkflowStatus.COMPLETED

        # Advancing again does nothing
        execution = engine.advance_workflow(execution)
        assert execution.status == WorkflowStatus.COMPLETED

    def test_advance_respects_dependencies(self):
        engine = WorkflowEngine()
        definition = _make_definition()
        execution = engine.start_workflow(definition)

        # Before advancing, only step-1 should be ready
        ready = engine.get_ready_steps(execution)
        assert ready == ["step-1"]

        # After step-1 completes, step-2 becomes ready
        execution = engine.advance_workflow(execution)
        ready = engine.get_ready_steps(execution)
        assert ready == ["step-2"]

    def test_parallel_ready_steps(self):
        """Steps with no dependencies on each other should both be ready."""
        engine = WorkflowEngine()
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="step-a",
                    name="Step A",
                    agent_domain=AgentDomain.CMDB,
                ),
                WorkflowStep(
                    step_id="step-b",
                    name="Step B",
                    agent_domain=AgentDomain.DISCOVERY,
                ),
                WorkflowStep(
                    step_id="step-c",
                    name="Step C",
                    agent_domain=AgentDomain.ASSET,
                    depends_on=["step-a", "step-b"],
                ),
            ]
        )
        execution = engine.start_workflow(definition)

        ready = engine.get_ready_steps(execution)
        assert "step-a" in ready
        assert "step-b" in ready
        assert "step-c" not in ready

    def test_step_results_stored(self):
        engine = WorkflowEngine()
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="s1",
                    name="Step 1",
                    agent_domain=AgentDomain.CMDB,
                )
            ]
        )
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)

        result = execution.step_results["s1"]
        assert result.status == TaskStatus.COMPLETED
        assert result.result_data["step_id"] == "s1"

    def test_step_result_merged_into_context(self):
        engine = WorkflowEngine()
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="s1",
                    name="Step 1",
                    agent_domain=AgentDomain.CMDB,
                    parameters={"key": "value"},
                ),
            ]
        )
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)

        # Step result data should be in context under the step_id key
        assert "s1" in execution.context


class TestWorkflowEngineFailure:
    """Tests for step failure handling."""

    def test_step_failure_stop_policy(self):
        """When on_failure='stop', the workflow should fail."""
        engine = WorkflowEngine()

        # We need to make the step fail. We'll use a custom executor
        # that raises on a specific step.
        class FailingExecutor:
            def execute(self, task, decision):
                raise RuntimeError("Simulated failure")

        # Cannot use FailingExecutor without registry, so test via
        # the default path with a step that fails during _execute_step
        # by overriding the engine method.
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="fail-step",
                    name="Failing Step",
                    agent_domain=AgentDomain.CMDB,
                    on_failure="stop",
                ),
            ]
        )

        class FailEngine(WorkflowEngine):
            def _execute_step(self, step, execution):
                raise RuntimeError("Simulated step failure")

        engine = FailEngine()
        execution = engine.start_workflow(definition)

        with pytest.raises(WorkflowStepFailedError, match="Simulated step failure"):
            engine.advance_workflow(execution)

        assert execution.status == WorkflowStatus.FAILED
        assert "fail-step" in str(execution.error_message)

    def test_step_failure_skip_policy(self):
        """When on_failure='skip', the workflow should continue."""
        definition = _make_definition(
            steps=[
                WorkflowStep(
                    step_id="skip-step",
                    name="Skippable Step",
                    agent_domain=AgentDomain.CMDB,
                    on_failure="skip",
                ),
                WorkflowStep(
                    step_id="next-step",
                    name="Next Step",
                    agent_domain=AgentDomain.DISCOVERY,
                    depends_on=["skip-step"],
                ),
            ]
        )

        class SkipEngine(WorkflowEngine):
            def _execute_step(self, step, execution):
                if step.step_id == "skip-step":
                    raise RuntimeError("Simulated skip failure")
                return super()._execute_step(step, execution)

        engine = SkipEngine()
        execution = engine.start_workflow(definition)

        # First advance: skip-step fails but is skipped
        execution = engine.advance_workflow(execution)
        assert "skip-step" in execution.steps_completed
        assert execution.step_results["skip-step"].status == TaskStatus.FAILED

        # Second advance: next-step runs
        execution = engine.advance_workflow(execution)
        assert execution.status == WorkflowStatus.COMPLETED


class TestWorkflowEngineCancelAndLookup:
    """Tests for cancel and lookup operations."""

    def test_cancel_workflow(self):
        engine = WorkflowEngine()
        definition = _make_definition()
        execution = engine.start_workflow(definition)

        cancelled = engine.cancel_workflow(execution.execution_id)
        assert cancelled.status == WorkflowStatus.CANCELLED
        assert cancelled.completed_at is not None

    def test_cancel_unknown_raises(self):
        engine = WorkflowEngine()
        with pytest.raises(KeyError, match="not found"):
            engine.cancel_workflow("nonexistent-id")

    def test_get_execution(self):
        engine = WorkflowEngine()
        definition = _make_definition()
        execution = engine.start_workflow(definition)

        found = engine.get_execution(execution.execution_id)
        assert found is not None
        assert found.execution_id == execution.execution_id

    def test_get_execution_missing(self):
        engine = WorkflowEngine()
        assert engine.get_execution("nonexistent") is None

    def test_list_executions(self):
        engine = WorkflowEngine()
        d1 = _make_definition("wf-1")
        d2 = _make_definition("wf-2")
        engine.start_workflow(d1)
        engine.start_workflow(d2)

        all_execs = engine.list_executions()
        assert len(all_execs) == 2

    def test_list_executions_by_status(self):
        engine = WorkflowEngine()
        d1 = _make_definition("wf-1")
        d2 = _make_definition("wf-2")
        engine.start_workflow(d1)
        e2 = engine.start_workflow(d2)
        engine.cancel_workflow(e2.execution_id)

        running = engine.list_executions(status=WorkflowStatus.RUNNING)
        assert len(running) == 1

        cancelled = engine.list_executions(status=WorkflowStatus.CANCELLED)
        assert len(cancelled) == 1


class TestWorkflowEngineGetReadySteps:
    """Tests for get_ready_steps method."""

    def test_no_definition_returns_empty(self):
        engine = WorkflowEngine()
        execution = WorkflowExecution(
            execution_id="fake",
            workflow_id="wf-1",
            status=WorkflowStatus.RUNNING,
            steps_remaining=["s1"],
        )
        assert engine.get_ready_steps(execution) == []

    def test_all_deps_satisfied(self):
        engine = WorkflowEngine()
        definition = _make_definition(
            steps=[
                WorkflowStep(step_id="a", name="A", agent_domain=AgentDomain.CMDB),
                WorkflowStep(
                    step_id="b",
                    name="B",
                    agent_domain=AgentDomain.CMDB,
                    depends_on=["a"],
                ),
            ]
        )
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)  # complete "a"
        ready = engine.get_ready_steps(execution)
        assert ready == ["b"]
