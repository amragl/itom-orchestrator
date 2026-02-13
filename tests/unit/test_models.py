"""
Tests for itom_orchestrator.models -- all Pydantic model validation and serialization.

Covers: AgentRegistration, AgentCapability, AgentStatus, AgentDomain,
Task, TaskResult, TaskPriority, TaskStatus, WorkflowDefinition, WorkflowStep,
WorkflowExecution, WorkflowStatus, WorkflowStepType, AgentMessage, MessageType.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from itom_orchestrator.models import (  # noqa: I001
    AgentCapability,
    AgentDomain,
    AgentMessage,
    AgentRegistration,
    AgentStatus,
    MessageType,
    Task,
    TaskPriority,
    TaskResult,
    TaskStatus,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepType,
)

# ---------------------------------------------------------------------------
# Helpers -- reusable valid data factories
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 13, 10, 0, 0, tzinfo=UTC)


def _make_capability(**overrides: object) -> AgentCapability:
    """Create a valid AgentCapability with sensible defaults."""
    defaults = {
        "name": "query_cis",
        "domain": AgentDomain.CMDB,
        "description": "Query configuration items from the CMDB",
    }
    defaults.update(overrides)
    return AgentCapability(**defaults)


def _make_registration(**overrides: object) -> AgentRegistration:
    """Create a valid AgentRegistration with sensible defaults."""
    defaults: dict[str, object] = {
        "agent_id": "cmdb-agent",
        "name": "CMDB Agent",
        "description": "Manages ServiceNow CMDB operations",
        "domain": AgentDomain.CMDB,
        "capabilities": [_make_capability()],
        "registered_at": _NOW,
    }
    defaults.update(overrides)
    return AgentRegistration(**defaults)


def _make_task(**overrides: object) -> Task:
    """Create a valid Task with sensible defaults."""
    defaults: dict[str, object] = {
        "task_id": "task-001",
        "title": "Query all Linux servers",
        "description": "Retrieve all CI records with OS containing Linux",
        "domain": AgentDomain.CMDB,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Task(**defaults)


def _make_task_result(**overrides: object) -> TaskResult:
    """Create a valid TaskResult with sensible defaults."""
    defaults: dict[str, object] = {
        "task_id": "task-001",
        "agent_id": "cmdb-agent",
        "status": TaskStatus.COMPLETED,
        "started_at": _NOW,
        "completed_at": datetime(2026, 2, 13, 10, 1, 0, tzinfo=UTC),
        "duration_seconds": 60.0,
    }
    defaults.update(overrides)
    return TaskResult(**defaults)


def _make_workflow_step(**overrides: object) -> WorkflowStep:
    """Create a valid WorkflowStep with sensible defaults."""
    defaults: dict[str, object] = {
        "step_id": "step-1",
        "name": "Discover network devices",
    }
    defaults.update(overrides)
    return WorkflowStep(**defaults)


def _make_workflow_definition(**overrides: object) -> WorkflowDefinition:
    """Create a valid WorkflowDefinition with sensible defaults."""
    defaults: dict[str, object] = {
        "workflow_id": "full-discovery-scan",
        "name": "Full Discovery Scan",
        "description": "Run a complete discovery scan across all network segments",
        "steps": [_make_workflow_step()],
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return WorkflowDefinition(**defaults)


def _make_message(**overrides: object) -> AgentMessage:
    """Create a valid AgentMessage with sensible defaults."""
    defaults: dict[str, object] = {
        "message_id": "msg-001",
        "message_type": MessageType.REQUEST,
        "sender_agent": "cmdb-agent",
        "recipient_agent": "discovery-agent",
        "subject": "Trigger discovery for subnet 10.0.0.0/24",
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return AgentMessage(**defaults)


# ===================================================================
# Agent model tests
# ===================================================================


class TestAgentDomain:
    """Tests for AgentDomain enum."""

    def test_has_all_seven_domains(self) -> None:
        assert len(AgentDomain) == 7

    def test_domain_values(self) -> None:
        expected = {"cmdb", "discovery", "asset", "csa", "audit", "documentation", "orchestration"}
        actual = {d.value for d in AgentDomain}
        assert actual == expected

    def test_domain_from_string(self) -> None:
        assert AgentDomain("cmdb") == AgentDomain.CMDB
        assert AgentDomain("discovery") == AgentDomain.DISCOVERY


class TestAgentStatus:
    """Tests for AgentStatus enum."""

    def test_has_all_four_states(self) -> None:
        assert len(AgentStatus) == 4

    def test_status_values(self) -> None:
        expected = {"online", "offline", "degraded", "maintenance"}
        actual = {s.value for s in AgentStatus}
        assert actual == expected


class TestAgentCapability:
    """Tests for AgentCapability model."""

    def test_valid_capability(self) -> None:
        cap = _make_capability()
        assert cap.name == "query_cis"
        assert cap.domain == AgentDomain.CMDB
        assert cap.description == "Query configuration items from the CMDB"
        assert cap.input_schema is None
        assert cap.output_schema is None

    def test_capability_with_schemas(self) -> None:
        input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        output_schema = {"type": "array", "items": {"type": "object"}}
        cap = _make_capability(input_schema=input_schema, output_schema=output_schema)
        assert cap.input_schema == input_schema
        assert cap.output_schema == output_schema

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Capability name must not be empty"):
            _make_capability(name="")

    def test_whitespace_only_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Capability name must not be empty"):
            _make_capability(name="   ")

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Capability description must not be empty"):
            _make_capability(description="")


class TestAgentRegistration:
    """Tests for AgentRegistration model."""

    def test_valid_registration(self) -> None:
        reg = _make_registration()
        assert reg.agent_id == "cmdb-agent"
        assert reg.name == "CMDB Agent"
        assert reg.domain == AgentDomain.CMDB
        assert reg.status == AgentStatus.OFFLINE
        assert len(reg.capabilities) == 1
        assert reg.mcp_server_url is None
        assert reg.last_health_check is None
        assert reg.metadata == {}

    def test_registration_with_all_fields(self) -> None:
        health_check_time = datetime(2026, 2, 13, 10, 5, 0, tzinfo=UTC)
        reg = _make_registration(
            mcp_server_url="http://localhost:8001",
            status=AgentStatus.ONLINE,
            last_health_check=health_check_time,
            metadata={"version": "1.2.0"},
        )
        assert reg.mcp_server_url == "http://localhost:8001"
        assert reg.status == AgentStatus.ONLINE
        assert reg.last_health_check == health_check_time
        assert reg.metadata == {"version": "1.2.0"}

    def test_empty_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id must not be empty"):
            _make_registration(agent_id="")

    def test_uppercase_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id.*is invalid"):
            _make_registration(agent_id="CMDB-Agent")

    def test_agent_id_with_spaces_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id.*is invalid"):
            _make_registration(agent_id="cmdb agent")

    def test_agent_id_with_underscores_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id.*is invalid"):
            _make_registration(agent_id="cmdb_agent")

    def test_agent_id_starting_with_number_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id.*is invalid"):
            _make_registration(agent_id="1cmdb-agent")

    def test_agent_id_starting_with_hyphen_rejected(self) -> None:
        with pytest.raises(ValidationError, match="agent_id.*is invalid"):
            _make_registration(agent_id="-cmdb-agent")

    def test_valid_agent_ids(self) -> None:
        valid_ids = ["cmdb-agent", "discovery-agent", "a", "agent123", "my-cool-agent-v2"]
        for agent_id in valid_ids:
            reg = _make_registration(agent_id=agent_id)
            assert reg.agent_id == agent_id

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Agent name must not be empty"):
            _make_registration(name="")

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Agent description must not be empty"):
            _make_registration(description="")

    def test_json_serialization_roundtrip(self) -> None:
        reg = _make_registration()
        json_str = reg.model_dump_json()
        restored = AgentRegistration.model_validate_json(json_str)
        assert restored == reg

    def test_dict_serialization_roundtrip(self) -> None:
        reg = _make_registration()
        data = reg.model_dump()
        restored = AgentRegistration.model_validate(data)
        assert restored == reg


# ===================================================================
# Task model tests
# ===================================================================


class TestTaskPriority:
    """Tests for TaskPriority enum."""

    def test_has_all_four_levels(self) -> None:
        assert len(TaskPriority) == 4

    def test_priority_values(self) -> None:
        expected = {"critical", "high", "medium", "low"}
        actual = {p.value for p in TaskPriority}
        assert actual == expected


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_has_all_seven_states(self) -> None:
        assert len(TaskStatus) == 7

    def test_status_values(self) -> None:
        expected = {
            "pending",
            "routed",
            "executing",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }
        actual = {s.value for s in TaskStatus}
        assert actual == expected


class TestTask:
    """Tests for Task model."""

    def test_task_with_defaults(self) -> None:
        task = _make_task()
        assert task.task_id == "task-001"
        assert task.priority == TaskPriority.MEDIUM
        assert task.status == TaskStatus.PENDING
        assert task.parameters == {}
        assert task.timeout_seconds == 300.0
        assert task.retry_count == 0
        assert task.max_retries == 3
        assert task.metadata == {}

    def test_task_with_explicit_values(self) -> None:
        task = _make_task(
            priority=TaskPriority.CRITICAL,
            status=TaskStatus.EXECUTING,
            target_agent="cmdb-agent",
            parameters={"query": "os_contains=Linux"},
            timeout_seconds=600.0,
            retry_count=1,
            max_retries=5,
            metadata={"source": "workflow"},
        )
        assert task.priority == TaskPriority.CRITICAL
        assert task.status == TaskStatus.EXECUTING
        assert task.target_agent == "cmdb-agent"
        assert task.parameters == {"query": "os_contains=Linux"}
        assert task.timeout_seconds == 600.0
        assert task.retry_count == 1
        assert task.max_retries == 5

    def test_task_without_domain(self) -> None:
        task = _make_task(domain=None)
        assert task.domain is None

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id must not be empty"):
            _make_task(task_id="")

    def test_whitespace_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="task_id must not be empty"):
            _make_task(task_id="   ")

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds must be positive"):
            _make_task(timeout_seconds=0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds must be positive"):
            _make_task(timeout_seconds=-10.0)

    def test_retry_count_exceeding_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="retry_count.*must not exceed.*max_retries"):
            _make_task(retry_count=5, max_retries=3)

    def test_retry_count_at_max_allowed(self) -> None:
        task = _make_task(retry_count=3, max_retries=3)
        assert task.retry_count == 3

    def test_json_serialization_roundtrip(self) -> None:
        task = _make_task()
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored == task


class TestTaskResult:
    """Tests for TaskResult model."""

    def test_valid_completed_result(self) -> None:
        result = _make_task_result()
        assert result.status == TaskStatus.COMPLETED
        assert result.duration_seconds == 60.0
        assert result.error_message is None

    def test_valid_failed_result(self) -> None:
        result = _make_task_result(
            status=TaskStatus.FAILED,
            error_message="Agent connection refused",
        )
        assert result.status == TaskStatus.FAILED
        assert result.error_message == "Agent connection refused"

    def test_valid_timed_out_result(self) -> None:
        result = _make_task_result(status=TaskStatus.TIMED_OUT)
        assert result.status == TaskStatus.TIMED_OUT

    def test_pending_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="terminal state"):
            _make_task_result(status=TaskStatus.PENDING)

    def test_routed_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="terminal state"):
            _make_task_result(status=TaskStatus.ROUTED)

    def test_executing_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="terminal state"):
            _make_task_result(status=TaskStatus.EXECUTING)

    def test_cancelled_status_rejected(self) -> None:
        with pytest.raises(ValidationError, match="terminal state"):
            _make_task_result(status=TaskStatus.CANCELLED)

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duration_seconds must be >= 0"):
            _make_task_result(duration_seconds=-1.0)

    def test_zero_duration_allowed(self) -> None:
        result = _make_task_result(duration_seconds=0.0)
        assert result.duration_seconds == 0.0

    def test_result_with_data(self) -> None:
        result_data = {"ci_count": 42, "query": "Linux servers"}
        result = _make_task_result(result_data=result_data)
        assert result.result_data == result_data

    def test_json_serialization_roundtrip(self) -> None:
        result = _make_task_result(
            result_data={"count": 10},
            error_message=None,
        )
        json_str = result.model_dump_json()
        restored = TaskResult.model_validate_json(json_str)
        assert restored == result


# ===================================================================
# Workflow model tests
# ===================================================================


class TestWorkflowStepType:
    """Tests for WorkflowStepType enum."""

    def test_has_all_three_types(self) -> None:
        assert len(WorkflowStepType) == 3

    def test_step_type_values(self) -> None:
        expected = {"task", "conditional", "parallel"}
        actual = {t.value for t in WorkflowStepType}
        assert actual == expected


class TestWorkflowStatus:
    """Tests for WorkflowStatus enum."""

    def test_has_all_eight_states(self) -> None:
        assert len(WorkflowStatus) == 8

    def test_status_values(self) -> None:
        expected = {
            "pending",
            "running",
            "step_executing",
            "step_completed",
            "paused",
            "failed",
            "completed",
            "cancelled",
        }
        actual = {s.value for s in WorkflowStatus}
        assert actual == expected


class TestWorkflowStep:
    """Tests for WorkflowStep model."""

    def test_step_defaults(self) -> None:
        step = _make_workflow_step()
        assert step.step_id == "step-1"
        assert step.name == "Discover network devices"
        assert step.step_type == WorkflowStepType.TASK
        assert step.agent_domain is None
        assert step.target_agent is None
        assert step.parameters == {}
        assert step.depends_on == []
        assert step.timeout_seconds == 300.0
        assert step.on_failure == "stop"
        assert step.max_retries == 2

    def test_step_with_all_fields(self) -> None:
        step = _make_workflow_step(
            step_type=WorkflowStepType.PARALLEL,
            agent_domain=AgentDomain.DISCOVERY,
            target_agent="discovery-agent",
            parameters={"subnet": "10.0.0.0/24"},
            depends_on=["step-0"],
            timeout_seconds=600.0,
            on_failure="retry",
            max_retries=5,
        )
        assert step.step_type == WorkflowStepType.PARALLEL
        assert step.agent_domain == AgentDomain.DISCOVERY
        assert step.target_agent == "discovery-agent"
        assert step.on_failure == "retry"

    def test_empty_step_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="step_id must not be empty"):
            _make_workflow_step(step_id="")

    def test_invalid_on_failure_rejected(self) -> None:
        with pytest.raises(ValidationError, match="on_failure must be one of"):
            _make_workflow_step(on_failure="ignore")

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout_seconds must be positive"):
            _make_workflow_step(timeout_seconds=0)

    def test_valid_on_failure_values(self) -> None:
        for value in ("stop", "skip", "retry"):
            step = _make_workflow_step(on_failure=value)
            assert step.on_failure == value


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition model."""

    def test_valid_definition(self) -> None:
        defn = _make_workflow_definition()
        assert defn.workflow_id == "full-discovery-scan"
        assert defn.name == "Full Discovery Scan"
        assert defn.version == "1.0.0"
        assert len(defn.steps) == 1
        assert defn.metadata == {}

    def test_multi_step_definition(self) -> None:
        step1 = _make_workflow_step(step_id="discover", name="Run discovery")
        step2 = _make_workflow_step(
            step_id="audit",
            name="Audit results",
            depends_on=["discover"],
        )
        defn = _make_workflow_definition(steps=[step1, step2])
        assert len(defn.steps) == 2
        assert defn.steps[1].depends_on == ["discover"]

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one step"):
            _make_workflow_definition(steps=[])

    def test_empty_workflow_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="workflow_id must not be empty"):
            _make_workflow_definition(workflow_id="")

    def test_duplicate_step_ids_rejected(self) -> None:
        step1 = _make_workflow_step(step_id="same-id", name="First step")
        step2 = _make_workflow_step(step_id="same-id", name="Second step")
        with pytest.raises(ValidationError, match="Duplicate step_ids"):
            _make_workflow_definition(steps=[step1, step2])

    def test_invalid_depends_on_reference_rejected(self) -> None:
        step = _make_workflow_step(step_id="step-1", depends_on=["nonexistent-step"])
        with pytest.raises(ValidationError, match="depends on 'nonexistent-step'"):
            _make_workflow_definition(steps=[step])

    def test_self_dependency_rejected(self) -> None:
        step = _make_workflow_step(step_id="step-1", depends_on=["step-1"])
        with pytest.raises(ValidationError, match="depends on itself"):
            _make_workflow_definition(steps=[step])

    def test_valid_dependency_chain(self) -> None:
        step_a = _make_workflow_step(step_id="a", name="Step A")
        step_b = _make_workflow_step(step_id="b", name="Step B", depends_on=["a"])
        step_c = _make_workflow_step(step_id="c", name="Step C", depends_on=["a", "b"])
        defn = _make_workflow_definition(steps=[step_a, step_b, step_c])
        assert len(defn.steps) == 3

    def test_json_serialization_roundtrip(self) -> None:
        defn = _make_workflow_definition()
        json_str = defn.model_dump_json()
        restored = WorkflowDefinition.model_validate_json(json_str)
        assert restored == defn


class TestWorkflowExecution:
    """Tests for WorkflowExecution model."""

    def test_execution_defaults(self) -> None:
        execution = WorkflowExecution(
            execution_id="exec-001",
            workflow_id="full-discovery-scan",
        )
        assert execution.status == WorkflowStatus.PENDING
        assert execution.current_step_id is None
        assert execution.steps_completed == []
        assert execution.steps_remaining == []
        assert execution.step_results == {}
        assert execution.context == {}
        assert execution.started_at is None
        assert execution.completed_at is None
        assert execution.error_message is None
        assert execution.metadata == {}

    def test_execution_state_tracking(self) -> None:
        result = _make_task_result()
        execution = WorkflowExecution(
            execution_id="exec-002",
            workflow_id="full-discovery-scan",
            status=WorkflowStatus.STEP_EXECUTING,
            current_step_id="step-1",
            steps_completed=["step-0"],
            steps_remaining=["step-1", "step-2"],
            step_results={"step-0": result},
            context={"discovered_count": 15},
            started_at=_NOW,
            metadata={"triggered_by": "schedule"},
        )
        assert execution.status == WorkflowStatus.STEP_EXECUTING
        assert execution.current_step_id == "step-1"
        assert len(execution.steps_completed) == 1
        assert len(execution.steps_remaining) == 2
        assert "step-0" in execution.step_results
        assert execution.context["discovered_count"] == 15

    def test_empty_execution_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="execution_id must not be empty"):
            WorkflowExecution(execution_id="", workflow_id="test")

    def test_empty_workflow_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="workflow_id must not be empty"):
            WorkflowExecution(execution_id="exec-001", workflow_id="")

    def test_completed_execution(self) -> None:
        completed_at = datetime(2026, 2, 13, 11, 0, 0, tzinfo=UTC)
        execution = WorkflowExecution(
            execution_id="exec-003",
            workflow_id="full-discovery-scan",
            status=WorkflowStatus.COMPLETED,
            steps_completed=["step-1", "step-2"],
            steps_remaining=[],
            started_at=_NOW,
            completed_at=completed_at,
        )
        assert execution.status == WorkflowStatus.COMPLETED
        assert execution.completed_at == completed_at

    def test_failed_execution(self) -> None:
        execution = WorkflowExecution(
            execution_id="exec-004",
            workflow_id="full-discovery-scan",
            status=WorkflowStatus.FAILED,
            current_step_id="step-2",
            steps_completed=["step-1"],
            steps_remaining=["step-2", "step-3"],
            error_message="Discovery agent timed out on subnet scan",
            started_at=_NOW,
        )
        assert execution.status == WorkflowStatus.FAILED
        assert execution.error_message is not None

    def test_json_serialization_roundtrip(self) -> None:
        result = _make_task_result()
        execution = WorkflowExecution(
            execution_id="exec-005",
            workflow_id="full-discovery-scan",
            status=WorkflowStatus.RUNNING,
            step_results={"step-1": result},
            started_at=_NOW,
        )
        json_str = execution.model_dump_json()
        restored = WorkflowExecution.model_validate_json(json_str)
        assert restored == execution


# ===================================================================
# Message model tests
# ===================================================================


class TestMessageType:
    """Tests for MessageType enum."""

    def test_has_all_five_types(self) -> None:
        assert len(MessageType) == 5

    def test_message_type_values(self) -> None:
        expected = {"request", "response", "notification", "event", "error"}
        actual = {t.value for t in MessageType}
        assert actual == expected


class TestAgentMessage:
    """Tests for AgentMessage model."""

    def test_valid_message(self) -> None:
        msg = _make_message()
        assert msg.message_id == "msg-001"
        assert msg.message_type == MessageType.REQUEST
        assert msg.sender_agent == "cmdb-agent"
        assert msg.recipient_agent == "discovery-agent"
        assert msg.subject == "Trigger discovery for subnet 10.0.0.0/24"
        assert msg.body == {}
        assert msg.correlation_id is None
        assert msg.expires_at is None
        assert msg.metadata == {}

    def test_broadcast_message(self) -> None:
        msg = _make_message(recipient_agent=None)
        assert msg.recipient_agent is None

    def test_message_with_correlation_id(self) -> None:
        msg = _make_message(
            message_type=MessageType.RESPONSE,
            correlation_id="corr-abc-123",
        )
        assert msg.message_type == MessageType.RESPONSE
        assert msg.correlation_id == "corr-abc-123"

    def test_correlation_id_links_request_response(self) -> None:
        correlation = "corr-req-001"
        request_msg = _make_message(
            message_id="msg-req",
            message_type=MessageType.REQUEST,
            correlation_id=correlation,
        )
        response_msg = _make_message(
            message_id="msg-resp",
            message_type=MessageType.RESPONSE,
            sender_agent="discovery-agent",
            recipient_agent="cmdb-agent",
            subject="Discovery results",
            correlation_id=correlation,
        )
        assert request_msg.correlation_id == response_msg.correlation_id

    def test_message_with_body(self) -> None:
        body = {"subnet": "10.0.0.0/24", "scan_type": "full"}
        msg = _make_message(body=body)
        assert msg.body == body

    def test_message_with_expiry(self) -> None:
        expires = datetime(2026, 2, 14, 10, 0, 0, tzinfo=UTC)
        msg = _make_message(expires_at=expires)
        assert msg.expires_at == expires

    def test_empty_message_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="message_id must not be empty"):
            _make_message(message_id="")

    def test_empty_sender_rejected(self) -> None:
        with pytest.raises(ValidationError, match="sender_agent must not be empty"):
            _make_message(sender_agent="")

    def test_empty_subject_rejected(self) -> None:
        with pytest.raises(ValidationError, match="subject must not be empty"):
            _make_message(subject="")

    def test_notification_message(self) -> None:
        msg = _make_message(
            message_type=MessageType.NOTIFICATION,
            subject="Agent health check scheduled",
            body={"schedule": "every 5 minutes"},
        )
        assert msg.message_type == MessageType.NOTIFICATION

    def test_error_message(self) -> None:
        msg = _make_message(
            message_type=MessageType.ERROR,
            subject="Task execution failed",
            body={"error_code": "ORCH_7001", "details": "Connection refused"},
        )
        assert msg.message_type == MessageType.ERROR

    def test_event_message(self) -> None:
        msg = _make_message(
            message_type=MessageType.EVENT,
            recipient_agent=None,
            subject="Workflow completed",
            body={"workflow_id": "full-discovery-scan", "execution_id": "exec-001"},
        )
        assert msg.message_type == MessageType.EVENT
        assert msg.recipient_agent is None

    def test_json_serialization_roundtrip(self) -> None:
        msg = _make_message(
            body={"key": "value"},
            correlation_id="corr-001",
            metadata={"priority": "high"},
        )
        json_str = msg.model_dump_json()
        restored = AgentMessage.model_validate_json(json_str)
        assert restored == msg


# ===================================================================
# Cross-model integration tests
# ===================================================================


class TestModelImports:
    """Tests that all models are properly importable from the package root."""

    def test_all_models_importable_from_package(self) -> None:
        """Verify that all models are re-exported from models.__init__."""
        from itom_orchestrator.models import (  # noqa: F401
            AgentCapability,
            AgentDomain,
            AgentMessage,
            AgentRegistration,
            AgentStatus,
            MessageType,
            Task,
            TaskPriority,
            TaskResult,
            TaskStatus,
            WorkflowDefinition,
            WorkflowExecution,
            WorkflowStatus,
            WorkflowStep,
            WorkflowStepType,
        )

    def test_all_exports_listed(self) -> None:
        """Verify __all__ contains exactly the expected model names."""
        import itom_orchestrator.models as models_pkg

        expected = {
            "AgentCapability",
            "AgentDomain",
            "AgentRegistration",
            "AgentStatus",
            "Task",
            "TaskPriority",
            "TaskResult",
            "TaskStatus",
            "WorkflowDefinition",
            "WorkflowExecution",
            "WorkflowStatus",
            "WorkflowStep",
            "WorkflowStepType",
            "AgentMessage",
            "MessageType",
        }
        assert set(models_pkg.__all__) == expected


class TestCrossModelInteraction:
    """Tests that models work together correctly across module boundaries."""

    def test_task_uses_agent_domain(self) -> None:
        """Task.domain references AgentDomain from the agents module."""
        task = _make_task(domain=AgentDomain.DISCOVERY)
        assert task.domain == AgentDomain.DISCOVERY

    def test_workflow_step_uses_agent_domain(self) -> None:
        """WorkflowStep.agent_domain references AgentDomain from the agents module."""
        step = _make_workflow_step(agent_domain=AgentDomain.ASSET)
        assert step.agent_domain == AgentDomain.ASSET

    def test_workflow_execution_uses_task_result(self) -> None:
        """WorkflowExecution.step_results contains TaskResult objects."""
        result = _make_task_result()
        execution = WorkflowExecution(
            execution_id="exec-cross",
            workflow_id="test-workflow",
            step_results={"step-1": result},
        )
        assert execution.step_results["step-1"].agent_id == "cmdb-agent"
        assert execution.step_results["step-1"].status == TaskStatus.COMPLETED
