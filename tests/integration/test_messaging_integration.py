"""
Integration tests for messaging, events, and role enforcement (ORCH-023).

Tests the interplay between message queue, event bus, notification
manager, and role enforcer in end-to-end scenarios.
"""

import pytest

from itom_orchestrator.audit_trail import AuditEntry, AuditEventType, AuditTrail
from itom_orchestrator.event_bus import Event, EventBus, EventType
from itom_orchestrator.messaging import AgentMessage, MessagePriority, MessageQueue
from itom_orchestrator.models.agents import AgentDomain
from itom_orchestrator.notifications import NotificationManager
from itom_orchestrator.role_enforcer import Permission, RoleEnforcer, RolePolicy, get_default_enforcer


@pytest.mark.integration
class TestMessageQueueIntegration:
    """Tests for message queue across multiple agents."""

    def test_multi_agent_messaging(self):
        """Test enqueue/dequeue across multiple agents."""
        queue = MessageQueue()

        # Send messages to different agents
        for agent_id in ["cmdb-agent", "discovery-agent", "asset-agent"]:
            msg = AgentMessage(
                sender_id="orchestrator",
                recipient_id=agent_id,
                message_type="task_assignment",
                payload={"task": f"task-for-{agent_id}"},
            )
            queue.enqueue(msg)

        assert queue.total_messages == 3

        # Each agent dequeues their own messages
        cmdb_msg = queue.dequeue("cmdb-agent")
        assert cmdb_msg is not None
        assert cmdb_msg.payload["task"] == "task-for-cmdb-agent"

        disc_msg = queue.dequeue("discovery-agent")
        assert disc_msg is not None
        assert disc_msg.payload["task"] == "task-for-discovery-agent"

        asset_msg = queue.dequeue("asset-agent")
        assert asset_msg is not None
        assert asset_msg.payload["task"] == "task-for-asset-agent"

        # Queues should be empty
        assert queue.total_messages == 0

    def test_priority_ordering_across_messages(self):
        """Test that priority ordering works correctly for one agent."""
        queue = MessageQueue()

        # Send messages with different priorities
        for priority, label in [
            (MessagePriority.LOW, "low"),
            (MessagePriority.CRITICAL, "critical"),
            (MessagePriority.NORMAL, "normal"),
            (MessagePriority.HIGH, "high"),
        ]:
            queue.enqueue(AgentMessage(
                sender_id="orchestrator",
                recipient_id="cmdb-agent",
                message_type="task",
                payload={"label": label},
                priority=priority,
            ))

        # Dequeue should return in priority order
        labels = []
        while (msg := queue.dequeue("cmdb-agent")) is not None:
            labels.append(msg.payload["label"])

        assert labels == ["critical", "high", "normal", "low"]


@pytest.mark.integration
class TestEventBusIntegration:
    """Tests for event bus publish/subscribe with real handlers."""

    def test_workflow_lifecycle_events(self):
        """Test subscribing to multiple workflow lifecycle events."""
        bus = EventBus()
        events_received: dict[str, list[Event]] = {
            "started": [],
            "completed": [],
            "failed": [],
        }

        bus.subscribe(EventType.WORKFLOW_STARTED, lambda e: events_received["started"].append(e))
        bus.subscribe(EventType.WORKFLOW_COMPLETED, lambda e: events_received["completed"].append(e))
        bus.subscribe(EventType.WORKFLOW_FAILED, lambda e: events_received["failed"].append(e))

        # Simulate workflow lifecycle
        bus.publish(Event(
            event_type=EventType.WORKFLOW_STARTED,
            source="engine",
            payload={"workflow_id": "wf-1"},
        ))
        bus.publish(Event(
            event_type=EventType.WORKFLOW_STEP_COMPLETED,
            source="engine",
            payload={"step_id": "s1"},
        ))
        bus.publish(Event(
            event_type=EventType.WORKFLOW_COMPLETED,
            source="engine",
            payload={"workflow_id": "wf-1"},
        ))

        assert len(events_received["started"]) == 1
        assert len(events_received["completed"]) == 1
        assert len(events_received["failed"]) == 0

    def test_event_handler_with_side_effects(self):
        """Test that handlers can trigger real side effects."""
        bus = EventBus()
        queue = MessageQueue()

        # Subscribe: when a task is routed, notify the target agent
        def on_task_routed(event):
            agent_id = event.payload.get("agent_id", "unknown")
            queue.enqueue(AgentMessage(
                sender_id="orchestrator",
                recipient_id=agent_id,
                message_type="task_notification",
                payload={"task_id": event.payload.get("task_id")},
            ))

        bus.subscribe(EventType.TASK_ROUTED, on_task_routed)

        # Publish task routed event
        bus.publish(Event(
            event_type=EventType.TASK_ROUTED,
            source="router",
            payload={"task_id": "t1", "agent_id": "cmdb-agent"},
        ))

        # Agent should have a message
        msg = queue.dequeue("cmdb-agent")
        assert msg is not None
        assert msg.payload["task_id"] == "t1"


@pytest.mark.integration
class TestNotificationManagerIntegration:
    """Tests for NotificationManager broadcast."""

    def test_notification_triggers_event(self):
        """Test that notification manager publishes events on the bus."""
        queue = MessageQueue()
        bus = EventBus()
        manager = NotificationManager(queue, bus)

        events_received = []
        bus.subscribe(EventType.TASK_COMPLETED, lambda e: events_received.append(e))

        manager.broadcast(
            message_type="system_update",
            payload={"version": "2.0.0"},
        )

        assert len(events_received) == 1
        assert events_received[0].payload["broadcast"] is True


@pytest.mark.integration
class TestRoleEnforcerIntegration:
    """Tests for role enforcer in end-to-end scenarios."""

    def test_enforce_then_audit(self):
        """Test role check followed by audit trail recording."""
        enforcer = get_default_enforcer()
        trail = AuditTrail()

        # Simulate a permission check
        allowed = enforcer.check_permission("cmdb-agent", "cmdb.query", AgentDomain.CMDB)
        trail.record(AuditEntry(
            event_type=AuditEventType.PERMISSION_CHECK,
            actor="cmdb-agent",
            target="cmdb.query",
            details={"domain": "cmdb", "allowed": allowed},
            result="success" if allowed else "failure",
        ))

        # Check denied action
        denied = enforcer.check_permission("cmdb-agent", "discovery.scan", AgentDomain.DISCOVERY)
        trail.record(AuditEntry(
            event_type=AuditEventType.PERMISSION_DENIED,
            actor="cmdb-agent",
            target="discovery.scan",
            details={"domain": "discovery", "allowed": denied},
            result="failure",
        ))

        assert allowed is True
        assert denied is False

        entries = trail.get_entries()
        assert len(entries) == 2

        # Check that audit correctly recorded the permission check
        permission_checks = trail.get_entries(event_type=AuditEventType.PERMISSION_CHECK)
        assert len(permission_checks) == 1
        assert permission_checks[0].result == "success"

        denied_entries = trail.get_entries(event_type=AuditEventType.PERMISSION_DENIED)
        assert len(denied_entries) == 1
        assert denied_entries[0].result == "failure"

    def test_all_default_agents_have_policies(self):
        """Verify all 6 default ITOM agents have role policies."""
        enforcer = get_default_enforcer()
        expected_roles = [
            "orchestrator",
            "cmdb-agent",
            "discovery-agent",
            "asset-agent",
            "itom-auditor",
            "itom-documentator",
        ]
        for role_id in expected_roles:
            policy = enforcer.get_policy(role_id)
            assert policy is not None, f"Missing policy for {role_id}"
            assert len(policy.permissions) > 0
