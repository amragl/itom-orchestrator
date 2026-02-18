"""
Inter-agent message passing for the ITOM Orchestrator.

Provides an in-memory message queue for agent-to-agent communication
with priority-based ordering and queue management.

This module implements ORCH-015: Inter-Agent Message Passing.
"""

import heapq
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class MessagePriority(StrEnum):
    """Priority levels for inter-agent messages.

    Messages with higher priority are dequeued first.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Numeric priority for heap ordering (lower number = higher priority)
_PRIORITY_ORDER: dict[MessagePriority, int] = {
    MessagePriority.CRITICAL: 0,
    MessagePriority.HIGH: 1,
    MessagePriority.NORMAL: 2,
    MessagePriority.LOW: 3,
}


class AgentMessage(BaseModel):
    """A message passed between agents via the message queue.

    Attributes:
        message_id: Unique identifier for the message.
        sender_id: Agent ID of the sender.
        recipient_id: Agent ID of the recipient.
        message_type: Type of message (e.g., 'request', 'notification').
        payload: Arbitrary structured message content.
        priority: Message priority for queue ordering.
        created_at: When the message was created.
        correlation_id: Links request/reply pairs together.
    """

    message_id: str = Field(default_factory=lambda: str(uuid4()))
    sender_id: str
    recipient_id: str
    message_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: MessagePriority = MessagePriority.NORMAL
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None


class MessageQueue:
    """In-memory message queue for inter-agent communication.

    Uses priority-based ordering so that CRITICAL messages are
    dequeued before NORMAL ones. Each recipient has a separate
    queue (implemented as a heap).
    """

    def __init__(self) -> None:
        # recipient_id -> list of (priority_num, sequence, message)
        self._queues: dict[str, list[tuple[int, int, AgentMessage]]] = {}
        self._sequence = 0  # Tiebreaker for equal priorities

    def enqueue(self, message: AgentMessage) -> None:
        """Add a message to the recipient's queue.

        Args:
            message: The message to enqueue.
        """
        recipient = message.recipient_id
        if recipient not in self._queues:
            self._queues[recipient] = []

        priority_num = _PRIORITY_ORDER.get(message.priority, 2)
        heapq.heappush(
            self._queues[recipient],
            (priority_num, self._sequence, message),
        )
        self._sequence += 1

        logger.debug(
            "Message enqueued",
            extra={
                "extra_data": {
                    "message_id": message.message_id,
                    "sender": message.sender_id,
                    "recipient": recipient,
                    "priority": message.priority.value,
                }
            },
        )

    def dequeue(self, recipient_id: str) -> AgentMessage | None:
        """Remove and return the highest-priority message for a recipient.

        Args:
            recipient_id: The agent ID to dequeue for.

        Returns:
            The next message, or None if the queue is empty.
        """
        queue = self._queues.get(recipient_id)
        if not queue:
            return None

        _, _, message = heapq.heappop(queue)

        # Clean up empty queues
        if not queue:
            del self._queues[recipient_id]

        logger.debug(
            "Message dequeued",
            extra={
                "extra_data": {
                    "message_id": message.message_id,
                    "recipient": recipient_id,
                }
            },
        )
        return message

    def peek(self, recipient_id: str) -> list[AgentMessage]:
        """View messages in a recipient's queue without removing them.

        Args:
            recipient_id: The agent ID to peek at.

        Returns:
            List of messages in priority order (highest first).
        """
        queue = self._queues.get(recipient_id, [])
        # Return sorted by priority and sequence
        sorted_entries = sorted(queue, key=lambda x: (x[0], x[1]))
        return [entry[2] for entry in sorted_entries]

    def get_all(self, recipient_id: str) -> list[AgentMessage]:
        """Remove and return all messages for a recipient.

        Args:
            recipient_id: The agent ID to drain.

        Returns:
            List of all messages in priority order.
        """
        queue = self._queues.pop(recipient_id, [])
        sorted_entries = sorted(queue, key=lambda x: (x[0], x[1]))
        return [entry[2] for entry in sorted_entries]

    def clear(self, recipient_id: str | None = None) -> int:
        """Clear messages from one or all queues.

        Args:
            recipient_id: If provided, clear only this recipient's queue.
                If None, clear all queues.

        Returns:
            Number of messages cleared.
        """
        if recipient_id is not None:
            queue = self._queues.pop(recipient_id, [])
            count = len(queue)
        else:
            count = sum(len(q) for q in self._queues.values())
            self._queues.clear()

        logger.info(
            "Message queue cleared",
            extra={
                "extra_data": {
                    "recipient": recipient_id or "all",
                    "cleared_count": count,
                }
            },
        )
        return count

    def queue_size(self, recipient_id: str) -> int:
        """Return the number of pending messages for a recipient.

        Args:
            recipient_id: The agent ID to check.

        Returns:
            Number of messages in the queue.
        """
        return len(self._queues.get(recipient_id, []))

    @property
    def total_messages(self) -> int:
        """Return total message count across all queues."""
        return sum(len(q) for q in self._queues.values())


# Global singleton
_global_queue: MessageQueue | None = None


def get_message_queue() -> MessageQueue:
    """Get the global MessageQueue singleton.

    Returns:
        The global MessageQueue instance.
    """
    global _global_queue
    if _global_queue is None:
        _global_queue = MessageQueue()
    return _global_queue


def reset_message_queue() -> None:
    """Reset the global MessageQueue singleton. For use in tests."""
    global _global_queue
    _global_queue = None
