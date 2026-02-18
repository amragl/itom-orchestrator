# itom-orchestrator

Central coordinator for all ITOM agents -- routes tasks, manages workflows, enforces role boundaries, handles cross-agent communication, maintains execution state.

A project managed by [Agent Forge](https://github.com/amragl/agent-forge).

## Part of: ServiceNow Suite

This project is part of the **servicenow-suite** program -- a full ITOM ServiceNow automation stack.

## Tech Stack

- **Language:** Python 3.11+
- **MCP Framework:** [FastMCP](https://github.com/jlowin/fastmcp) >= 2.0
- **Validation:** [Pydantic](https://docs.pydantic.dev/) >= 2.0
- **Configuration:** [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) >= 2.0
- **Build System:** [Hatch](https://hatch.pypa.io/) (hatchling)
- **Package Manager:** [uv](https://docs.astral.sh/uv/)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/amragl/itom-orchestrator.git
cd itom-orchestrator

# Install dependencies (requires uv)
uv sync --extra dev

# Copy and configure environment
cp .env.example .env

# Run tests
uv run pytest tests/ -v

# Run linters
uv run ruff check src/ tests/
uv run black --check src/ tests/
uv run mypy src/itom_orchestrator/
```

## Development Setup

### Prerequisites

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) package manager

### Install

```bash
# Install all dependencies (production + dev)
uv sync --extra dev
```

### Common Tasks

Use the Makefile for common development tasks:

```bash
make help          # Show all available targets
make install-dev   # Install all dependencies
make test          # Run test suite
make lint          # Run ruff, black --check, and mypy
make format        # Auto-format code
make typecheck     # Run mypy strict type checking
make clean         # Remove caches and build artifacts
```

### Configuration

All configuration is via environment variables with the `ORCH_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCH_DATA_DIR` | `.itom-orchestrator` | Root data directory for state and configs |
| `ORCH_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `ORCH_LOG_DIR` | `<data_dir>/logs` | Directory for log files |

See `.env.example` for the full list.

## Architecture

### Workflow Engine (ORCH-012)

The workflow engine executes multi-step workflow definitions with dependency ordering:

- **WorkflowEngine** -- executes steps in topological order based on `depends_on` declarations
- **WorkflowTemplateRegistry** -- pre-built templates for common ITOM operations (CMDB health check, incident response, discovery audit, asset lifecycle)
- **WorkflowCheckpointer** -- saves/restores execution state to JSON files for resumption after interruptions

### Messaging System (ORCH-015, ORCH-016, ORCH-017)

Inter-agent communication is handled by three complementary components:

- **MessageQueue** -- priority-based in-memory message queue for point-to-point agent messaging
- **EventBus** -- synchronous publish/subscribe event bus for workflow lifecycle and agent status events
- **NotificationManager** -- unified notification interface combining the queue and event bus

### Role Enforcement (ORCH-018, ORCH-020)

Role-based access control for agent actions:

- **RoleEnforcer** -- checks permissions for role/action/domain combinations
- Default policies for all 6 ITOM agents: orchestrator (admin), cmdb-agent, discovery-agent, asset-agent, itom-auditor (read-only), itom-documentator
- JSON-based policy configuration with load/save/validate functions

### Audit Trail (ORCH-019)

Records all significant actions for compliance and debugging:

- **AuditTrail** -- in-memory audit log with filtering by event type, actor, and timestamp
- Supports JSON export for external analysis
- Integrated with role enforcer for permission check auditing

### Routing Configuration (ORCH-010)

Externalized routing rules via JSON configuration:

- **RoutingConfig** -- Pydantic model for routing rules
- **RoutingRulesLoader** -- file-based loading with validation, caching, and hot-reload

## Project Structure

```
itom-orchestrator/
  src/
    itom_orchestrator/
      __init__.py              # Package exports, version
      config.py                # Pydantic BaseSettings configuration
      logging_config.py        # Structured JSON logging
      error_codes.py           # ORCH_XXXX error code constants
      server.py                # FastMCP server entry point
      http_server.py           # FastAPI HTTP server
      router.py                # Task routing engine
      executor.py              # Task execution with retry/timeout
      registry.py              # Agent registry
      health.py                # Agent health checking
      workflow_engine.py       # Workflow state machine and execution
      workflow_templates.py    # Pre-built workflow templates
      workflow_checkpoint.py   # Workflow state checkpointing
      messaging.py             # Inter-agent message queue
      event_bus.py             # Publish/subscribe event bus
      notifications.py         # Agent notification manager
      role_enforcer.py         # Role-based access control
      audit_trail.py           # Audit trail recording
      routing_config.py        # Routing rules configuration
      models/                  # Pydantic models (agents, tasks, workflows, messages)
      api/                     # FastAPI routes
  tests/
    conftest.py                # Shared fixtures
    unit/                      # Unit tests (24 files)
    integration/               # Integration tests (3 files)
  Dockerfile                   # Container image
  docker-compose.yml           # Docker Compose config
  pyproject.toml               # Package config (hatchling build)
  Makefile                     # Development task runner
  .env.example                 # Environment variable template
```

## Docker

```bash
# Build and run
docker compose up --build

# Or build manually
docker build -t itom-orchestrator .
docker run -p 8000:8000 itom-orchestrator
```
