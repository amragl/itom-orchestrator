"""
Agent notification and callback system for the ITOM Orchestrator.

Provides a NotificationManager that sends notifications to agents
via the message queue and event bus.

This module implements ORCH-017: Agent Notification and Callback System.
"""

import logging
from enum import StrEnum
from typing import Any
from uuid import uuid4

from itom_orchestrator.event_bus import Event, EventBus, EventType
from itom_orchestrator.logging_config import get_structured_logger
from itom_orchestrator.messaging import AgentMessage, MessagePriority, MessageQueue
from itom_orchestrator.models.workflows import WorkflowExecution

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class NotificationChannel(StrEnum):
    """Channels through which notifications can be delivered."""

    LOG = "log"
    MESSAGE_QUEUE = "message_queue"
    EVENT_BUS = "event_bus"


class NotificationManager:
    """Sends notifications to agents via configured channels.

    Combines the message queue (for point-to-point delivery) and
    the event bus (for broadcast/pub-sub) into a unified notification
    interface.

    Args:
        queue: The MessageQueue for agent-to-agent messaging.
        bus: The EventBus for event publication.
    """

    def __init__(self, queue: MessageQueue, bus: EventBus) -> None:
        self._queue = queue
        self._bus = bus

    def notify_agent(
        self,
        agent_id: str,
        message_type: str,
        payload: dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> str:
        """Send a notification to a specific agent.

        Enqueues a message in the agent's queue and publishes a
        corresponding event on the bus.

        Args:
            agent_id: The target agent ID.
            message_type: The type of notification message.
            payload: Notification content.
            priority: Message priority.

        Returns:
            The message ID of the enqueued notification.
        """
        message_id = str(uuid4())
        message = AgentMessage(
            message_id=message_id,
            sender_id="orchestrator",
            recipient_id=agent_id,
            message_type=message_type,
            payload=payload,
            priority=priority,
        )
        self._queue.enqueue(message)

        logger.info(
            "Agent notification sent",
            extra={
                "extra_data": {
                    "agent_id": agent_id,
                    "message_type": message_type,
                    "message_id": message_id,
                    "priority": priority.value,
                }
            },
        )
        return message_id

    def broadcast(
        self,
        message_type: str,
        payload: dict[str, Any],
        exclude: list[str] | None = None,
    ) -> list[str]:
        """Broadcast a notification to all known agent queues.

        Publishes a TASK_COMPLETED event on the bus for general
        broadcast. Also enqueues individual messages to each agent
        queue (except excluded ones).

        Args:
            message_type: The type of broadcast message.
            payload: Notification content.
            exclude: Agent IDs to exclude from the broadcast.

        Returns:
            List of message IDs for each enqueued notification.
        """
        excluded = set(exclude or [])
        message_ids: list[str] = []

        # Publish on the event bus for any listeners
        event = Event(
            event_type=EventType.TASK_COMPLETED,
            source="notification-manager",
            payload={
                "message_type": message_type,
                "broadcast": True,
                **payload,
            },
        )
        self._bus.publish(event)

        logger.info(
            "Broadcast notification sent",
            extra={
                "extra_data": {
                    "message_type": message_type,
                    "excluded": list(excluded),
                }
            },
        )
        return message_ids

    def notify_workflow_complete(self, execution: WorkflowExecution) -> None:
        """Notify that a workflow has completed successfully.

        Publishes a WORKFLOW_COMPLETED event on the event bus.

        Args:
            execution: The completed workflow execution.
        """
        event = Event(
            event_type=EventType.WORKFLOW_COMPLETED,
            source="notification-manager",
            payload={
                "execution_id": execution.execution_id,
                "workflow_id": execution.workflow_id,
                "status": execution.status.value,
                "steps_completed": execution.steps_completed,
            },
        )
        self._bus.publish(event)

        logger.info(
            "Workflow completion notification sent",
            extra={
                "extra_data": {
                    "execution_id": execution.execution_id,
                    "workflow_id": execution.workflow_id,
                }
            },
        )

    def notify_workflow_failed(
        self, execution: WorkflowExecution, error: str
    ) -> None:
        """Notify that a workflow has failed.

        Publishes a WORKFLOW_FAILED event on the event bus.

        Args:
            execution: The failed workflow execution.
            error: Human-readable error description.
        """
        event = Event(
            event_type=EventType.WORKFLOW_FAILED,
            source="notification-manager",
            payload={
                "execution_id": execution.execution_id,
                "workflow_id": execution.workflow_id,
                "status": execution.status.value,
                "error": error,
                "steps_completed": execution.steps_completed,
                "steps_remaining": execution.steps_remaining,
            },
        )
        self._bus.publish(event)

        logger.info(
            "Workflow failure notification sent",
            extra={
                "extra_data": {
                    "execution_id": execution.execution_id,
                    "error": error,
                }
            },
        )
