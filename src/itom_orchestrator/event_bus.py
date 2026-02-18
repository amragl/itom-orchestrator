"""
Event bus for the ITOM Orchestrator.

Provides a synchronous publish/subscribe event bus for workflow
lifecycle events, agent status changes, and task routing events.

This module implements ORCH-016: Event Bus.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class EventType(StrEnum):
    """Types of events published on the event bus."""

    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_STEP_COMPLETED = "workflow.step.completed"
    AGENT_REGISTERED = "agent.registered"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    TASK_ROUTED = "task.routed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


class Event(BaseModel):
    """An event published on the event bus.

    Attributes:
        event_id: Unique identifier for the event.
        event_type: The type of event.
        source: Component or module that fired the event.
        payload: Arbitrary structured event data.
        timestamp: When the event occurred.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


EventHandler = Callable[[Event], None]


class EventBus:
    """Simple synchronous event bus for workflow and agent lifecycle events.

    Handlers are called synchronously in subscription order. If a handler
    raises an exception, the error is logged but other handlers still execute.
    """

    def __init__(self) -> None:
        # event_type -> list of (subscription_id, handler)
        self._subscribers: dict[EventType, list[tuple[str, EventHandler]]] = {}
        self._history: list[Event] = []
        self._max_history = 1000

    def subscribe(self, event_type: EventType, handler: EventHandler) -> str:
        """Subscribe a handler to an event type.

        Args:
            event_type: The event type to subscribe to.
            handler: Callable that accepts an Event argument.

        Returns:
            Subscription ID for later unsubscription.
        """
        subscription_id = str(uuid4())
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append((subscription_id, handler))

        logger.debug(
            "Event handler subscribed",
            extra={
                "extra_data": {
                    "event_type": event_type.value,
                    "subscription_id": subscription_id,
                }
            },
        )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by its ID.

        Args:
            subscription_id: The subscription ID returned by subscribe().

        Returns:
            True if the subscription was found and removed, False otherwise.
        """
        for event_type, handlers in self._subscribers.items():
            for i, (sid, _) in enumerate(handlers):
                if sid == subscription_id:
                    handlers.pop(i)
                    logger.debug(
                        "Event handler unsubscribed",
                        extra={
                            "extra_data": {
                                "subscription_id": subscription_id,
                                "event_type": event_type.value,
                            }
                        },
                    )
                    return True
        return False

    def publish(self, event: Event) -> int:
        """Publish an event to all subscribed handlers.

        Handlers are called synchronously. If a handler raises an
        exception, the error is logged and remaining handlers still
        execute.

        Args:
            event: The event to publish.

        Returns:
            Number of handlers that were invoked.
        """
        # Record in history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._subscribers.get(event.event_type, [])
        handler_count = 0

        for subscription_id, handler in handlers:
            try:
                handler(event)
                handler_count += 1
            except Exception:
                logger.error(
                    "Event handler raised an exception",
                    extra={
                        "extra_data": {
                            "event_type": event.event_type.value,
                            "event_id": event.event_id,
                            "subscription_id": subscription_id,
                        }
                    },
                    exc_info=True,
                )
                handler_count += 1  # Still counts as invoked

        logger.debug(
            "Event published",
            extra={
                "extra_data": {
                    "event_type": event.event_type.value,
                    "event_id": event.event_id,
                    "handler_count": handler_count,
                }
            },
        )
        return handler_count

    def get_history(
        self,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Retrieve event history.

        Args:
            event_type: If provided, filter to this event type only.
            limit: Maximum number of events to return (most recent first).

        Returns:
            List of recent events, newest first.
        """
        if event_type is not None:
            events = [e for e in self._history if e.event_type == event_type]
        else:
            events = list(self._history)

        recent = events[-limit:] if limit < len(events) else events
        return list(reversed(recent))

    def clear_history(self) -> int:
        """Clear the event history.

        Returns:
            Number of events cleared.
        """
        count = len(self._history)
        self._history.clear()
        return count

    @property
    def subscriber_count(self) -> int:
        """Total number of active subscriptions."""
        return sum(len(handlers) for handlers in self._subscribers.values())


# Global singleton
_global_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global EventBus singleton.

    Returns:
        The global EventBus instance.
    """
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def reset_event_bus() -> None:
    """Reset the global EventBus singleton. For use in tests."""
    global _global_bus
    _global_bus = None
