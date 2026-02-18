"""
Integration tests for workflow execution (ORCH-022).

Tests end-to-end workflow: create definition -> start -> advance -> complete.
Tests checkpointing: save -> reload -> resume.
"""

import pytest

from datetime import UTC, datetime

from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.models.workflows import (
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepType,
)
from itom_orchestrator.workflow_checkpoint import WorkflowCheckpointer
from itom_orchestrator.workflow_engine import WorkflowEngine, WorkflowStepFailedError
from itom_orchestrator.workflow_templates import get_default_registry


@pytest.mark.integration
class TestWorkflowEndToEnd:
    """End-to-end workflow execution tests."""

    def _make_linear_definition(self):
        return WorkflowDefinition(
            workflow_id="integration-wf",
            name="Integration Test Workflow",
            description="Tests full workflow lifecycle",
            steps=[
                WorkflowStep(
                    step_id="step-1",
                    name="Query CMDB",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.CMDB,
                    parameters={"action": "query_cis"},
                ),
                WorkflowStep(
                    step_id="step-2",
                    name="Run Discovery",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DISCOVERY,
                    parameters={"action": "run_discovery_scan"},
                    depends_on=["step-1"],
                ),
                WorkflowStep(
                    step_id="step-3",
                    name="Generate Report",
                    step_type=WorkflowStepType.TASK,
                    agent_domain=AgentDomain.DOCUMENTATION,
                    parameters={"action": "generate_documentation"},
                    depends_on=["step-2"],
                ),
            ],
            created_at=datetime.now(UTC),
        )

    def test_full_workflow_lifecycle(self):
        """Test complete workflow: start -> advance through all steps -> complete."""
        engine = WorkflowEngine()
        definition = self._make_linear_definition()

        # Start
        execution = engine.start_workflow(definition)
        assert execution.status == WorkflowStatus.RUNNING
        assert len(execution.steps_remaining) == 3

        # Advance step-1
        execution = engine.advance_workflow(execution)
        assert "step-1" in execution.steps_completed
        assert len(execution.steps_remaining) == 2

        # Advance step-2
        execution = engine.advance_workflow(execution)
        assert "step-2" in execution.steps_completed

        # Advance step-3 (completes workflow)
        execution = engine.advance_workflow(execution)
        assert execution.status == WorkflowStatus.COMPLETED
        assert len(execution.steps_completed) == 3
        assert len(execution.steps_remaining) == 0
        assert execution.completed_at is not None

    def test_workflow_failure_stops_execution(self):
        """Test that a failing step with on_failure='stop' halts the workflow."""
        definition = WorkflowDefinition(
            workflow_id="fail-wf",
            name="Failing Workflow",
            description="A workflow that fails",
            steps=[
                WorkflowStep(
                    step_id="good-step",
                    name="Good Step",
                    agent_domain=AgentDomain.CMDB,
                ),
                WorkflowStep(
                    step_id="bad-step",
                    name="Bad Step",
                    agent_domain=AgentDomain.DISCOVERY,
                    on_failure="stop",
                    depends_on=["good-step"],
                ),
                WorkflowStep(
                    step_id="unreached",
                    name="Unreached Step",
                    agent_domain=AgentDomain.ASSET,
                    depends_on=["bad-step"],
                ),
            ],
            created_at=datetime.now(UTC),
        )

        class FailOnBadStep(WorkflowEngine):
            def _execute_step(self, step, execution):
                if step.step_id == "bad-step":
                    raise RuntimeError("Simulated failure")
                return super()._execute_step(step, execution)

        engine = FailOnBadStep()
        execution = engine.start_workflow(definition)

        # Advance good-step
        execution = engine.advance_workflow(execution)
        assert "good-step" in execution.steps_completed

        # Advance bad-step (should fail)
        with pytest.raises(WorkflowStepFailedError):
            engine.advance_workflow(execution)

        assert execution.status == WorkflowStatus.FAILED
        assert "unreached" in execution.steps_remaining

    def test_workflow_from_template(self):
        """Test instantiating and executing a workflow from a template."""
        registry = get_default_registry()
        engine = WorkflowEngine()

        definition = registry.instantiate(
            "cmdb-health-check",
            parameters={"scope": "all"},
        )

        execution = engine.start_workflow(definition)
        assert execution.status == WorkflowStatus.RUNNING

        # Run through all steps
        while execution.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
            execution = engine.advance_workflow(execution)

        assert execution.status == WorkflowStatus.COMPLETED
        assert len(execution.steps_completed) == 3


@pytest.mark.integration
class TestWorkflowCheckpointing:
    """Tests for checkpoint save/reload/resume."""

    def test_checkpoint_and_resume(self, tmp_path):
        """Test saving a checkpoint and resuming execution."""
        engine = WorkflowEngine()
        checkpointer = WorkflowCheckpointer(tmp_path)

        definition = WorkflowDefinition(
            workflow_id="checkpoint-wf",
            name="Checkpoint Test",
            description="Tests checkpointing",
            steps=[
                WorkflowStep(
                    step_id="s1",
                    name="Step 1",
                    agent_domain=AgentDomain.CMDB,
                ),
                WorkflowStep(
                    step_id="s2",
                    name="Step 2",
                    agent_domain=AgentDomain.DISCOVERY,
                    depends_on=["s1"],
                ),
            ],
            created_at=datetime.now(UTC),
        )

        # Start and advance one step
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)
        assert "s1" in execution.steps_completed

        # Checkpoint
        checkpointer.save(execution)

        # Load checkpoint
        loaded = checkpointer.load(execution.execution_id)
        assert loaded is not None
        assert loaded.execution_id == execution.execution_id
        assert "s1" in loaded.steps_completed
        assert loaded.status == WorkflowStatus.STEP_COMPLETED

    def test_list_and_delete_checkpoints(self, tmp_path):
        """Test listing and deleting checkpoints."""
        checkpointer = WorkflowCheckpointer(tmp_path)
        engine = WorkflowEngine()

        definition = WorkflowDefinition(
            workflow_id="list-wf",
            name="List Test",
            description="test",
            steps=[
                WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB),
            ],
            created_at=datetime.now(UTC),
        )

        e1 = engine.start_workflow(definition)
        e2 = engine.start_workflow(definition)

        checkpointer.save(e1)
        checkpointer.save(e2)

        checkpoints = checkpointer.list_checkpoints()
        assert len(checkpoints) == 2

        checkpointer.delete(e1.execution_id)
        checkpoints = checkpointer.list_checkpoints()
        assert len(checkpoints) == 1


@pytest.mark.integration
class TestWorkflowStateTransitions:
    """Tests for workflow state machine transitions."""

    def test_pending_to_running(self):
        engine = WorkflowEngine()
        definition = WorkflowDefinition(
            workflow_id="state-wf",
            name="State Test",
            description="test",
            steps=[
                WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB),
            ],
            created_at=datetime.now(UTC),
        )
        execution = engine.start_workflow(definition)
        assert execution.status == WorkflowStatus.RUNNING

    def test_running_to_completed(self):
        engine = WorkflowEngine()
        definition = WorkflowDefinition(
            workflow_id="state-wf",
            name="State Test",
            description="test",
            steps=[
                WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB),
            ],
            created_at=datetime.now(UTC),
        )
        execution = engine.start_workflow(definition)
        execution = engine.advance_workflow(execution)
        assert execution.status == WorkflowStatus.COMPLETED

    def test_running_to_cancelled(self):
        engine = WorkflowEngine()
        definition = WorkflowDefinition(
            workflow_id="state-wf",
            name="State Test",
            description="test",
            steps=[
                WorkflowStep(step_id="s1", name="S1", agent_domain=AgentDomain.CMDB),
            ],
            created_at=datetime.now(UTC),
        )
        execution = engine.start_workflow(definition)
        execution = engine.cancel_workflow(execution.execution_id)
        assert execution.status == WorkflowStatus.CANCELLED
