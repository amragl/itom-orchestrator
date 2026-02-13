"""
Tests for the StatePersistence class and singleton access functions.

Covers:
- Basic CRUD (save/load/delete/exists/list_keys)
- Pydantic model serialization and deserialization
- Metadata envelope verification
- Atomic write cleanup
- Key validation
- Corrupted file handling
- Auto-directory creation
- Version mismatch warnings
- Datetime / Enum / Path serialization
- Singleton lifecycle
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from itom_orchestrator.models.agents import (
    AgentCapability,
    AgentDomain,
    AgentRegistration,
    AgentStatus,
)
from itom_orchestrator.persistence import (
    StatePersistence,
    get_persistence,
    reset_persistence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_raw(path: Path) -> dict[str, Any]:
    """Read a JSON file and return the parsed dict."""
    with open(path, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
    return result


def _make_agent_registration() -> AgentRegistration:
    """Create a sample AgentRegistration for testing."""
    return AgentRegistration(
        agent_id="test-agent",
        name="Test Agent",
        description="An agent used in persistence tests",
        domain=AgentDomain.CMDB,
        capabilities=[
            AgentCapability(
                name="query_cis",
                domain=AgentDomain.CMDB,
                description="Query configuration items",
            ),
        ],
        status=AgentStatus.ONLINE,
        registered_at=datetime(2026, 1, 15, 10, 30, 0),
        last_health_check=datetime(2026, 2, 13, 8, 0, 0),
        metadata={"version": "1.0"},
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    """Tests for save() and load() with plain dict data."""

    def test_save_and_load_dict(self, tmp_path: Path) -> None:
        """Save a dict, load it back, verify equality."""
        ps = StatePersistence(tmp_path / "state")
        data = {"name": "orchestrator", "count": 42, "tags": ["a", "b"]}

        saved_path = ps.save("test-data", data)

        assert saved_path.exists()
        loaded = ps.load("test-data")
        assert loaded == data

    def test_save_returns_correct_path(self, tmp_path: Path) -> None:
        """save() returns the path to the .json file."""
        ps = StatePersistence(tmp_path / "state")
        path = ps.save("my-key", {"x": 1})
        assert path == tmp_path / "state" / "my-key.json"

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """load() returns None when the file does not exist."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.load("missing-key") is None

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        """Saving to the same key overwrites the previous data."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("overwrite-test", {"version": 1})
        ps.save("overwrite-test", {"version": 2})

        loaded = ps.load("overwrite-test")
        assert loaded is not None
        assert loaded["version"] == 2


# ---------------------------------------------------------------------------
# Pydantic model save/load
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Tests for save/load with Pydantic models."""

    def test_save_pydantic_model(self, tmp_path: Path) -> None:
        """A Pydantic model can be saved and loaded as a dict."""
        ps = StatePersistence(tmp_path / "state")
        agent = _make_agent_registration()

        ps.save("agent-reg", agent)
        loaded = ps.load("agent-reg")

        assert loaded is not None
        assert loaded["agent_id"] == "test-agent"
        assert loaded["domain"] == "cmdb"
        assert loaded["status"] == "online"

    def test_load_model_roundtrip(self, tmp_path: Path) -> None:
        """load_model() parses data back into the correct Pydantic model."""
        ps = StatePersistence(tmp_path / "state")
        original = _make_agent_registration()

        ps.save("agent-roundtrip", original)
        restored = ps.load_model("agent-roundtrip", AgentRegistration)

        assert restored is not None
        assert restored.agent_id == original.agent_id
        assert restored.domain == original.domain
        assert restored.status == original.status
        assert len(restored.capabilities) == 1
        assert restored.capabilities[0].name == "query_cis"

    def test_load_model_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """load_model() returns None when the file does not exist."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.load_model("no-such-key", AgentRegistration) is None

    def test_load_model_invalid_data_raises(self, tmp_path: Path) -> None:
        """load_model() raises ValidationError when data doesn't match the model."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("bad-agent", {"not_a_valid_field": True})

        with pytest.raises(ValidationError):
            ps.load_model("bad-agent", AgentRegistration)


# ---------------------------------------------------------------------------
# Envelope metadata
# ---------------------------------------------------------------------------


class TestEnvelopeMetadata:
    """Tests verifying the _version, _saved_at, _key fields in the saved file."""

    def test_envelope_contains_metadata(self, tmp_path: Path) -> None:
        """The saved file contains _version, _saved_at, and _key fields."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("meta-test", {"hello": "world"})

        raw = _read_raw(tmp_path / "state" / "meta-test.json")

        assert raw["_version"] == StatePersistence.STATE_VERSION
        assert "_saved_at" in raw
        assert raw["_key"] == "meta-test"
        assert raw["data"] == {"hello": "world"}

    def test_saved_at_is_iso_format(self, tmp_path: Path) -> None:
        """The _saved_at field is a valid ISO 8601 timestamp string."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("ts-test", {"x": 1})

        raw = _read_raw(tmp_path / "state" / "ts-test.json")
        saved_at = raw["_saved_at"]

        # Should parse without error
        dt = datetime.fromisoformat(saved_at)
        assert isinstance(dt, datetime)

    def test_get_metadata(self, tmp_path: Path) -> None:
        """get_metadata() returns envelope fields without the data payload."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("md-test", {"big": "payload", "nested": {"a": 1}})

        meta = ps.get_metadata("md-test")

        assert meta is not None
        assert meta["version"] == StatePersistence.STATE_VERSION
        assert meta["key"] == "md-test"
        assert "saved_at" in meta
        # Should NOT contain the data payload
        assert "data" not in meta
        assert "big" not in meta

    def test_get_metadata_nonexistent(self, tmp_path: Path) -> None:
        """get_metadata() returns None for a non-existent key."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.get_metadata("ghost") is None


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    """Tests that the temp file is cleaned up on successful save."""

    def test_tmp_file_removed_after_save(self, tmp_path: Path) -> None:
        """After a successful save, the .json.tmp file should not exist."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("atomic-test", {"safe": True})

        tmp_file = tmp_path / "state" / "atomic-test.json.tmp"
        target_file = tmp_path / "state" / "atomic-test.json"

        assert not tmp_file.exists()
        assert target_file.exists()


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------


class TestKeyValidation:
    """Tests for key format validation."""

    @pytest.mark.parametrize(
        "bad_key",
        [
            "",
            "foo/bar",
            "foo\\bar",
            "../escape",
            "has.dot",
            "has spaces",
            ".hidden",
            "-leading-hyphen",
            "_leading-underscore",
            "special!char",
            "semi;colon",
        ],
    )
    def test_invalid_keys_rejected(self, tmp_path: Path, bad_key: str) -> None:
        """Keys with invalid characters are rejected by save, load, delete, exists."""
        ps = StatePersistence(tmp_path / "state")

        with pytest.raises(ValueError, match="Invalid state key|must not be empty"):
            ps.save(bad_key, {"x": 1})

        with pytest.raises(ValueError, match="Invalid state key|must not be empty"):
            ps.load(bad_key)

        with pytest.raises(ValueError, match="Invalid state key|must not be empty"):
            ps.delete(bad_key)

        with pytest.raises(ValueError, match="Invalid state key|must not be empty"):
            ps.exists(bad_key)

    @pytest.mark.parametrize(
        "good_key",
        [
            "simple",
            "agent-registry",
            "workflow_executions",
            "data123",
            "A-B-C",
            "camelCase",
        ],
    )
    def test_valid_keys_accepted(self, tmp_path: Path, good_key: str) -> None:
        """Valid keys are accepted without error."""
        ps = StatePersistence(tmp_path / "state")
        ps.save(good_key, {"ok": True})
        loaded = ps.load(good_key)
        assert loaded == {"ok": True}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    """Tests for the delete() method."""

    def test_delete_existing(self, tmp_path: Path) -> None:
        """Delete an existing key returns True and removes the file."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("to-delete", {"x": 1})

        assert ps.delete("to-delete") is True
        assert not (tmp_path / "state" / "to-delete.json").exists()
        assert ps.load("to-delete") is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        """Delete a non-existent key returns False."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.delete("not-here") is False


# ---------------------------------------------------------------------------
# Exists
# ---------------------------------------------------------------------------


class TestExists:
    """Tests for the exists() method."""

    def test_exists_after_save(self, tmp_path: Path) -> None:
        """exists() returns True after saving a key."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.exists("check-me") is False

        ps.save("check-me", {"v": 1})
        assert ps.exists("check-me") is True

    def test_exists_after_delete(self, tmp_path: Path) -> None:
        """exists() returns False after deleting a key."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("delete-check", {"v": 1})
        ps.delete("delete-check")
        assert ps.exists("delete-check") is False


# ---------------------------------------------------------------------------
# List keys
# ---------------------------------------------------------------------------


class TestListKeys:
    """Tests for the list_keys() method."""

    def test_list_keys_returns_all(self, tmp_path: Path) -> None:
        """list_keys() returns all saved keys sorted alphabetically."""
        ps = StatePersistence(tmp_path / "state")
        ps.save("charlie", {"c": 3})
        ps.save("alpha", {"a": 1})
        ps.save("bravo", {"b": 2})

        keys = ps.list_keys()
        assert keys == ["alpha", "bravo", "charlie"]

    def test_list_keys_empty_dir(self, tmp_path: Path) -> None:
        """list_keys() returns an empty list for an empty state directory."""
        ps = StatePersistence(tmp_path / "state")
        assert ps.list_keys() == []

    def test_list_keys_ignores_tmp_files(self, tmp_path: Path) -> None:
        """list_keys() does not include .json.tmp files."""
        state_dir = tmp_path / "state"
        ps = StatePersistence(state_dir)
        ps.save("real-key", {"v": 1})

        # Manually create a stale tmp file
        (state_dir / "orphan.json.tmp").write_text("{}", encoding="utf-8")

        keys = ps.list_keys()
        assert "orphan" not in keys
        assert keys == ["real-key"]


# ---------------------------------------------------------------------------
# Corrupted file handling
# ---------------------------------------------------------------------------


class TestCorruptedFile:
    """Tests for graceful handling of corrupted JSON files."""

    def test_load_corrupted_json(self, tmp_path: Path) -> None:
        """load() returns None (does not crash) when the file contains invalid JSON."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        ps = StatePersistence(state_dir)

        # Write invalid JSON directly
        (state_dir / "corrupted.json").write_text("not valid json {{{{", encoding="utf-8")

        result = ps.load("corrupted")
        assert result is None

    def test_get_metadata_corrupted_json(self, tmp_path: Path) -> None:
        """get_metadata() returns None for a corrupted file."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        ps = StatePersistence(state_dir)

        (state_dir / "bad-meta.json").write_text("{broken", encoding="utf-8")

        result = ps.get_metadata("bad-meta")
        assert result is None


# ---------------------------------------------------------------------------
# Auto-directory creation
# ---------------------------------------------------------------------------


class TestAutoDirectoryCreation:
    """Tests that the state directory is created automatically."""

    def test_creates_nested_directory(self, tmp_path: Path) -> None:
        """StatePersistence creates the state dir and parents if they don't exist."""
        deep_path = tmp_path / "a" / "b" / "c" / "state"
        assert not deep_path.exists()

        ps = StatePersistence(deep_path)

        assert deep_path.exists()
        assert deep_path.is_dir()
        assert ps.state_dir == deep_path


# ---------------------------------------------------------------------------
# Version mismatch
# ---------------------------------------------------------------------------


class TestVersionMismatch:
    """Tests for handling state files with a different version."""

    def test_load_logs_warning_on_version_mismatch(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load() logs a warning when the file version differs from STATE_VERSION."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        ps = StatePersistence(state_dir)

        # Write a file with a different version
        envelope = {
            "_version": 999,
            "_saved_at": "2026-01-01T00:00:00",
            "_key": "old-version",
            "data": {"legacy": True},
        }
        (state_dir / "old-version.json").write_text(json.dumps(envelope), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            result = ps.load("old-version")

        assert result == {"legacy": True}
        assert any("version mismatch" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Datetime serialization
# ---------------------------------------------------------------------------


class TestDatetimeSerialization:
    """Tests that datetime objects are serialized to ISO format."""

    def test_datetime_in_dict_data(self, tmp_path: Path) -> None:
        """Datetime values in dict data are stored as ISO format strings."""
        ps = StatePersistence(tmp_path / "state")
        dt = datetime(2026, 2, 13, 14, 30, 0)
        ps.save("dt-test", {"timestamp": dt, "name": "test"})

        raw = _read_raw(tmp_path / "state" / "dt-test.json")
        assert raw["data"]["timestamp"] == "2026-02-13T14:30:00"

    def test_pydantic_model_with_datetimes(self, tmp_path: Path) -> None:
        """Pydantic models with datetime fields serialize correctly."""
        ps = StatePersistence(tmp_path / "state")
        agent = _make_agent_registration()

        ps.save("agent-dt", agent)
        raw = _read_raw(tmp_path / "state" / "agent-dt.json")

        # Pydantic model_dump(mode="json") converts datetimes to ISO strings
        assert isinstance(raw["data"]["registered_at"], str)
        assert "2026-01-15" in raw["data"]["registered_at"]


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for get_persistence() and reset_persistence()."""

    def test_get_persistence_returns_same_instance(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_persistence() returns the same instance on repeated calls."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))

        p1 = get_persistence()
        p2 = get_persistence()

        assert p1 is p2

    def test_reset_clears_singleton(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reset_persistence() clears the singleton so a new instance is created."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))

        p1 = get_persistence()
        reset_persistence()
        p2 = get_persistence()

        assert p1 is not p2

    def test_singleton_uses_config_state_dir(
        self, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The singleton instance uses the state_dir from config."""
        monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))

        ps = get_persistence()
        assert str(ps.state_dir) == str(Path(tmp_data_dir) / "state")


# ---------------------------------------------------------------------------
# State dir property
# ---------------------------------------------------------------------------


class TestStateDir:
    """Tests for the state_dir property."""

    def test_state_dir_returns_path(self, tmp_path: Path) -> None:
        """state_dir returns the Path object for the state directory."""
        state_dir = tmp_path / "my-state"
        ps = StatePersistence(state_dir)
        assert ps.state_dir == state_dir
        assert isinstance(ps.state_dir, Path)
