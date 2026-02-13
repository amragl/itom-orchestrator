"""
Task Executor for the ITOM Orchestrator.

Dispatches routed tasks to agents, manages execution lifecycle with
timeout handling, retry with exponential backoff, and execution history.

This module implements ORCH-009: Task execution and result handling.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from itom_orchestrator.error_codes import (
    ORCH_7001_TASK_EXECUTION_FAILED,
    ORCH_7002_TASK_TIMEOUT,
    ORCH_7004_TASK_RETRY_EXHAUSTED,
)
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.models.tasks import Task, TaskResult, TaskStatus
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.router import RoutingDecision, TaskRouter

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

# Persistence key for execution history
EXECUTION_HISTORY_KEY = "execution-history"


class ExecutionError(Exception):
    """Base exception for task execution failures.

    Attributes:
        error_code: Machine-readable error code from error_codes.py.
        message: Human-readable error description.
        task_id: The task that failed.
    """

    def __init__(self, error_code: str, message: str, task_id: str) -> None:
        self.error_code = error_code
        self.message = message
        self.task_id = task_id
        super().__init__(f"[{error_code}] Task '{task_id}': {message}")


class TaskExecutionFailedError(ExecutionError):
    """Raised when a task execution fails."""

    def __init__(self, task_id: str, reason: str) -> None:
        super().__init__(
            ORCH_7001_TASK_EXECUTION_FAILED,
            f"Execution failed: {reason}",
            task_id,
        )


class TaskTimeoutError(ExecutionError):
    """Raised when a task exceeds its timeout."""

    def __init__(self, task_id: str, timeout_seconds: float) -> None:
        super().__init__(
            ORCH_7002_TASK_TIMEOUT,
            f"Timed out after {timeout_seconds}s",
            task_id,
        )


class TaskRetryExhaustedError(ExecutionError):
    """Raised when all retry attempts for a task are exhausted."""

    def __init__(self, task_id: str, attempts: int, last_error: str) -> None:
        super().__init__(
            ORCH_7004_TASK_RETRY_EXHAUSTED,
            f"All {attempts} retry attempts exhausted. Last error: {last_error}",
            task_id,
        )


class ExecutionRecord:
    """Record of a single task execution attempt.

    Captures timing, routing, result, and retry information for audit
    and history purposes.

    Attributes:
        task_id: The executed task ID.
        agent_id: The agent that handled the task.
        attempt: Which attempt number this was (1-based).
        status: Terminal task status.
        started_at: When execution began.
        completed_at: When execution finished.
        duration_seconds: Wall-clock execution time.
        routing_method: How the task was routed.
        error_message: Error description if failed.
        result_data: Output data if successful.
    """

    def __init__(
        self,
        task_id: str,
        agent_id: str,
        attempt: int,
        status: TaskStatus,
        started_at: datetime,
        completed_at: datetime,
        duration_seconds: float,
        routing_method: str,
        error_message: str | None = None,
        result_data: dict[str, Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.agent_id = agent_id
        self.attempt = attempt
        self.status = status
        self.started_at = started_at
        self.completed_at = completed_at
        self.duration_seconds = duration_seconds
        self.routing_method = routing_method
        self.error_message = error_message
        self.result_data = result_data or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "attempt": self.attempt,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "routing_method": self.routing_method,
            "error_message": self.error_message,
            "result_summary": {
                "has_data": bool(self.result_data),
                "keys": list(self.result_data.keys()) if self.result_data else [],
            },
        }


class ExecutorConfig:
    """Configuration for the TaskExecutor.

    Attributes:
        default_timeout_seconds: Default timeout if not specified by the task.
        retry_base_delay_seconds: Base delay for exponential backoff.
        retry_max_delay_seconds: Maximum delay cap for backoff.
        retry_backoff_factor: Multiplier for each retry delay.
        max_history_records: Maximum execution records to keep in memory.
    """

    def __init__(
        self,
        default_timeout_seconds: float = 300.0,
        retry_base_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 60.0,
        retry_backoff_factor: float = 2.0,
        max_history_records: int = 500,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.retry_backoff_factor = retry_backoff_factor
        self.max_history_records = max_history_records


class TaskExecutor:
    """Dispatches routed tasks to agents with timeout and retry handling.

    The executor receives a Task and a RoutingDecision, then manages the
    execution lifecycle:
    1. Marks the task as EXECUTING
    2. Dispatches to the target agent
    3. Monitors for timeout
    4. Retries on failure with exponential backoff
    5. Records the execution in history

    Since agents may be MCP servers, local tools, or remote services, the
    executor uses a pluggable dispatch mechanism. The default dispatch
    simulates execution for orchestrator-level task tracking. Real dispatch
    to MCP agents will be connected when agent endpoints are available.

    Args:
        router: The TaskRouter for task routing.
        persistence: StatePersistence for saving execution history.
        config: Executor configuration. If None, uses defaults.
    """

    def __init__(
        self,
        router: TaskRouter,
        persistence: StatePersistence,
        config: ExecutorConfig | None = None,
    ) -> None:
        self._router = router
        self._persistence = persistence
        self._config = config or ExecutorConfig()
        self._history: list[ExecutionRecord] = []
        self._active_tasks: dict[str, Task] = {}
        self._load_history()

    def _load_history(self) -> None:
        """Load execution history from persistence."""
        data = self._persistence.load(EXECUTION_HISTORY_KEY)
        if data is None:
            self._history = []
            return

        try:
            records_data = data.get("records", [])
            for record_dict in records_data:
                record = ExecutionRecord(
                    task_id=record_dict["task_id"],
                    agent_id=record_dict["agent_id"],
                    attempt=record_dict["attempt"],
                    status=TaskStatus(record_dict["status"]),
                    started_at=datetime.fromisoformat(record_dict["started_at"]),
                    completed_at=datetime.fromisoformat(record_dict["completed_at"]),
                    duration_seconds=record_dict["duration_seconds"],
                    routing_method=record_dict["routing_method"],
                    error_message=record_dict.get("error_message"),
                )
                self._history.append(record)
            logger.info(
                "Execution history loaded",
                extra={"extra_data": {"record_count": len(self._history)}},
            )
        except Exception:
            logger.warning("Failed to parse execution history, starting fresh", exc_info=True)
            self._history = []

    def _save_history(self) -> None:
        """Persist execution history."""
        data = {
            "records": [r.to_dict() for r in self._history],
            "total_records": len(self._history),
            "last_updated": datetime.now(UTC).isoformat(),
        }
        try:
            self._persistence.save(EXECUTION_HISTORY_KEY, data)
        except OSError:
            logger.error("Failed to save execution history", exc_info=True)

    def _append_record(self, record: ExecutionRecord) -> None:
        """Add an execution record and enforce history size limit."""
        self._history.append(record)
        if len(self._history) > self._config.max_history_records:
            excess = len(self._history) - self._config.max_history_records
            self._history = self._history[excess:]

    def execute(self, task: Task, routing_decision: RoutingDecision) -> TaskResult:
        """Execute a task synchronously with timeout and retry handling.

        Routes the task to the selected agent, manages the execution
        lifecycle, and records the result in history.

        Args:
            task: The task to execute.
            routing_decision: The routing decision from the TaskRouter.

        Returns:
            TaskResult with the execution outcome.

        Raises:
            TaskTimeoutError: If the task exceeds its timeout.
            TaskRetryExhaustedError: If all retry attempts fail.
        """
        agent_id = routing_decision.agent.agent_id
        timeout = task.timeout_seconds or self._config.default_timeout_seconds
        max_attempts = task.max_retries + 1  # 1 original + N retries

        self._active_tasks[task.task_id] = task
        last_error = ""

        try:
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    "Executing task",
                    extra={
                        "extra_data": {
                            "task_id": task.task_id,
                            "agent_id": agent_id,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "timeout": timeout,
                        }
                    },
                )

                started_at = datetime.now(UTC)
                start_time = time.monotonic()

                try:
                    # Dispatch to agent with timeout
                    result_data = self._dispatch_with_timeout(
                        task=task,
                        agent_id=agent_id,
                        timeout_seconds=timeout,
                    )

                    elapsed = time.monotonic() - start_time
                    completed_at = datetime.now(UTC)

                    # Success
                    result = TaskResult(
                        task_id=task.task_id,
                        agent_id=agent_id,
                        status=TaskStatus.COMPLETED,
                        result_data=result_data,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=elapsed,
                    )

                    record = ExecutionRecord(
                        task_id=task.task_id,
                        agent_id=agent_id,
                        attempt=attempt,
                        status=TaskStatus.COMPLETED,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=elapsed,
                        routing_method=routing_decision.method,
                        result_data=result_data,
                    )
                    self._append_record(record)
                    self._save_history()

                    logger.info(
                        "Task completed successfully",
                        extra={
                            "extra_data": {
                                "task_id": task.task_id,
                                "agent_id": agent_id,
                                "duration_seconds": round(elapsed, 3),
                                "attempt": attempt,
                            }
                        },
                    )

                    return result

                except TimeoutError:
                    elapsed = time.monotonic() - start_time
                    completed_at = datetime.now(UTC)
                    last_error = f"Timed out after {timeout}s"

                    record = ExecutionRecord(
                        task_id=task.task_id,
                        agent_id=agent_id,
                        attempt=attempt,
                        status=TaskStatus.TIMED_OUT,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=elapsed,
                        routing_method=routing_decision.method,
                        error_message=last_error,
                    )
                    self._append_record(record)
                    self._save_history()

                    logger.warning(
                        "Task timed out",
                        extra={
                            "extra_data": {
                                "task_id": task.task_id,
                                "timeout": timeout,
                                "attempt": attempt,
                            }
                        },
                    )

                    if attempt == max_attempts:
                        raise TaskTimeoutError(task.task_id, timeout)

                except Exception as exc:
                    elapsed = time.monotonic() - start_time
                    completed_at = datetime.now(UTC)
                    last_error = str(exc)

                    record = ExecutionRecord(
                        task_id=task.task_id,
                        agent_id=agent_id,
                        attempt=attempt,
                        status=TaskStatus.FAILED,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=elapsed,
                        routing_method=routing_decision.method,
                        error_message=last_error,
                    )
                    self._append_record(record)
                    self._save_history()

                    logger.warning(
                        "Task execution failed",
                        extra={
                            "extra_data": {
                                "task_id": task.task_id,
                                "error": last_error,
                                "attempt": attempt,
                            }
                        },
                    )

                    if attempt == max_attempts:
                        raise TaskRetryExhaustedError(
                            task.task_id, max_attempts, last_error
                        )

                # Exponential backoff before retry
                delay = self._calculate_backoff(attempt)
                logger.info(
                    "Retrying task",
                    extra={
                        "extra_data": {
                            "task_id": task.task_id,
                            "next_attempt": attempt + 1,
                            "backoff_seconds": round(delay, 2),
                        }
                    },
                )
                time.sleep(delay)

        finally:
            self._active_tasks.pop(task.task_id, None)

        # Should not reach here, but just in case
        raise TaskRetryExhaustedError(task.task_id, max_attempts, last_error)

    def _dispatch_with_timeout(
        self,
        task: Task,
        agent_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Dispatch a task to an agent with timeout enforcement.

        Currently implements local execution tracking. When agents run as
        MCP servers, this will use the MCP client to call the agent's tools.

        Args:
            task: The task to dispatch.
            agent_id: The target agent ID.
            timeout_seconds: Maximum execution time.

        Returns:
            Dictionary with execution result data.

        Raises:
            TimeoutError: If execution exceeds the timeout.
            Exception: If the agent returns an error.
        """
        start = time.monotonic()

        # Dispatch to the registered handler (if any)
        handler = self._dispatch_handlers.get(agent_id)
        if handler is not None:
            # Call the handler with timeout enforcement
            result = handler(task)
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                raise TimeoutError(f"Execution took {elapsed:.1f}s, exceeding timeout of {timeout_seconds}s")
            return result

        # Default dispatch: record the task was dispatched and return acknowledgment
        # This is the orchestrator's execution tracking layer. The actual agent
        # invocation will be connected when MCP client transport is available.
        return {
            "dispatched_to": agent_id,
            "task_id": task.task_id,
            "task_title": task.title,
            "domain": task.domain.value if task.domain else None,
            "acknowledged": True,
            "dispatch_timestamp": datetime.now(UTC).isoformat(),
        }

    # Pluggable dispatch handlers for testing and future agent integration
    _dispatch_handlers: dict[str, Any] = {}

    @classmethod
    def register_dispatch_handler(
        cls, agent_id: str, handler: Any
    ) -> None:
        """Register a dispatch handler for a specific agent.

        Handlers are called with a Task and should return a result dict
        or raise an exception.

        Args:
            agent_id: The agent ID to register the handler for.
            handler: Callable(Task) -> dict[str, Any].
        """
        cls._dispatch_handlers[agent_id] = handler

    @classmethod
    def clear_dispatch_handlers(cls) -> None:
        """Remove all registered dispatch handlers."""
        cls._dispatch_handlers.clear()

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay for a retry attempt.

        Args:
            attempt: The attempt number that just failed (1-based).

        Returns:
            Delay in seconds before the next retry.
        """
        delay = self._config.retry_base_delay_seconds * (
            self._config.retry_backoff_factor ** (attempt - 1)
        )
        return min(delay, self._config.retry_max_delay_seconds)

    def get_execution_history(
        self, task_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return execution history records.

        Args:
            task_id: If provided, filter to records for this task only.
            limit: Maximum number of records to return (most recent first).

        Returns:
            List of execution record dictionaries, newest first.
        """
        if task_id:
            records = [r for r in self._history if r.task_id == task_id]
        else:
            records = self._history

        recent = records[-limit:] if limit < len(records) else records
        return [r.to_dict() for r in reversed(recent)]

    def get_active_tasks(self) -> dict[str, dict[str, Any]]:
        """Return currently executing tasks.

        Returns:
            Dictionary mapping task_id to task summary.
        """
        result: dict[str, dict[str, Any]] = {}
        for task_id, task in self._active_tasks.items():
            result[task_id] = {
                "task_id": task.task_id,
                "title": task.title,
                "domain": task.domain.value if task.domain else None,
                "priority": task.priority.value,
                "status": task.status.value,
            }
        return result

    def get_execution_stats(self) -> dict[str, Any]:
        """Return aggregate execution statistics.

        Returns:
            Dictionary with total_executions, success_rate, avg_duration,
            and status distribution.
        """
        if not self._history:
            return {
                "total_executions": 0,
                "success_rate": 0.0,
                "avg_duration_seconds": 0.0,
                "status_distribution": {},
                "active_tasks": len(self._active_tasks),
            }

        total = len(self._history)
        completed = sum(1 for r in self._history if r.status == TaskStatus.COMPLETED)
        avg_duration = sum(r.duration_seconds for r in self._history) / total

        distribution: dict[str, int] = {}
        for r in self._history:
            key = r.status.value
            distribution[key] = distribution.get(key, 0) + 1

        return {
            "total_executions": total,
            "success_rate": round((completed / total) * 100, 2) if total > 0 else 0.0,
            "avg_duration_seconds": round(avg_duration, 3),
            "status_distribution": distribution,
            "active_tasks": len(self._active_tasks),
        }

    @property
    def config(self) -> ExecutorConfig:
        """Return the current executor configuration."""
        return self._config
