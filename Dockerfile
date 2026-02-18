FROM python:3.13-slim AS base

LABEL maintainer="Cesar Garcia Lopez <cesar@cesargl.com>"
LABEL description="ITOM Orchestrator -- central coordinator for all ITOM agents"

WORKDIR /app

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package
RUN pip install --no-cache-dir .

# Create data directory
RUN mkdir -p /data/itom-orchestrator/state /data/itom-orchestrator/logs

# Environment defaults
ENV ORCH_DATA_DIR=/data/itom-orchestrator
ENV ORCH_LOG_LEVEL=INFO
ENV ORCH_HTTP_HOST=0.0.0.0
ENV ORCH_HTTP_PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

ENTRYPOINT ["itom-orchestrator-http"]
