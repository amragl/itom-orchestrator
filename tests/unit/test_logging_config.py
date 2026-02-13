"""
Tests for itom_orchestrator.logging_config -- structured JSON logging setup.
"""

import json
import logging
from pathlib import Path

from itom_orchestrator.logging_config import (
    StructuredJsonFormatter,
    StructuredLoggerAdapter,
    agent_name_var,
    correlation_id_var,
    generate_correlation_id,
    get_structured_logger,
    setup_logging,
    workflow_id_var,
)


class TestGenerateCorrelationId:
    """Verify correlation ID generation."""

    def test_returns_12_char_hex(self) -> None:
        cid = generate_correlation_id()
        assert len(cid) == 12
        # Must be valid hexadecimal
        int(cid, 16)

    def test_unique_ids(self) -> None:
        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestStructuredJsonFormatter:
    """Verify that the JSON formatter produces valid structured output."""

    def test_basic_format(self) -> None:
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "Hello world"
        assert "timestamp" in data

    def test_correlation_context_attached(self) -> None:
        formatter = StructuredJsonFormatter()
        token = correlation_id_var.set("abc123")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data["correlation_id"] == "abc123"
        finally:
            correlation_id_var.reset(token)

    def test_workflow_id_attached(self) -> None:
        formatter = StructuredJsonFormatter()
        token = workflow_id_var.set("wf-001")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data["workflow_id"] == "wf-001"
        finally:
            workflow_id_var.reset(token)

    def test_agent_name_attached(self) -> None:
        formatter = StructuredJsonFormatter()
        token = agent_name_var.set("cmdb-agent")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert data["agent_name"] == "cmdb-agent"
        finally:
            agent_name_var.reset(token)

    def test_extra_data_attached(self) -> None:
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"key": "value", "count": 42}  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["data"]["key"] == "value"
        assert data["data"]["count"] == 42

    def test_sensitive_keys_redacted(self) -> None:
        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.extra_data = {  # type: ignore[attr-defined]
            "username": "admin",
            "password": "supersecret",
            "api_key": "sk-123",
            "nested": {"token": "tok-abc"},
        }
        output = formatter.format(record)
        data = json.loads(output)
        assert data["data"]["username"] == "admin"
        assert data["data"]["password"] == "***REDACTED***"
        assert data["data"]["api_key"] == "***REDACTED***"
        assert data["data"]["nested"]["token"] == "***REDACTED***"

    def test_exception_info_attached(self) -> None:
        formatter = StructuredJsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="something failed",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["exception"]["type"] == "ValueError"
        assert data["exception"]["message"] == "test error"


class TestGetStructuredLogger:
    """Verify the get_structured_logger factory."""

    def test_returns_adapter(self) -> None:
        logger = get_structured_logger("test.module")
        assert isinstance(logger, StructuredLoggerAdapter)

    def test_structured_method_exists(self) -> None:
        logger = get_structured_logger("test.module")
        assert callable(logger.structured)


class TestSetupLogging:
    """Verify the setup_logging function configures handlers correctly."""

    def test_setup_with_defaults(self) -> None:
        setup_logging(level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        # At least one handler (console)
        assert len(root.handlers) >= 1

    def test_setup_with_file_logging(self, tmp_path: Path) -> None:
        log_dir = str(tmp_path / "logs")
        setup_logging(level="DEBUG", log_dir=log_dir, log_file="test.log")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        # Should have 2 handlers: console + file
        assert len(root.handlers) == 2
        # Verify the log file was created (handler opens the file)
        assert (Path(log_dir) / "test.log").exists()

    def test_setup_creates_log_directory(self, tmp_path: Path) -> None:
        log_dir = str(tmp_path / "nested" / "logs")
        setup_logging(level="INFO", log_dir=log_dir)
        assert Path(log_dir).is_dir()
