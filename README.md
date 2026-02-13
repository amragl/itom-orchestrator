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

## Project Structure

```
itom-orchestrator/
  src/
    itom_orchestrator/
      __init__.py          # Package exports, version
      config.py            # Pydantic BaseSettings configuration
      logging_config.py    # Structured JSON logging
      error_codes.py       # ORCH_XXXX error code constants
      server.py            # FastMCP server entry point
      models/              # Pydantic models (agents, tasks, workflows)
  tests/
    conftest.py            # Shared fixtures
    unit/                  # Unit tests
    integration/           # Integration tests
  pyproject.toml           # Package config (hatchling build)
  Makefile                 # Development task runner
  .env.example             # Environment variable template
```
