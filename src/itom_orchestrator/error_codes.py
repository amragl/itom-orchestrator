"""
Error code constants for the ITOM Orchestrator.

All error codes follow the ``ORCH_XXXX`` format grouped by domain.
Each constant is a string suitable for use in structured error responses
and machine-readable logging.
"""

# ---------------------------------------------------------------------------
# Registry errors (ORCH_1xxx)
# ---------------------------------------------------------------------------

ORCH_1001_AGENT_NOT_FOUND = "ORCH_1001"
"""Agent ID not found in the registry."""

ORCH_1002_AGENT_ALREADY_REGISTERED = "ORCH_1002"
"""Agent with the given ID is already registered."""

ORCH_1003_AGENT_REGISTRATION_INVALID = "ORCH_1003"
"""Agent registration payload failed validation."""

ORCH_1004_REGISTRY_LOAD_FAILED = "ORCH_1004"
"""Failed to load the agent registry from persistent storage."""

ORCH_1005_REGISTRY_SAVE_FAILED = "ORCH_1005"
"""Failed to persist the agent registry to storage."""

# ---------------------------------------------------------------------------
# Routing errors (ORCH_2xxx)
# ---------------------------------------------------------------------------

ORCH_2001_NO_ROUTE_FOUND = "ORCH_2001"
"""No agent could be matched for the given task domain/capability."""

ORCH_2002_AGENT_UNAVAILABLE = "ORCH_2002"
"""The target agent exists but is currently unavailable (unhealthy or offline)."""

ORCH_2003_ROUTING_RULE_INVALID = "ORCH_2003"
"""A routing rule failed validation on load."""

ORCH_2004_ROUTING_RULES_LOAD_FAILED = "ORCH_2004"
"""Failed to load routing rules configuration."""

ORCH_2005_AMBIGUOUS_ROUTE = "ORCH_2005"
"""Multiple agents matched with equal priority; cannot determine best route."""

# ---------------------------------------------------------------------------
# Workflow errors (ORCH_3xxx)
# ---------------------------------------------------------------------------

ORCH_3001_WORKFLOW_NOT_FOUND = "ORCH_3001"
"""Workflow definition or execution not found."""

ORCH_3002_WORKFLOW_INVALID_TRANSITION = "ORCH_3002"
"""Attempted an invalid state machine transition."""

ORCH_3003_WORKFLOW_STEP_FAILED = "ORCH_3003"
"""A workflow step execution failed (FAIL-STOP triggered)."""

ORCH_3004_WORKFLOW_TIMEOUT = "ORCH_3004"
"""Workflow or step exceeded the configured timeout."""

ORCH_3005_WORKFLOW_CHECKPOINT_FAILED = "ORCH_3005"
"""Failed to save or restore a workflow checkpoint."""

ORCH_3006_WORKFLOW_DEFINITION_INVALID = "ORCH_3006"
"""Workflow definition failed schema validation."""

# ---------------------------------------------------------------------------
# Communication errors (ORCH_4xxx)
# ---------------------------------------------------------------------------

ORCH_4001_MESSAGE_DELIVERY_FAILED = "ORCH_4001"
"""Failed to deliver a message to the target agent."""

ORCH_4002_MESSAGE_INVALID = "ORCH_4002"
"""Message payload failed validation."""

ORCH_4003_EVENT_DISPATCH_FAILED = "ORCH_4003"
"""Failed to dispatch an event on the event bus."""

ORCH_4004_NOTIFICATION_FAILED = "ORCH_4004"
"""Failed to send a notification to an agent."""

ORCH_4005_CALLBACK_TIMEOUT = "ORCH_4005"
"""Agent callback did not respond within the timeout window."""

# ---------------------------------------------------------------------------
# Persistence errors (ORCH_5xxx)
# ---------------------------------------------------------------------------

ORCH_5001_STATE_WRITE_FAILED = "ORCH_5001"
"""Failed to write state to persistent storage."""

ORCH_5002_STATE_READ_FAILED = "ORCH_5002"
"""Failed to read state from persistent storage."""

ORCH_5003_STATE_CORRUPTED = "ORCH_5003"
"""State file exists but contains invalid or corrupted data."""

ORCH_5004_STATE_LOCK_FAILED = "ORCH_5004"
"""Failed to acquire a file lock for atomic state write."""

ORCH_5005_STATE_VERSION_MISMATCH = "ORCH_5005"
"""State file version does not match the expected schema version."""

# ---------------------------------------------------------------------------
# Role enforcement errors (ORCH_6xxx)
# ---------------------------------------------------------------------------

ORCH_6001_ROLE_VIOLATION = "ORCH_6001"
"""Agent attempted an action outside its defined role boundaries."""

ORCH_6002_ROLE_DEFINITION_MISSING = "ORCH_6002"
"""No role boundary definition found for the specified agent."""

ORCH_6003_ROLE_CONFIG_INVALID = "ORCH_6003"
"""Role boundary configuration file failed validation."""

ORCH_6004_AUDIT_WRITE_FAILED = "ORCH_6004"
"""Failed to write an entry to the audit trail."""

# ---------------------------------------------------------------------------
# Task execution errors (ORCH_7xxx)
# ---------------------------------------------------------------------------

ORCH_7001_TASK_EXECUTION_FAILED = "ORCH_7001"
"""Task execution failed after all retry attempts."""

ORCH_7002_TASK_TIMEOUT = "ORCH_7002"
"""Task execution exceeded the configured timeout."""

ORCH_7003_TASK_INVALID = "ORCH_7003"
"""Task payload failed validation."""

ORCH_7004_TASK_RETRY_EXHAUSTED = "ORCH_7004"
"""All retry attempts for the task have been exhausted."""
