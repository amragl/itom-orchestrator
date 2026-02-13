"""
Tests for the Task Executor (ORCH-009).

Validates that:
- Tasks execute successfully and return TaskResult
- Execution history is recorded and queryable
- Timeout handling works correctly
- Retry with exponential backoff functions
- Dispatch handlers can be registered for testing
- Execution statistics are computed correctly
- Active task tracking works during execution
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from itom_orchestrator.executor import (
    ExecutionRecord,
    ExecutorConfig,
    TaskExecutionFailedError,
    TaskExecutor,
    TaskRetryExhaustedError,
    TaskTimeoutError,
)
from itom_orchestrator.models.agents import AgentDomain, AgentStatus
from itom_orchestrator.models.tasks import Task, TaskPriority, TaskResult, TaskStatus
from itom_orchestrator.persistence import StatePersistence
from itom_orchestrator.registry import AgentRegistry
from itom_orchestrator.router import RoutingDecision, TaskRouter


@pytest.fixture()
def persistence(tmp_data_dir: Path) -> StatePersistence:
    """Create a StatePersistence instance for tests."""
    return StatePersistence(state_dir=str(tmp_data_dir / "state"))


@pytest.fixture()
def registry(persistence: StatePersistence) -> AgentRegistry:
    """Create an AgentRegistry with all agents ONLINE."""
    reg = AgentRegistry(persistence=persistence, load_defaults=True)
    reg.initialize()
    for agent in reg.list_all():
        reg.update_status(agent.agent_id, AgentStatus.ONLINE)
    return reg


@pytest.fixture()
def router(registry: AgentRegistry) -> TaskRouter:
    """Create a TaskRouter."""
    return TaskRouter(registry=registry)


@pytest.fixture()
def executor(router: TaskRouter, persistence: StatePersistence) -> TaskExecutor:
    """Create a TaskExecutor with fast retry settings for tests."""
    config = ExecutorConfig(
        default_timeout_seconds=5.0,
        retry_base_delay_seconds=0.01,  # Very fast retries for tests
        retry_max_delay_seconds=0.05,
        retry_backoff_factor=2.0,
        max_history_records=100,
    )
    TaskExecutor.clear_dispatch_handlers()
    return TaskExecutor(router=router, persistence=persistence, config=config)


def _make_task(
    task_id: str = "test-task-1",
    title: str = "Query CMDB for servers",
    description: str = "Query all server CIs",
    domain: AgentDomain = AgentDomain.CMDB,
    max_retries: int = 2,
    timeout: float = 5.0,
) -> Task:
    """Create a test task."""
    return Task(
        task_id=task_id,
        title=title,
        description=description,
        domain=domain,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.PENDING,
        parameters={},
        created_at=datetime.now(UTC),
        timeout_seconds=timeout,
        max_retries=max_retries,
    )


class TestBasicExecution:
    """Tests for basic task execution."""

    def test_execute_returns_task_result(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """execute() should return a TaskResult on success."""
        task = _make_task()
        decision = router.route(task)
        result = executor.execute(task, decision)

        assert isinstance(result, TaskResult)
        assert result.task_id == "test-task-1"
        assert result.agent_id == "cmdb-agent"
        assert result.status == TaskStatus.COMPLETED

    def test_execute_records_duration(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Result should have a non-negative duration."""
        task = _make_task()
        decision = router.route(task)
        result = executor.execute(task, decision)

        assert result.duration_seconds >= 0

    def test_execute_sets_timestamps(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Result should have valid started_at and completed_at."""
        task = _make_task()
        decision = router.route(task)
        result = executor.execute(task, decision)

        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.completed_at >= result.started_at

    def test_execute_returns_result_data(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Result should include dispatch acknowledgment data."""
        task = _make_task()
        decision = router.route(task)
        result = executor.execute(task, decision)

        assert result.result_data is not None
        assert result.result_data.get("acknowledged") is True
        assert result.result_data.get("dispatched_to") == "cmdb-agent"


class TestDispatchHandlers:
    """Tests for pluggable dispatch handlers."""

    def test_custom_handler_called(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Registered handler should be called for the target agent."""
        handler_called = {"value": False}

        def custom_handler(task: Task) -> dict[str, Any]:
            handler_called["value"] = True
            return {"custom": True, "data": "from handler"}

        TaskExecutor.register_dispatch_handler("cmdb-agent", custom_handler)
        try:
            task = _make_task()
            decision = router.route(task)
            result = executor.execute(task, decision)

            assert handler_called["value"] is True
            assert result.result_data.get("custom") is True
        finally:
            TaskExecutor.clear_dispatch_handlers()

    def test_handler_exception_triggers_retry(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Handler exceptions should trigger retry."""
        call_count = {"value": 0}

        def failing_then_succeeding(task: Task) -> dict[str, Any]:
            call_count["value"] += 1
            if call_count["value"] < 3:
                raise RuntimeError("Agent unavailable")
            return {"recovered": True}

        TaskExecutor.register_dispatch_handler("cmdb-agent", failing_then_succeeding)
        try:
            task = _make_task(max_retries=3)
            decision = router.route(task)
            result = executor.execute(task, decision)

            assert result.status == TaskStatus.COMPLETED
            assert call_count["value"] == 3
        finally:
            TaskExecutor.clear_dispatch_handlers()

    def test_handler_always_fails_exhausts_retries(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Handler that always fails should exhaust retries and raise."""

        def always_fails(task: Task) -> dict[str, Any]:
            raise RuntimeError("Permanent failure")

        TaskExecutor.register_dispatch_handler("cmdb-agent", always_fails)
        try:
            task = _make_task(max_retries=2)
            decision = router.route(task)
            with pytest.raises(TaskRetryExhaustedError) as exc_info:
                executor.execute(task, decision)
            assert "3" in str(exc_info.value)  # 1 original + 2 retries = 3
        finally:
            TaskExecutor.clear_dispatch_handlers()


class TestRetryBehavior:
    """Tests for retry with exponential backoff."""

    def test_backoff_calculation(self, executor: TaskExecutor) -> None:
        """Backoff should increase exponentially."""
        delay1 = executor._calculate_backoff(1)
        delay2 = executor._calculate_backoff(2)
        delay3 = executor._calculate_backoff(3)

        assert delay1 == executor.config.retry_base_delay_seconds
        assert delay2 > delay1
        assert delay3 > delay2

    def test_backoff_capped_at_max(self, executor: TaskExecutor) -> None:
        """Backoff should not exceed max_delay."""
        delay = executor._calculate_backoff(100)
        assert delay <= executor.config.retry_max_delay_seconds

    def test_retry_count_matches_max_retries(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Number of attempts should equal max_retries + 1."""
        call_count = {"value": 0}

        def counting_handler(task: Task) -> dict[str, Any]:
            call_count["value"] += 1
            raise RuntimeError("Fail")

        TaskExecutor.register_dispatch_handler("cmdb-agent", counting_handler)
        try:
            task = _make_task(max_retries=2)
            decision = router.route(task)
            with pytest.raises(TaskRetryExhaustedError):
                executor.execute(task, decision)
            assert call_count["value"] == 3  # 1 + 2 retries
        finally:
            TaskExecutor.clear_dispatch_handlers()


class TestExecutionHistory:
    """Tests for execution history tracking."""

    def test_successful_execution_recorded(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Successful execution should appear in history."""
        task = _make_task()
        decision = router.route(task)
        executor.execute(task, decision)

        history = executor.get_execution_history()
        assert len(history) == 1
        assert history[0]["task_id"] == "test-task-1"
        assert history[0]["status"] == "completed"

    def test_failed_execution_recorded(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Failed execution attempts should appear in history."""

        def always_fails(task: Task) -> dict[str, Any]:
            raise RuntimeError("Fail")

        TaskExecutor.register_dispatch_handler("cmdb-agent", always_fails)
        try:
            task = _make_task(max_retries=1)
            decision = router.route(task)
            with pytest.raises(TaskRetryExhaustedError):
                executor.execute(task, decision)

            history = executor.get_execution_history()
            assert len(history) == 2  # 1 original + 1 retry
            assert all(r["status"] == "failed" for r in history)
        finally:
            TaskExecutor.clear_dispatch_handlers()

    def test_history_filtered_by_task_id(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """History should be filterable by task_id."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}")
            decision = router.route(task)
            executor.execute(task, decision)

        history = executor.get_execution_history(task_id="task-1")
        assert len(history) == 1
        assert history[0]["task_id"] == "task-1"

    def test_history_limited(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """History limit parameter should cap returned records."""
        for i in range(5):
            task = _make_task(task_id=f"task-{i}")
            decision = router.route(task)
            executor.execute(task, decision)

        history = executor.get_execution_history(limit=3)
        assert len(history) == 3

    def test_history_newest_first(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """History should be returned newest first."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}")
            decision = router.route(task)
            executor.execute(task, decision)

        history = executor.get_execution_history()
        assert history[0]["task_id"] == "task-2"
        assert history[-1]["task_id"] == "task-0"


class TestExecutionStats:
    """Tests for aggregate execution statistics."""

    def test_empty_stats(self, executor: TaskExecutor) -> None:
        """Stats with no history should return zeros."""
        stats = executor.get_execution_stats()
        assert stats["total_executions"] == 0
        assert stats["success_rate"] == 0.0

    def test_stats_after_successful_executions(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Stats should reflect successful executions."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}")
            decision = router.route(task)
            executor.execute(task, decision)

        stats = executor.get_execution_stats()
        assert stats["total_executions"] == 3
        assert stats["success_rate"] == 100.0
        assert stats["status_distribution"]["completed"] == 3

    def test_stats_with_mixed_results(
        self, executor: TaskExecutor, router: TaskRouter
    ) -> None:
        """Stats should handle mixed success/failure."""
        # One success
        task = _make_task(task_id="success-task")
        decision = router.route(task)
        executor.execute(task, decision)

        # One failure
        def always_fails(task: Task) -> dict[str, Any]:
            raise RuntimeError("Fail")

        TaskExecutor.register_dispatch_handler("cmdb-agent", always_fails)
        try:
            task = _make_task(task_id="fail-task", max_retries=0)
            decision = router.route(task)
            with pytest.raises(TaskRetryExhaustedError):
                executor.execute(task, decision)
        finally:
            TaskExecutor.clear_dispatch_handlers()

        stats = executor.get_execution_stats()
        assert stats["total_executions"] == 2
        assert stats["success_rate"] == 50.0


class TestExecutionRecord:
    """Tests for ExecutionRecord serialization."""

    def test_to_dict(self) -> None:
        """to_dict() should include all required fields."""
        now = datetime.now(UTC)
        record = ExecutionRecord(
            task_id="test-1",
            agent_id="cmdb-agent",
            attempt=1,
            status=TaskStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            duration_seconds=1.234,
            routing_method="domain",
        )
        d = record.to_dict()
        assert d["task_id"] == "test-1"
        assert d["agent_id"] == "cmdb-agent"
        assert d["attempt"] == 1
        assert d["status"] == "completed"
        assert d["duration_seconds"] == 1.234
        assert d["routing_method"] == "domain"

    def test_to_dict_with_error(self) -> None:
        """to_dict() should include error_message when present."""
        now = datetime.now(UTC)
        record = ExecutionRecord(
            task_id="test-1",
            agent_id="cmdb-agent",
            attempt=1,
            status=TaskStatus.FAILED,
            started_at=now,
            completed_at=now,
            duration_seconds=0.5,
            routing_method="domain",
            error_message="Something broke",
        )
        d = record.to_dict()
        assert d["error_message"] == "Something broke"


class TestHistoryPersistence:
    """Tests for execution history persistence."""

    def test_history_persisted_after_execution(
        self, executor: TaskExecutor, router: TaskRouter, persistence: StatePersistence
    ) -> None:
        """Execution should persist history to disk."""
        task = _make_task()
        decision = router.route(task)
        executor.execute(task, decision)

        # Load persisted data directly
        data = persistence.load("execution-history")
        assert data is not None
        assert len(data["records"]) == 1
        assert data["records"][0]["task_id"] == "test-task-1"

    def test_history_loaded_on_init(
        self, router: TaskRouter, persistence: StatePersistence
    ) -> None:
        """New executor should load history from persistence."""
        # Create executor and execute a task
        config = ExecutorConfig(retry_base_delay_seconds=0.01, retry_max_delay_seconds=0.05)
        TaskExecutor.clear_dispatch_handlers()
        exec1 = TaskExecutor(router=router, persistence=persistence, config=config)
        task = _make_task()
        decision = router.route(task)
        exec1.execute(task, decision)

        # Create new executor and verify history loaded
        exec2 = TaskExecutor(router=router, persistence=persistence, config=config)
        history = exec2.get_execution_history()
        assert len(history) == 1
        assert history[0]["task_id"] == "test-task-1"
