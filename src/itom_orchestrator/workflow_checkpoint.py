"""
Workflow checkpointing for the ITOM Orchestrator.

Saves and restores workflow execution state to JSON files,
enabling workflow resumption after interruptions.

This module implements ORCH-013: Workflow Checkpointing.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.workflows import WorkflowExecution

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class WorkflowCheckpointer:
    """Saves and restores workflow execution state.

    Stores execution state as JSON files in a designated storage
    directory. Supports save, load, list, and delete operations.

    Args:
        storage_dir: Root directory for checkpoint files. Checkpoints
            are stored in ``storage_dir/workflows/{execution_id}.json``.
    """

    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir
        self._workflows_dir = storage_dir / "workflows"
        self._workflows_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "WorkflowCheckpointer initialized",
            extra={"extra_data": {"storage_dir": str(storage_dir)}},
        )

    def save(self, execution: WorkflowExecution) -> Path:
        """Save a workflow execution as a checkpoint.

        Uses atomic writes (write to temp file, then rename) to
        prevent corruption.

        Args:
            execution: The workflow execution to checkpoint.

        Returns:
            Path to the saved checkpoint file.

        Raises:
            OSError: If the file cannot be written.
        """
        target = self._workflows_dir / f"{execution.execution_id}.json"
        tmp = self._workflows_dir / f"{execution.execution_id}.json.tmp"

        data = {
            "execution": execution.model_dump(mode="json"),
            "checkpointed_at": datetime.now(UTC).isoformat(),
        }

        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.write("\n")
            os.replace(tmp, target)
        except OSError:
            if tmp.exists():
                tmp.unlink()
            logger.error(
                "Failed to save workflow checkpoint",
                extra={
                    "extra_data": {
                        "execution_id": execution.execution_id,
                        "path": str(target),
                    }
                },
                exc_info=True,
            )
            raise

        logger.info(
            "Workflow checkpoint saved",
            extra={
                "extra_data": {
                    "execution_id": execution.execution_id,
                    "status": execution.status.value,
                    "path": str(target),
                }
            },
        )
        return target

    def load(self, execution_id: str) -> WorkflowExecution | None:
        """Load a workflow execution from a checkpoint.

        Args:
            execution_id: The execution ID to load.

        Returns:
            The restored WorkflowExecution, or None if not found.
        """
        target = self._workflows_dir / f"{execution_id}.json"
        if not target.exists():
            logger.debug(
                "Checkpoint not found",
                extra={"extra_data": {"execution_id": execution_id}},
            )
            return None

        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.error(
                "Failed to load workflow checkpoint",
                extra={
                    "extra_data": {
                        "execution_id": execution_id,
                        "path": str(target),
                    }
                },
                exc_info=True,
            )
            return None

        try:
            execution = WorkflowExecution.model_validate(data["execution"])
        except Exception:
            logger.error(
                "Failed to parse workflow checkpoint",
                extra={"extra_data": {"execution_id": execution_id}},
                exc_info=True,
            )
            return None

        logger.info(
            "Workflow checkpoint loaded",
            extra={
                "extra_data": {
                    "execution_id": execution_id,
                    "status": execution.status.value,
                }
            },
        )
        return execution

    def list_checkpoints(self) -> list[str]:
        """List all available checkpoint execution IDs.

        Returns:
            Sorted list of execution IDs that have checkpoints.
        """
        checkpoints: list[str] = []
        for path in self._workflows_dir.iterdir():
            if path.is_file() and path.suffix == ".json" and not path.name.endswith(".tmp"):
                checkpoints.append(path.stem)
        return sorted(checkpoints)

    def delete(self, execution_id: str) -> bool:
        """Delete a checkpoint.

        Args:
            execution_id: The execution ID to delete.

        Returns:
            True if the checkpoint was deleted, False if not found.
        """
        target = self._workflows_dir / f"{execution_id}.json"
        if not target.exists():
            return False

        target.unlink()
        logger.info(
            "Workflow checkpoint deleted",
            extra={"extra_data": {"execution_id": execution_id}},
        )
        return True
