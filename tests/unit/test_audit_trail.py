"""
Tests for the audit trail (ORCH-019).
"""

from datetime import UTC, datetime, timedelta

import pytest

from itom_orchestrator.audit_trail import (
    AuditEntry,
    AuditEventType,
    AuditTrail,
    get_audit_trail,
    reset_audit_trail,
)


class TestAuditEntry:
    """Tests for the AuditEntry model."""

    def test_create_entry(self):
        entry = AuditEntry(
            event_type=AuditEventType.TASK_ROUTED,
            actor="orchestrator",
            target="task-1",
            details={"agent_id": "cmdb-agent"},
            result="success",
        )
        assert entry.entry_id
        assert entry.event_type == AuditEventType.TASK_ROUTED
        assert entry.actor == "orchestrator"
        assert entry.result == "success"
        assert entry.timestamp is not None


class TestAuditTrail:
    """Tests for the AuditTrail."""

    def _make_entry(
        self,
        event_type=AuditEventType.TASK_ROUTED,
        actor="orchestrator",
        result="success",
        timestamp=None,
    ):
        return AuditEntry(
            event_type=event_type,
            actor=actor,
            target="target-1",
            result=result,
            timestamp=timestamp or datetime.now(UTC),
        )

    def test_record_and_get_recent(self):
        trail = AuditTrail()
        entry = self._make_entry()
        trail.record(entry)

        recent = trail.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].entry_id == entry.entry_id

    def test_get_entries_by_event_type(self):
        trail = AuditTrail()
        trail.record(self._make_entry(event_type=AuditEventType.TASK_ROUTED))
        trail.record(self._make_entry(event_type=AuditEventType.TASK_FAILED))
        trail.record(self._make_entry(event_type=AuditEventType.TASK_ROUTED))

        entries = trail.get_entries(event_type=AuditEventType.TASK_ROUTED)
        assert len(entries) == 2

    def test_get_entries_by_actor(self):
        trail = AuditTrail()
        trail.record(self._make_entry(actor="agent-a"))
        trail.record(self._make_entry(actor="agent-b"))
        trail.record(self._make_entry(actor="agent-a"))

        entries = trail.get_entries(actor="agent-a")
        assert len(entries) == 2

    def test_get_entries_since(self):
        trail = AuditTrail()
        old = datetime.now(UTC) - timedelta(hours=2)
        recent = datetime.now(UTC) - timedelta(minutes=5)
        now = datetime.now(UTC)

        trail.record(self._make_entry(timestamp=old))
        trail.record(self._make_entry(timestamp=recent))
        trail.record(self._make_entry(timestamp=now))

        since = datetime.now(UTC) - timedelta(hours=1)
        entries = trail.get_entries(since=since)
        assert len(entries) == 2

    def test_get_entries_with_limit(self):
        trail = AuditTrail()
        for _ in range(10):
            trail.record(self._make_entry())

        entries = trail.get_entries(limit=5)
        assert len(entries) == 5

    def test_get_recent_ordering(self):
        trail = AuditTrail()
        e1 = self._make_entry(actor="first")
        e2 = self._make_entry(actor="second")
        trail.record(e1)
        trail.record(e2)

        recent = trail.get_recent()
        # Most recent first
        assert recent[0].actor == "second"
        assert recent[1].actor == "first"

    def test_clear(self):
        trail = AuditTrail()
        trail.record(self._make_entry())
        trail.record(self._make_entry())

        count = trail.clear()
        assert count == 2
        assert trail.entry_count == 0

    def test_export_json(self):
        trail = AuditTrail()
        trail.record(self._make_entry())
        trail.record(self._make_entry())

        exported = trail.export_json()
        assert len(exported) == 2
        assert isinstance(exported[0], dict)
        assert "entry_id" in exported[0]
        assert "event_type" in exported[0]

    def test_entry_count(self):
        trail = AuditTrail()
        assert trail.entry_count == 0

        trail.record(self._make_entry())
        trail.record(self._make_entry())
        assert trail.entry_count == 2

    def test_max_entries_enforced(self):
        trail = AuditTrail()
        trail._max_entries = 5

        for i in range(10):
            trail.record(self._make_entry(actor=f"agent-{i}"))

        assert trail.entry_count == 5

    def test_combined_filters(self):
        trail = AuditTrail()
        trail.record(self._make_entry(
            event_type=AuditEventType.TASK_ROUTED,
            actor="agent-a",
        ))
        trail.record(self._make_entry(
            event_type=AuditEventType.TASK_ROUTED,
            actor="agent-b",
        ))
        trail.record(self._make_entry(
            event_type=AuditEventType.TASK_FAILED,
            actor="agent-a",
        ))

        entries = trail.get_entries(
            event_type=AuditEventType.TASK_ROUTED,
            actor="agent-a",
        )
        assert len(entries) == 1


class TestAuditTrailSingleton:
    """Tests for the global singleton."""

    def test_get_audit_trail_returns_same_instance(self):
        reset_audit_trail()
        t1 = get_audit_trail()
        t2 = get_audit_trail()
        assert t1 is t2

    def test_reset_creates_new_instance(self):
        reset_audit_trail()
        t1 = get_audit_trail()
        reset_audit_trail()
        t2 = get_audit_trail()
        assert t1 is not t2
