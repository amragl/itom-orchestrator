"""
Tests for workflow checkpointing (ORCH-013).
"""

from datetime import UTC, datetime

import pytest

from itom_orchestrator.models.workflows import WorkflowExecution, WorkflowStatus
from itom_orchestrator.workflow_checkpoint import WorkflowCheckpointer


def _make_execution(execution_id="exec-1", status=WorkflowStatus.RUNNING):
    """Helper to create a WorkflowExecution for tests."""
    return WorkflowExecution(
        execution_id=execution_id,
        workflow_id="wf-1",
        status=status,
        steps_remaining=["step-1", "step-2"],
        started_at=datetime.now(UTC),
    )


class TestWorkflowCheckpointer:
    """Tests for checkpoint save/load/list/delete."""

    def test_save_and_load(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        execution = _make_execution("exec-save")

        path = checkpointer.save(execution)
        assert path.exists()

        loaded = checkpointer.load("exec-save")
        assert loaded is not None
        assert loaded.execution_id == "exec-save"
        assert loaded.workflow_id == "wf-1"
        assert loaded.status == WorkflowStatus.RUNNING

    def test_load_missing_returns_none(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        assert checkpointer.load("nonexistent") is None

    def test_list_checkpoints(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        checkpointer.save(_make_execution("exec-1"))
        checkpointer.save(_make_execution("exec-2"))
        checkpointer.save(_make_execution("exec-3"))

        checkpoints = checkpointer.list_checkpoints()
        assert len(checkpoints) == 3
        assert checkpoints == ["exec-1", "exec-2", "exec-3"]

    def test_delete_checkpoint(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        checkpointer.save(_make_execution("exec-del"))

        assert checkpointer.delete("exec-del") is True
        assert checkpointer.load("exec-del") is None

    def test_delete_missing_returns_false(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        assert checkpointer.delete("nonexistent") is False

    def test_save_overwrites_existing(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)

        # Save initial
        exec1 = _make_execution("exec-ow", status=WorkflowStatus.RUNNING)
        checkpointer.save(exec1)

        # Save updated
        exec2 = _make_execution("exec-ow", status=WorkflowStatus.COMPLETED)
        checkpointer.save(exec2)

        loaded = checkpointer.load("exec-ow")
        assert loaded is not None
        assert loaded.status == WorkflowStatus.COMPLETED

    def test_creates_workflows_subdirectory(self, tmp_path):
        storage_dir = tmp_path / "deep" / "nested"
        checkpointer = WorkflowCheckpointer(storage_dir)

        assert (storage_dir / "workflows").exists()

    def test_load_corrupted_file_returns_none(self, tmp_path):
        checkpointer = WorkflowCheckpointer(tmp_path)
        corrupted_file = tmp_path / "workflows" / "bad.json"
        corrupted_file.write_text("{ invalid json }")

        assert checkpointer.load("bad") is None

    def test_preserves_step_results(self, tmp_path):
        from itom_orchestrator.models.tasks import TaskResult, TaskStatus

        checkpointer = WorkflowCheckpointer(tmp_path)
        execution = _make_execution("exec-results")
        execution.steps_completed = ["step-1"]
        execution.steps_remaining = ["step-2"]
        execution.step_results["step-1"] = TaskResult(
            task_id="step-1",
            agent_id="cmdb-agent",
            status=TaskStatus.COMPLETED,
            result_data={"key": "value"},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            duration_seconds=1.5,
        )

        checkpointer.save(execution)
        loaded = checkpointer.load("exec-results")

        assert loaded is not None
        assert "step-1" in loaded.step_results
        assert loaded.step_results["step-1"].result_data == {"key": "value"}
