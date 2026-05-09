FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel

# Layer 1: install runtime deps without project for cache friendliness
RUN pip install \
      "fastapi>=0.115.0" \
      "uvicorn[standard]>=0.30.0" \
      "pydantic>=2.7" \
      "pydantic-settings>=2.4" \
      "sqlalchemy[asyncio]>=2.0.30" \
      "asyncpg>=0.29" \
      "qdrant-client>=1.10" \
      "neo4j>=5.22" \
      "redis>=5.0" \
      "pathspec>=0.12" \
      "structlog>=24.1" \
      "opentelemetry-api>=1.27" \
      "opentelemetry-sdk>=1.27" \
      "opentelemetry-instrumentation-fastapi>=0.48b0" \
      "opentelemetry-exporter-otlp>=1.27"

COPY apps ./apps
COPY core ./core
COPY storage ./storage
COPY schemas ./schemas

RUN pip install -e .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
