"""
Audit trail for the ITOM Orchestrator.

Records all significant actions for compliance, debugging, and
governance. Provides in-memory storage with query and export
capabilities.

This module implements ORCH-019: Audit Trail.
"""

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)


class AuditEventType(StrEnum):
    """Types of events recorded in the audit trail."""

    TASK_ROUTED = "task_routed"
    TASK_EXECUTED = "task_executed"
    TASK_FAILED = "task_failed"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    PERMISSION_CHECK = "permission_check"
    PERMISSION_DENIED = "permission_denied"
    AGENT_REGISTERED = "agent_registered"


class AuditEntry(BaseModel):
    """A single entry in the audit trail.

    Attributes:
        entry_id: Unique identifier for the entry.
        event_type: The type of auditable event.
        actor: Agent or component that performed the action.
        target: What was acted upon (e.g., task ID, agent ID).
        details: Arbitrary structured details about the event.
        result: Outcome of the action ('success' or 'failure').
        timestamp: When the event occurred.
    """

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: AuditEventType
    actor: str
    target: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    result: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditTrail:
    """Records all significant actions for compliance and debugging.

    Provides in-memory audit trail storage with filtering, querying,
    and JSON export capabilities.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._max_entries = 10000

    def record(self, entry: AuditEntry) -> None:
        """Record an audit entry.

        Args:
            entry: The audit entry to record.
        """
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        logger.debug(
            "Audit entry recorded",
            extra={
                "extra_data": {
                    "entry_id": entry.entry_id,
                    "event_type": entry.event_type.value,
                    "actor": entry.actor,
                    "target": entry.target,
                    "result": entry.result,
                }
            },
        )

    def get_entries(
        self,
        event_type: AuditEventType | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filtering.

        Args:
            event_type: Filter by event type.
            actor: Filter by actor.
            since: Filter to entries after this timestamp.
            limit: Maximum entries to return (most recent first).

        Returns:
            List of matching audit entries, newest first.
        """
        entries = self._entries

        if event_type is not None:
            entries = [e for e in entries if e.event_type == event_type]

        if actor is not None:
            entries = [e for e in entries if e.actor == actor]

        if since is not None:
            entries = [e for e in entries if e.timestamp >= since]

        recent = entries[-limit:] if limit < len(entries) else entries
        return list(reversed(recent))

    def get_recent(self, limit: int = 50) -> list[AuditEntry]:
        """Return the most recent audit entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of recent entries, newest first.
        """
        recent = self._entries[-limit:] if limit < len(self._entries) else self._entries
        return list(reversed(recent))

    def clear(self) -> int:
        """Clear all audit entries.

        Returns:
            Number of entries cleared.
        """
        count = len(self._entries)
        self._entries.clear()
        logger.info(
            "Audit trail cleared",
            extra={"extra_data": {"cleared_count": count}},
        )
        return count

    def export_json(self) -> list[dict[str, Any]]:
        """Export all audit entries as JSON-serializable dictionaries.

        Returns:
            List of entry dictionaries with ISO 8601 timestamps.
        """
        return [entry.model_dump(mode="json") for entry in self._entries]

    @property
    def entry_count(self) -> int:
        """Return the number of recorded entries."""
        return len(self._entries)


# Global singleton
_global_trail: AuditTrail | None = None


def get_audit_trail() -> AuditTrail:
    """Get the global AuditTrail singleton.

    Returns:
        The global AuditTrail instance.
    """
    global _global_trail
    if _global_trail is None:
        _global_trail = AuditTrail()
    return _global_trail


def reset_audit_trail() -> None:
    """Reset the global AuditTrail singleton. For use in tests."""
    global _global_trail
    _global_trail = None
