"""
JSON file-based state persistence for the ITOM Orchestrator.

Provides atomic writes, auto-directory creation, metadata envelopes
with versioning, and Pydantic model serialization/deserialization.

All orchestrator state operations go through the :class:`StatePersistence`
class or its singleton accessor :func:`get_persistence`.
"""

import json
import logging
import os
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from itom_orchestrator.config import get_config
from itom_orchestrator.logging_config import get_structured_logger

logger: logging.LoggerAdapter[Any] = get_structured_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _json_serializer(obj: object) -> Any:
    """Custom JSON serializer for types not handled by the default encoder.

    Handles:
    - ``datetime`` -- ISO 8601 format string.
    - ``Path`` -- string representation.
    - ``Enum`` -- the ``.value`` attribute.
    - Pydantic ``BaseModel`` -- calls ``model_dump(mode="json")``.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class StatePersistence:
    """JSON file-based state persistence for the orchestrator.

    All state files live in the configured state directory.
    Supports atomic writes, auto-directory creation, and state versioning.
    """

    STATE_VERSION = 1  # Increment when state schema changes

    def __init__(self, state_dir: str | Path) -> None:
        """Initialize with the state directory path.

        Creates the directory (and parents) if it does not exist.
        """
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "StatePersistence initialized",
            extra={"extra_data": {"state_dir": str(self._state_dir)}},
        )

    @staticmethod
    def _validate_key(key: str) -> None:
        """Validate that a key is safe for use as a filename.

        Keys must be alphanumeric with hyphens and underscores only.
        No path separators, dots, or other special characters.

        Raises:
            ValueError: If the key is invalid.
        """
        if not key:
            raise ValueError("State key must not be empty")
        if not _KEY_PATTERN.match(key):
            raise ValueError(
                f"Invalid state key '{key}'. Keys must be alphanumeric with "
                f"hyphens and underscores only (pattern: {_KEY_PATTERN.pattern})"
            )

    def _file_path(self, key: str) -> Path:
        """Return the full file path for a given key."""
        return self._state_dir / f"{key}.json"

    def _tmp_path(self, key: str) -> Path:
        """Return the temporary file path used during atomic writes."""
        return self._state_dir / f"{key}.json.tmp"

    def save(self, key: str, data: dict[str, Any] | BaseModel) -> Path:
        """Save state data to a JSON file.

        Args:
            key: State file identifier (e.g., ``"agent-registry"``,
                ``"workflow-executions"``).
            data: Dict or Pydantic model to persist.

        Returns:
            Path to the saved file.

        Raises:
            ValueError: If the key is invalid.
            OSError: If the file cannot be written.

        Uses atomic writes: write to a temp file, then ``os.replace()``
        to the target path. Wraps data with a metadata envelope containing
        ``_version``, ``_saved_at``, and ``_key``.
        """
        self._validate_key(key)

        # Convert Pydantic models to dicts
        serializable_data = data.model_dump(mode="json") if isinstance(data, BaseModel) else data

        envelope: dict[str, Any] = {
            "_version": self.STATE_VERSION,
            "_saved_at": datetime.now(UTC).isoformat(),
            "_key": key,
            "data": serializable_data,
        }

        target = self._file_path(key)
        tmp = self._tmp_path(key)

        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(envelope, f, indent=2, default=_json_serializer)
                f.write("\n")
            os.replace(tmp, target)
        except OSError:
            # Clean up temp file on failure
            if tmp.exists():
                tmp.unlink()
            logger.error(
                "Failed to save state",
                extra={"extra_data": {"key": key, "path": str(target)}},
                exc_info=True,
            )
            raise

        logger.info(
            "State saved",
            extra={"extra_data": {"key": key, "path": str(target), "version": self.STATE_VERSION}},
        )
        return target

    def load(self, key: str) -> dict[str, Any] | None:
        """Load state data from a JSON file.

        Returns the ``"data"`` field from the envelope, or ``None`` if the
        file does not exist. Logs a warning if the file version does not
        match :attr:`STATE_VERSION`.

        Args:
            key: State file identifier.

        Returns:
            The stored data dictionary, or ``None`` if the file does not exist.

        Raises:
            ValueError: If the key is invalid.
        """
        self._validate_key(key)

        target = self._file_path(key)
        if not target.exists():
            logger.debug(
                "State file not found",
                extra={"extra_data": {"key": key, "path": str(target)}},
            )
            return None

        try:
            with open(target, encoding="utf-8") as f:
                envelope = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.error(
                "Failed to load state -- file corrupted or unreadable",
                extra={"extra_data": {"key": key, "path": str(target)}},
                exc_info=True,
            )
            return None

        # Version check
        file_version = envelope.get("_version")
        if file_version != self.STATE_VERSION:
            logger.warning(
                "State version mismatch",
                extra={
                    "extra_data": {
                        "key": key,
                        "file_version": file_version,
                        "expected_version": self.STATE_VERSION,
                    }
                },
            )

        result: dict[str, Any] = envelope.get("data", {})
        logger.debug(
            "State loaded",
            extra={"extra_data": {"key": key, "version": file_version}},
        )
        return result

    def load_model(self, key: str, model_class: type[T]) -> T | None:
        """Load state and parse into a Pydantic model.

        Args:
            key: State file identifier.
            model_class: The Pydantic model class to parse the data into.

        Returns:
            An instance of ``model_class``, or ``None`` if the file does not exist.

        Raises:
            ValueError: If the key is invalid.
            pydantic.ValidationError: If the data does not match the model schema.
        """
        data = self.load(key)
        if data is None:
            return None
        return model_class.model_validate(data)

    def delete(self, key: str) -> bool:
        """Delete a state file.

        Args:
            key: State file identifier.

        Returns:
            ``True`` if the file was deleted, ``False`` if it did not exist.

        Raises:
            ValueError: If the key is invalid.
        """
        self._validate_key(key)

        target = self._file_path(key)
        if not target.exists():
            logger.debug(
                "State file not found for deletion",
                extra={"extra_data": {"key": key}},
            )
            return False

        target.unlink()
        logger.info(
            "State deleted",
            extra={"extra_data": {"key": key, "path": str(target)}},
        )
        return True

    def exists(self, key: str) -> bool:
        """Check if a state file exists.

        Args:
            key: State file identifier.

        Returns:
            ``True`` if the file exists, ``False`` otherwise.

        Raises:
            ValueError: If the key is invalid.
        """
        self._validate_key(key)
        return self._file_path(key).exists()

    def list_keys(self) -> list[str]:
        """List all state file keys.

        Returns:
            Sorted list of state keys (filenames without the ``.json`` extension).
        """
        keys: list[str] = []
        for path in self._state_dir.iterdir():
            if path.is_file() and path.suffix == ".json" and not path.name.endswith(".json.tmp"):
                keys.append(path.stem)
        return sorted(keys)

    def get_metadata(self, key: str) -> dict[str, Any] | None:
        """Get just the metadata envelope without loading full data.

        Returns the envelope fields (``_version``, ``_saved_at``, ``_key``)
        without the ``data`` payload.

        Args:
            key: State file identifier.

        Returns:
            Metadata dict with ``version``, ``saved_at``, and ``key`` fields,
            or ``None`` if the file does not exist.

        Raises:
            ValueError: If the key is invalid.
        """
        self._validate_key(key)

        target = self._file_path(key)
        if not target.exists():
            return None

        try:
            with open(target, encoding="utf-8") as f:
                envelope = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.error(
                "Failed to read metadata -- file corrupted or unreadable",
                extra={"extra_data": {"key": key, "path": str(target)}},
            )
            return None

        return {
            "version": envelope.get("_version"),
            "saved_at": envelope.get("_saved_at"),
            "key": envelope.get("_key"),
        }

    @property
    def state_dir(self) -> Path:
        """The state directory path."""
        return self._state_dir


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_persistence: StatePersistence | None = None


def get_persistence() -> StatePersistence:
    """Get the singleton :class:`StatePersistence` instance.

    Creates the instance on first call using the state directory from
    :func:`~itom_orchestrator.config.get_config`. Subsequent calls return
    the same instance. Call :func:`reset_persistence` to clear it.
    """
    global _persistence
    if _persistence is None:
        config = get_config()
        _persistence = StatePersistence(config.state_dir)
    return _persistence


def reset_persistence() -> None:
    """Reset the singleton :class:`StatePersistence` instance.

    Intended for use in test fixtures to ensure a clean instance per test.
    """
    global _persistence
    _persistence = None
