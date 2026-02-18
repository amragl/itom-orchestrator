"""
Tests for the event bus (ORCH-016).
"""

import pytest

from itom_orchestrator.event_bus import (
    Event,
    EventBus,
    EventType,
    get_event_bus,
    reset_event_bus,
)


class TestEvent:
    """Tests for the Event model."""

    def test_create_event(self):
        event = Event(
            event_type=EventType.WORKFLOW_STARTED,
            source="test",
            payload={"workflow_id": "wf-1"},
        )
        assert event.event_id
        assert event.event_type == EventType.WORKFLOW_STARTED
        assert event.source == "test"
        assert event.timestamp is not None


class TestEventBus:
    """Tests for the EventBus."""

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.WORKFLOW_STARTED, handler)

        event = Event(
            event_type=EventType.WORKFLOW_STARTED,
            source="test",
        )
        count = bus.publish(event)

        assert count == 1
        assert len(received) == 1
        assert received[0].event_id == event.event_id

    def test_publish_no_subscribers_returns_zero(self):
        bus = EventBus()
        event = Event(
            event_type=EventType.WORKFLOW_STARTED,
            source="test",
        )
        count = bus.publish(event)
        assert count == 0

    def test_multiple_subscribers(self):
        bus = EventBus()
        results = {"a": 0, "b": 0}

        bus.subscribe(EventType.TASK_COMPLETED, lambda e: results.update(a=results["a"] + 1))
        bus.subscribe(EventType.TASK_COMPLETED, lambda e: results.update(b=results["b"] + 1))

        event = Event(event_type=EventType.TASK_COMPLETED, source="test")
        count = bus.publish(event)

        assert count == 2
        assert results["a"] == 1
        assert results["b"] == 1

    def test_subscribe_only_matching_type(self):
        bus = EventBus()
        received = []

        bus.subscribe(EventType.WORKFLOW_STARTED, lambda e: received.append(e))

        # Publish a different event type
        event = Event(event_type=EventType.TASK_FAILED, source="test")
        bus.publish(event)

        assert len(received) == 0

    def test_unsubscribe(self):
        bus = EventBus()
        received = []

        sub_id = bus.subscribe(EventType.WORKFLOW_STARTED, lambda e: received.append(e))

        # Unsubscribe
        result = bus.unsubscribe(sub_id)
        assert result is True

        # Publish should not trigger handler
        event = Event(event_type=EventType.WORKFLOW_STARTED, source="test")
        bus.publish(event)
        assert len(received) == 0

    def test_unsubscribe_unknown_returns_false(self):
        bus = EventBus()
        assert bus.unsubscribe("nonexistent-id") is False

    def test_handler_exception_does_not_stop_others(self):
        bus = EventBus()
        results = []

        def failing_handler(event):
            raise RuntimeError("Handler failure")

        def working_handler(event):
            results.append("ok")

        bus.subscribe(EventType.TASK_COMPLETED, failing_handler)
        bus.subscribe(EventType.TASK_COMPLETED, working_handler)

        event = Event(event_type=EventType.TASK_COMPLETED, source="test")
        count = bus.publish(event)

        # Both handlers were invoked (even though first raised)
        assert count == 2
        assert results == ["ok"]

    def test_get_history(self):
        bus = EventBus()

        e1 = Event(event_type=EventType.WORKFLOW_STARTED, source="test")
        e2 = Event(event_type=EventType.WORKFLOW_COMPLETED, source="test")
        bus.publish(e1)
        bus.publish(e2)

        history = bus.get_history()
        assert len(history) == 2
        # Most recent first
        assert history[0].event_id == e2.event_id
        assert history[1].event_id == e1.event_id

    def test_get_history_by_type(self):
        bus = EventBus()

        bus.publish(Event(event_type=EventType.WORKFLOW_STARTED, source="test"))
        bus.publish(Event(event_type=EventType.TASK_COMPLETED, source="test"))
        bus.publish(Event(event_type=EventType.WORKFLOW_STARTED, source="test"))

        history = bus.get_history(event_type=EventType.WORKFLOW_STARTED)
        assert len(history) == 2

    def test_get_history_with_limit(self):
        bus = EventBus()

        for _ in range(10):
            bus.publish(Event(event_type=EventType.TASK_COMPLETED, source="test"))

        history = bus.get_history(limit=5)
        assert len(history) == 5

    def test_clear_history(self):
        bus = EventBus()
        bus.publish(Event(event_type=EventType.TASK_COMPLETED, source="test"))
        bus.publish(Event(event_type=EventType.TASK_COMPLETED, source="test"))

        count = bus.clear_history()
        assert count == 2
        assert bus.get_history() == []

    def test_subscriber_count(self):
        bus = EventBus()
        assert bus.subscriber_count == 0

        bus.subscribe(EventType.WORKFLOW_STARTED, lambda e: None)
        bus.subscribe(EventType.TASK_COMPLETED, lambda e: None)
        assert bus.subscriber_count == 2


class TestEventBusSingleton:
    """Tests for the global singleton."""

    def test_get_event_bus_returns_same_instance(self):
        reset_event_bus()
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_reset_creates_new_instance(self):
        reset_event_bus()
        b1 = get_event_bus()
        reset_event_bus()
        b2 = get_event_bus()
        assert b1 is not b2
