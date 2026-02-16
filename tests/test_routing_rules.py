"""
Tests for routing rules configuration loading, validation, and hot-reload.

Tests the RoutingRulesLoader class to ensure proper:
- Configuration file loading from JSON
- Schema validation with error reporting
- Cache management
- Hot-reload detection
- Configuration consistency checks
"""

import json
import tempfile
from pathlib import Path

import pytest

from itom_orchestrator.router import RoutingRulesLoader


class TestRoutingRulesLoaderBasic:
    """Tests for basic routing rules configuration loading."""

    def test_load_valid_config(self):
        """Test loading a valid routing rules configuration."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    "id": "cmdb",
                    "name": "CMDB Operations",
                    "keywords": ["cmdb", "configuration"],
                    "target_agent": "cmdb-agent",
                    "priority": 1,
                }
            },
            "routing_rules": [
                {
                    "id": "rule-001",
                    "name": "CMDB Operations",
                    "priority": 10,
                    "domain": "cmdb",
                    "keywords": ["cmdb"],
                    "target_agent": "cmdb-agent",
                }
            ],
            "capability_mappings": {
                "cmdb_read": {
                    "domain": "cmdb",
                    "capability_name": "cmdb_read",
                    "agents": ["cmdb-agent"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(str(config_path), validate_on_load=True)
            config = loader.load()

            assert config["version"] == "1.0.0"
            assert len(config["domains"]) == 1
            assert len(config["routing_rules"]) == 1
            assert len(config["capability_mappings"]) == 1

    def test_load_missing_file(self):
        """Test loading when config file does not exist."""
        loader = RoutingRulesLoader("/nonexistent/path/routing-rules.json")

        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_load_invalid_json(self):
        """Test loading invalid JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                f.write("{ invalid json }")

            loader = RoutingRulesLoader(str(config_path), validate_on_load=False)

            with pytest.raises(ValueError, match="Invalid JSON"):
                loader.load()

    def test_load_missing_required_field(self):
        """Test validation fails when required fields are missing."""
        config_data = {
            "version": "1.0.0",
            # Missing 'domains', 'routing_rules', 'capability_mappings'
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(str(config_path), validate_on_load=True)

            with pytest.raises(ValueError, match="validation failed"):
                loader.load()


class TestRoutingRulesValidation:
    """Tests for configuration validation logic."""

    def test_validate_valid_config(self):
        """Test validation passes for valid configuration."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    "id": "cmdb",
                    "name": "CMDB Operations",
                    "keywords": ["cmdb"],
                }
            },
            "routing_rules": [
                {
                    "id": "rule-001",
                    "name": "CMDB",
                    "priority": 10,
                }
            ],
            "capability_mappings": {
                "cmdb_read": {
                    "domain": "cmdb",
                    "agents": ["cmdb-agent"],
                }
            },
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert len(errors) == 0

    def test_validate_missing_domain_field(self):
        """Test validation detects missing domain 'id' field."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    # Missing 'id'
                    "name": "CMDB Operations",
                    "keywords": ["cmdb"],
                }
            },
            "routing_rules": [],
            "capability_mappings": {},
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert any("Domain" in error and "missing 'id'" in error for error in errors)

    def test_validate_domain_reference_in_rule(self):
        """Test validation detects undefined domain references in rules."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    "id": "cmdb",
                    "name": "CMDB",
                    "keywords": ["cmdb"],
                }
            },
            "routing_rules": [
                {
                    "id": "rule-001",
                    "name": "Test Rule",
                    "priority": 10,
                    "domain": "undefined_domain",  # References undefined domain
                }
            ],
            "capability_mappings": {},
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert any("undefined domain" in error for error in errors)

    def test_validate_domain_reference_in_capability(self):
        """Test validation detects undefined domain references in capabilities."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    "id": "cmdb",
                    "name": "CMDB",
                    "keywords": ["cmdb"],
                }
            },
            "routing_rules": [],
            "capability_mappings": {
                "cmdb_read": {
                    "domain": "undefined_domain",  # References undefined domain
                    "agents": ["cmdb-agent"],
                }
            },
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert any("undefined domain" in error for error in errors)

    def test_validate_invalid_rule_priority(self):
        """Test validation detects invalid priority values in rules."""
        config_data = {
            "version": "1.0.0",
            "domains": {
                "cmdb": {
                    "id": "cmdb",
                    "name": "CMDB",
                    "keywords": ["cmdb"],
                }
            },
            "routing_rules": [
                {
                    "id": "rule-001",
                    "name": "Test Rule",
                    "priority": "invalid_priority",  # Should be int
                }
            ],
            "capability_mappings": {},
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert any("priority" in error.lower() for error in errors)


class TestRoutingRulesCaching:
    """Tests for configuration caching."""

    def test_cache_config_on_load(self):
        """Test that config is cached when cache_config=True."""
        config_data = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(
                str(config_path), validate_on_load=False, cache_config=True
            )
            loaded_config = loader.load()

            cached_config = loader.get_cached_config()

            assert cached_config is not None
            assert cached_config == loaded_config

    def test_no_cache_when_disabled(self):
        """Test that config is not cached when cache_config=False."""
        config_data = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(
                str(config_path), validate_on_load=False, cache_config=False
            )
            loader.load()

            cached_config = loader.get_cached_config()

            assert cached_config is None

    def test_clear_cache(self):
        """Test clearing the cache."""
        config_data = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(
                str(config_path), validate_on_load=False, cache_config=True
            )
            loader.load()

            assert loader.get_cached_config() is not None

            loader.clear_cache()

            assert loader.get_cached_config() is None


class TestRoutingRulesHotReload:
    """Tests for hot-reload detection."""

    def test_needs_reload_on_file_modification(self):
        """Test that needs_reload() detects file modifications."""
        import time

        config_data = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(
                str(config_path),
                validate_on_load=False,
                cache_config=True,
                enable_hot_reload=True,
            )
            loader.load()

            # File has not been modified yet
            assert not loader.needs_reload()

            # Modify the file
            time.sleep(0.1)  # Ensure mtime changes
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            # Now it should detect the modification
            assert loader.needs_reload()

    def test_hot_reload_disabled(self):
        """Test that hot-reload detection is disabled when enable_hot_reload=False."""
        import time

        config_data = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routing-rules.json"
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            loader = RoutingRulesLoader(
                str(config_path),
                validate_on_load=False,
                cache_config=True,
                enable_hot_reload=False,
            )
            loader.load()

            # Modify the file
            time.sleep(0.1)
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            # Should return False since hot-reload is disabled
            assert not loader.needs_reload()


class TestRoutingRulesValidationErrors:
    """Tests for validation error reporting."""

    def test_validation_errors_property(self):
        """Test that validation errors are properly reported."""
        config_data = {
            "version": "1.0.0",
            # Missing required fields
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)
        errors = loader.validate(config_data)

        assert len(errors) > 0
        # validation_errors returns a copy, so compare by content
        assert len(loader.validation_errors) > 0
        assert sorted(loader.validation_errors) == sorted(errors)

    def test_validation_errors_cleared_on_successful_validation(self):
        """Test that errors are cleared on successful validation."""
        config_data_invalid = {"version": "1.0.0"}
        config_data_valid = {
            "version": "1.0.0",
            "domains": {},
            "routing_rules": [],
            "capability_mappings": {},
        }

        loader = RoutingRulesLoader("dummy", validate_on_load=False)

        # First validation fails
        errors1 = loader.validate(config_data_invalid)
        assert len(errors1) > 0

        # Second validation succeeds
        errors2 = loader.validate(config_data_valid)
        assert len(errors2) == 0
        assert loader.validation_errors == []
