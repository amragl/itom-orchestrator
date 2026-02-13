"""
Tests for itom_orchestrator.config -- OrchestratorConfig loading, defaults, and env vars.
"""

from pathlib import Path

import pytest

from itom_orchestrator.config import OrchestratorConfig, get_config, reset_config


class TestOrchestratorConfigDefaults:
    """Verify that OrchestratorConfig has correct defaults when no env vars are set."""

    def test_default_data_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config = OrchestratorConfig()
        assert config.data_dir == ".itom-orchestrator"

    def test_default_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_LOG_LEVEL", raising=False)
        config = OrchestratorConfig()
        assert config.log_level == "INFO"

    def test_default_log_dir_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_LOG_DIR", raising=False)
        config = OrchestratorConfig()
        assert config.log_dir == ""

    def test_computed_state_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config = OrchestratorConfig()
        expected = str(Path(".itom-orchestrator") / "state")
        assert config.state_dir == expected

    def test_computed_resolved_log_dir_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When log_dir is empty, resolved_log_dir falls back to <data_dir>/logs."""
        monkeypatch.delenv("ORCH_LOG_DIR", raising=False)
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config = OrchestratorConfig()
        expected = str(Path(".itom-orchestrator") / "logs")
        assert config.resolved_log_dir == expected


class TestOrchestratorConfigEnvOverrides:
    """Verify that environment variables override defaults."""

    def test_env_data_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_DATA_DIR", "/tmp/my-data")
        config = OrchestratorConfig()
        assert config.data_dir == "/tmp/my-data"

    def test_env_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_LOG_LEVEL", "DEBUG")
        config = OrchestratorConfig()
        assert config.log_level == "DEBUG"

    def test_env_log_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_LOG_DIR", "/var/log/itom")
        config = OrchestratorConfig()
        assert config.log_dir == "/var/log/itom"

    def test_env_log_dir_overrides_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When log_dir is explicitly set, resolved_log_dir uses it directly."""
        monkeypatch.setenv("ORCH_LOG_DIR", "/var/log/itom")
        config = OrchestratorConfig()
        assert config.resolved_log_dir == "/var/log/itom"

    def test_env_data_dir_affects_state_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCH_DATA_DIR", "/opt/orch")
        config = OrchestratorConfig()
        assert config.state_dir == str(Path("/opt/orch") / "state")


class TestConfigWithTmpPath:
    """Tests that use the tmp_path-based fixtures from conftest."""

    def test_test_config_uses_tmp_dir(self, test_config: OrchestratorConfig) -> None:
        assert "itom-orchestrator" in test_config.data_dir
        assert test_config.log_level == "DEBUG"

    def test_state_dir_under_tmp(self, test_config: OrchestratorConfig) -> None:
        assert test_config.state_dir.endswith("state")
        assert Path(test_config.data_dir).exists()


class TestGetConfigSingleton:
    """Verify the get_config / reset_config singleton pattern."""

    def test_get_config_returns_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config = get_config()
        assert isinstance(config, OrchestratorConfig)

    def test_get_config_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config_a = get_config()
        config_b = get_config()
        assert config_a is config_b

    def test_reset_config_clears_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCH_DATA_DIR", raising=False)
        config_a = get_config()
        reset_config()
        config_b = get_config()
        assert config_a is not config_b
