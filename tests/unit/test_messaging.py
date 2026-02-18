"""
Tests for inter-agent message passing (ORCH-015).
"""

import pytest

from itom_orchestrator.messaging import (
    AgentMessage,
    MessagePriority,
    MessageQueue,
    get_message_queue,
    reset_message_queue,
)


class TestAgentMessage:
    """Tests for the AgentMessage model."""

    def test_create_message(self):
        msg = AgentMessage(
            sender_id="agent-a",
            recipient_id="agent-b",
            message_type="request",
            payload={"action": "query"},
        )
        assert msg.message_id  # auto-generated
        assert msg.sender_id == "agent-a"
        assert msg.recipient_id == "agent-b"
        assert msg.priority == MessagePriority.NORMAL
        assert msg.correlation_id is None

    def test_create_with_priority(self):
        msg = AgentMessage(
            sender_id="a",
            recipient_id="b",
            message_type="alert",
            priority=MessagePriority.CRITICAL,
        )
        assert msg.priority == MessagePriority.CRITICAL


class TestMessageQueue:
    """Tests for the MessageQueue."""

    def _make_msg(self, sender="a", recipient="b", priority=MessagePriority.NORMAL):
        return AgentMessage(
            sender_id=sender,
            recipient_id=recipient,
            message_type="test",
            payload={"data": "test"},
            priority=priority,
        )

    def test_enqueue_and_dequeue(self):
        queue = MessageQueue()
        msg = self._make_msg()
        queue.enqueue(msg)

        result = queue.dequeue("b")
        assert result is not None
        assert result.message_id == msg.message_id

    def test_dequeue_empty_returns_none(self):
        queue = MessageQueue()
        assert queue.dequeue("nonexistent") is None

    def test_fifo_order_same_priority(self):
        queue = MessageQueue()
        msg1 = self._make_msg(sender="first")
        msg2 = self._make_msg(sender="second")

        queue.enqueue(msg1)
        queue.enqueue(msg2)

        result1 = queue.dequeue("b")
        result2 = queue.dequeue("b")

        assert result1.sender_id == "first"
        assert result2.sender_id == "second"

    def test_priority_ordering(self):
        queue = MessageQueue()
        low = self._make_msg(sender="low", priority=MessagePriority.LOW)
        high = self._make_msg(sender="high", priority=MessagePriority.HIGH)
        critical = self._make_msg(sender="critical", priority=MessagePriority.CRITICAL)
        normal = self._make_msg(sender="normal", priority=MessagePriority.NORMAL)

        # Enqueue in non-priority order
        queue.enqueue(low)
        queue.enqueue(normal)
        queue.enqueue(critical)
        queue.enqueue(high)

        # Dequeue should be in priority order
        assert queue.dequeue("b").sender_id == "critical"
        assert queue.dequeue("b").sender_id == "high"
        assert queue.dequeue("b").sender_id == "normal"
        assert queue.dequeue("b").sender_id == "low"

    def test_peek_does_not_remove(self):
        queue = MessageQueue()
        msg = self._make_msg()
        queue.enqueue(msg)

        peeked = queue.peek("b")
        assert len(peeked) == 1
        assert peeked[0].message_id == msg.message_id

        # Message should still be in queue
        assert queue.dequeue("b") is not None

    def test_peek_empty_queue(self):
        queue = MessageQueue()
        assert queue.peek("nonexistent") == []

    def test_get_all_drains_queue(self):
        queue = MessageQueue()
        queue.enqueue(self._make_msg(sender="1"))
        queue.enqueue(self._make_msg(sender="2"))

        messages = queue.get_all("b")
        assert len(messages) == 2

        # Queue should be empty now
        assert queue.dequeue("b") is None

    def test_clear_specific_recipient(self):
        queue = MessageQueue()
        queue.enqueue(self._make_msg(recipient="b"))
        queue.enqueue(self._make_msg(recipient="c"))

        count = queue.clear("b")
        assert count == 1
        assert queue.dequeue("b") is None
        assert queue.dequeue("c") is not None

    def test_clear_all(self):
        queue = MessageQueue()
        queue.enqueue(self._make_msg(recipient="b"))
        queue.enqueue(self._make_msg(recipient="c"))

        count = queue.clear()
        assert count == 2
        assert queue.total_messages == 0

    def test_queue_size(self):
        queue = MessageQueue()
        assert queue.queue_size("b") == 0

        queue.enqueue(self._make_msg())
        queue.enqueue(self._make_msg())
        assert queue.queue_size("b") == 2

    def test_total_messages(self):
        queue = MessageQueue()
        queue.enqueue(self._make_msg(recipient="b"))
        queue.enqueue(self._make_msg(recipient="c"))
        queue.enqueue(self._make_msg(recipient="b"))

        assert queue.total_messages == 3

    def test_separate_queues_per_recipient(self):
        queue = MessageQueue()
        queue.enqueue(self._make_msg(sender="for-b", recipient="b"))
        queue.enqueue(self._make_msg(sender="for-c", recipient="c"))

        msg_b = queue.dequeue("b")
        assert msg_b.sender_id == "for-b"

        msg_c = queue.dequeue("c")
        assert msg_c.sender_id == "for-c"


class TestMessageQueueSingleton:
    """Tests for the global singleton."""

    def test_get_message_queue_returns_same_instance(self):
        reset_message_queue()
        q1 = get_message_queue()
        q2 = get_message_queue()
        assert q1 is q2

    def test_reset_creates_new_instance(self):
        reset_message_queue()
        q1 = get_message_queue()
        reset_message_queue()
        q2 = get_message_queue()
        assert q1 is not q2
