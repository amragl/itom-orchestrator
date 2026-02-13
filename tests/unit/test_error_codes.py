"""
Tests for itom_orchestrator.error_codes -- verify error code format and uniqueness.
"""

import itom_orchestrator.error_codes as ec


class TestErrorCodeFormat:
    """Verify that all error codes follow the ORCH_XXXX format."""

    def _get_all_error_codes(self) -> list[tuple[str, str]]:
        """Return all (name, value) pairs that are error code constants."""
        return [
            (name, getattr(ec, name))
            for name in dir(ec)
            if name.startswith("ORCH_") and isinstance(getattr(ec, name), str)
        ]

    def test_all_codes_follow_format(self) -> None:
        codes = self._get_all_error_codes()
        assert len(codes) > 0, "No error codes found"
        for name, value in codes:
            assert value.startswith("ORCH_"), f"{name} value {value!r} does not start with ORCH_"
            # Value should be ORCH_ followed by digits
            suffix = value.removeprefix("ORCH_")
            assert suffix.isdigit(), f"{name} value {value!r} suffix is not numeric: {suffix!r}"

    def test_all_codes_unique(self) -> None:
        codes = self._get_all_error_codes()
        values = [v for _, v in codes]
        assert len(values) == len(set(values)), "Duplicate error code values found"

    def test_registry_errors_in_1xxx(self) -> None:
        assert ec.ORCH_1001_AGENT_NOT_FOUND == "ORCH_1001"
        assert ec.ORCH_1005_REGISTRY_SAVE_FAILED == "ORCH_1005"

    def test_routing_errors_in_2xxx(self) -> None:
        assert ec.ORCH_2001_NO_ROUTE_FOUND == "ORCH_2001"
        assert ec.ORCH_2005_AMBIGUOUS_ROUTE == "ORCH_2005"

    def test_workflow_errors_in_3xxx(self) -> None:
        assert ec.ORCH_3001_WORKFLOW_NOT_FOUND == "ORCH_3001"
        assert ec.ORCH_3006_WORKFLOW_DEFINITION_INVALID == "ORCH_3006"

    def test_communication_errors_in_4xxx(self) -> None:
        assert ec.ORCH_4001_MESSAGE_DELIVERY_FAILED == "ORCH_4001"
        assert ec.ORCH_4005_CALLBACK_TIMEOUT == "ORCH_4005"

    def test_persistence_errors_in_5xxx(self) -> None:
        assert ec.ORCH_5001_STATE_WRITE_FAILED == "ORCH_5001"
        assert ec.ORCH_5005_STATE_VERSION_MISMATCH == "ORCH_5005"

    def test_role_enforcement_errors_in_6xxx(self) -> None:
        assert ec.ORCH_6001_ROLE_VIOLATION == "ORCH_6001"
        assert ec.ORCH_6004_AUDIT_WRITE_FAILED == "ORCH_6004"

    def test_task_execution_errors_in_7xxx(self) -> None:
        assert ec.ORCH_7001_TASK_EXECUTION_FAILED == "ORCH_7001"
        assert ec.ORCH_7004_TASK_RETRY_EXHAUSTED == "ORCH_7004"
