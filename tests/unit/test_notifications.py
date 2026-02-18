"""
Tests for the notification and callback system (ORCH-017).
"""

from datetime import UTC, datetime

from itom_orchestrator.event_bus import EventBus, EventType
from itom_orchestrator.messaging import MessageQueue
from itom_orchestrator.models.workflows import WorkflowExecution, WorkflowStatus
from itom_orchestrator.notifications import NotificationManager


class TestNotificationManager:
    """Tests for the NotificationManager."""

    def _make_manager(self):
        queue = MessageQueue()
        bus = EventBus()
        return NotificationManager(queue, bus), queue, bus

    def test_notify_agent(self):
        manager, queue, bus = self._make_manager()

        message_id = manager.notify_agent(
            agent_id="cmdb-agent",
            message_type="task_assigned",
            payload={"task_id": "t1"},
        )

        assert message_id
        # Message should be in the queue
        msg = queue.dequeue("cmdb-agent")
        assert msg is not None
        assert msg.message_type == "task_assigned"
        assert msg.payload["task_id"] == "t1"

    def test_notify_agent_with_priority(self):
        from itom_orchestrator.messaging import MessagePriority

        manager, queue, _ = self._make_manager()

        manager.notify_agent(
            agent_id="cmdb-agent",
            message_type="alert",
            payload={},
            priority=MessagePriority.CRITICAL,
        )

        msg = queue.dequeue("cmdb-agent")
        assert msg is not None
        assert msg.priority == MessagePriority.CRITICAL

    def test_broadcast(self):
        manager, queue, bus = self._make_manager()
        received_events = []
        bus.subscribe(EventType.TASK_COMPLETED, lambda e: received_events.append(e))

        message_ids = manager.broadcast(
            message_type="system_update",
            payload={"version": "1.0.1"},
        )

        # Event should have been published
        assert len(received_events) == 1
        assert received_events[0].payload["message_type"] == "system_update"
        assert received_events[0].payload["broadcast"] is True

    def test_broadcast_with_exclude(self):
        manager, queue, bus = self._make_manager()

        message_ids = manager.broadcast(
            message_type="update",
            payload={},
            exclude=["cmdb-agent"],
        )

        # Should return list (even if empty for this implementation)
        assert isinstance(message_ids, list)

    def test_notify_workflow_complete(self):
        manager, _, bus = self._make_manager()
        received = []
        bus.subscribe(EventType.WORKFLOW_COMPLETED, lambda e: received.append(e))

        execution = WorkflowExecution(
            execution_id="exec-1",
            workflow_id="wf-1",
            status=WorkflowStatus.COMPLETED,
            steps_completed=["s1", "s2"],
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

        manager.notify_workflow_complete(execution)

        assert len(received) == 1
        assert received[0].payload["execution_id"] == "exec-1"
        assert received[0].payload["status"] == "completed"

    def test_notify_workflow_failed(self):
        manager, _, bus = self._make_manager()
        received = []
        bus.subscribe(EventType.WORKFLOW_FAILED, lambda e: received.append(e))

        execution = WorkflowExecution(
            execution_id="exec-2",
            workflow_id="wf-2",
            status=WorkflowStatus.FAILED,
            steps_completed=["s1"],
            steps_remaining=["s2"],
            started_at=datetime.now(UTC),
            error_message="Step s2 failed",
        )

        manager.notify_workflow_failed(execution, "Step s2 failed")

        assert len(received) == 1
        assert received[0].payload["error"] == "Step s2 failed"
        assert received[0].payload["execution_id"] == "exec-2"
