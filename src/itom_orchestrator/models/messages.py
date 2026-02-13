"""
Inter-agent messaging models for the ITOM Orchestrator.

Defines the data contracts for messages exchanged between agents
through the orchestrator's message bus. Messages support request/response
patterns, broadcast notifications, and event-driven communication.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MessageType(StrEnum):
    """Type of inter-agent message.

    Determines how the message bus processes and delivers the message.
    """

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    EVENT = "event"
    ERROR = "error"


class AgentMessage(BaseModel):
    """Message passed between agents via the orchestrator.

    Messages are the primary communication mechanism between agents.
    They support point-to-point delivery (with ``recipient_agent``) and
    broadcast (when ``recipient_agent`` is ``None``). Request/response
    pairs are linked via ``correlation_id``.

    Attributes:
        message_id: Unique identifier (UUID format).
        message_type: The type of message.
        sender_agent: Agent ID of the sender.
        recipient_agent: Agent ID of the recipient, or ``None`` for broadcast.
        subject: Short description of the message purpose.
        body: Arbitrary structured message content.
        correlation_id: Links request/response pairs together.
        created_at: When the message was created.
        expires_at: When the message expires (``None`` for no expiry).
        metadata: Arbitrary key-value metadata for extensibility.
    """

    message_id: str
    message_type: MessageType
    sender_agent: str
    recipient_agent: str | None = None
    subject: str
    body: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message_id")
    @classmethod
    def message_id_must_be_non_empty(cls, v: str) -> str:
        """Message ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("message_id must not be empty")
        return v

    @field_validator("sender_agent")
    @classmethod
    def sender_agent_must_be_non_empty(cls, v: str) -> str:
        """Sender agent ID must be a non-empty string."""
        if not v.strip():
            raise ValueError("sender_agent must not be empty")
        return v

    @field_validator("subject")
    @classmethod
    def subject_must_be_non_empty(cls, v: str) -> str:
        """Message subject must be a non-empty string."""
        if not v.strip():
            raise ValueError("subject must not be empty")
        return v
