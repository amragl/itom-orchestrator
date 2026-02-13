"""
Structured JSON logging configuration for the ITOM Orchestrator.

Provides structured log output with correlation IDs for request tracing,
configurable log levels, and dual output (stderr + file).

Follows the same pattern as servicenow-cmdb-agent-mcp logging_config.
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Context variables for request/workflow tracing
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
workflow_id_var: ContextVar[str] = ContextVar("workflow_id", default="")
agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")


def generate_correlation_id() -> str:
    """Generate a new 12-character correlation ID."""
    return uuid.uuid4().hex[:12]


class StructuredJsonFormatter(logging.Formatter):
    """JSON formatter that outputs structured log entries.

    Each log record is serialised as a single-line JSON object containing
    the timestamp, level, module, function, message, and any attached
    correlation context or extra data.
    """

    SENSITIVE_KEYS = frozenset(
        {
            "password",
            "api_key",
            "secret",
            "token",
            "credential",
            "auth",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        # Attach correlation context if present
        cid = correlation_id_var.get("")
        if cid:
            entry["correlation_id"] = cid

        wid = workflow_id_var.get("")
        if wid:
            entry["workflow_id"] = wid

        agent = agent_name_var.get("")
        if agent:
            entry["agent_name"] = agent

        # Attach extra structured data
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            entry["data"] = self._redact(record.extra_data)

        # Attach exception info
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(entry, default=str)

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive fields from log data."""
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if key.lower() in self.SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            elif isinstance(value, dict):
                redacted[key] = self._redact(value)
            else:
                redacted[key] = value
        return redacted


class StructuredLoggerAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Logger adapter that passes extra structured data to the formatter."""

    def process(  # type: ignore[override]
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        if self.extra:
            extra.update(self.extra)
        kwargs["extra"] = extra
        return msg, kwargs

    def structured(
        self, level: int, msg: str, data: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """Log with structured data attached.

        Args:
            level: Logging level (e.g. ``logging.INFO``).
            msg: Log message.
            data: Optional dictionary of structured data to attach to the record.
            **kwargs: Forwarded to the underlying logger.
        """
        if data:
            kwargs.setdefault("extra", {})["extra_data"] = data
        self.log(level, msg, **kwargs)


def get_structured_logger(name: str) -> StructuredLoggerAdapter:
    """Get a structured logger for a module.

    Args:
        name: Module name (typically ``__name__``).

    Returns:
        A :class:`StructuredLoggerAdapter` that supports the ``structured()``
        method for attaching key-value data to log records.
    """
    base_logger = logging.getLogger(name)
    return StructuredLoggerAdapter(base_logger, {})


def setup_logging(
    level: str = "INFO",
    log_dir: str | None = None,
    log_file: str = "orchestrator.log",
) -> None:
    """Configure structured JSON logging for the orchestrator.

    Call this once at application startup.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory for the log file. If ``None``, file logging is
                 skipped (only stderr output).
        log_file: Log file name within ``log_dir``.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = StructuredJsonFormatter()

    # Console handler (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(console_handler)

    # File handler (optional)
    if log_dir:
        resolved_dir = Path(log_dir)
        resolved_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(resolved_dir / log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
