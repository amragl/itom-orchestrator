"""
Shared test fixtures for itom-orchestrator.

Provides reusable fixtures for:
- OrchestratorConfig with test-safe values using tmp_path isolation
- Module-level singleton cleanup between tests
"""

from collections.abc import Generator
from pathlib import Path

import pytest

from itom_orchestrator.config import OrchestratorConfig

# ---------------------------------------------------------------------------
# Configuration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Return a temporary data directory for orchestrator state files.

    Creates the standard subdirectories (state, logs) so that tests
    operating on the filesystem have a realistic layout.
    """
    data_dir = tmp_path / "itom-orchestrator"
    data_dir.mkdir()
    (data_dir / "state").mkdir()
    (data_dir / "logs").mkdir()
    return data_dir


@pytest.fixture()
def test_config(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> OrchestratorConfig:
    """Return an OrchestratorConfig pointing at a temporary data directory.

    Sets environment variables so that the config picks up the temp paths.
    """
    monkeypatch.setenv("ORCH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("ORCH_LOG_LEVEL", "DEBUG")
    return OrchestratorConfig()


# ---------------------------------------------------------------------------
# Singleton cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_global_singletons() -> Generator[None, None, None]:
    """Reset all module-level global singletons before and after each test.

    This prevents one test's state from leaking into another.
    """
    import itom_orchestrator.config as config_mod
    import itom_orchestrator.http_server as http_server_mod
    import itom_orchestrator.persistence as persistence_mod
    import itom_orchestrator.server as server_mod

    config_mod._config = None
    persistence_mod._persistence = None
    server_mod._registry_instance = None
    server_mod._health_checker_instance = None
    server_mod._router_instance = None
    http_server_mod._registry_instance = None
    http_server_mod._health_checker_instance = None

    yield

    config_mod._config = None
    persistence_mod._persistence = None
    server_mod._registry_instance = None
    server_mod._health_checker_instance = None
    server_mod._router_instance = None
    http_server_mod._registry_instance = None
    http_server_mod._health_checker_instance = None
