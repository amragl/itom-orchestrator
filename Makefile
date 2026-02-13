.PHONY: install install-dev test test-cov lint format typecheck run clean all help

PYTHON := uv run python
PYTEST := uv run pytest
RUFF := uv run ruff
BLACK := uv run black
MYPY := uv run mypy

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	uv sync

install-dev: ## Install all dependencies including dev extras
	uv sync --extra dev

test: ## Run test suite
	$(PYTEST) --tb=short

test-cov: ## Run tests with coverage report
	$(PYTEST) --cov=src/itom_orchestrator --cov-report=term-missing --cov-report=html --tb=short

lint: ## Run all linters (ruff, black --check, mypy)
	$(RUFF) check src/ tests/
	$(BLACK) --check src/ tests/
	$(MYPY) src/itom_orchestrator/

format: ## Auto-format code with ruff and black
	$(RUFF) check --fix src/ tests/
	$(BLACK) src/ tests/

typecheck: ## Run mypy strict type checking
	$(MYPY) src/itom_orchestrator/

run: ## Start the MCP server
	$(PYTHON) -m itom_orchestrator.server

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

all: lint test ## Run lint and test
