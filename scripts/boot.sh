#!/bin/sh
# Memory-CL boot orchestrator (Phase 9).
# Runs minimal pre-flight checks, then exec()s into the supplied CMD.
# Inside the running app, BootSequence (apps/api/bootstrap.py) executes
# the deterministic 8-stage health gate at lifespan startup — this
# script just verifies process-level prerequisites.

set -eu

ENV_NAME="${ENVIRONMENT:-development}"
log() { printf '[boot] %s\n' "$*" >&2; }

log "environment=${ENV_NAME}"
log "python=$(python --version 2>&1)"

# Required env vars vary by environment.
case "$ENV_NAME" in
  production|staging)
    : "${POSTGRES_URL:?POSTGRES_URL is required in ${ENV_NAME}}"
    : "${QDRANT_URL:?QDRANT_URL is required in ${ENV_NAME}}"
    : "${NEO4J_URI:?NEO4J_URI is required in ${ENV_NAME}}"
    : "${REDIS_URL:?REDIS_URL is required in ${ENV_NAME}}"
    if [ "$ENV_NAME" = "production" ]; then
      : "${MCP_API_KEY:?MCP_API_KEY MUST be set in production}"
    fi
    ;;
esac

# Verify the package is importable before exec — surfaces a clean
# error early instead of a uvicorn traceback. We deliberately do NOT
# redirect stderr to /dev/null here: when this check fails, the
# Python traceback IS the diagnostic. Swallowing it (the previous
# behavior) made every boot crash look identical and forced operators
# to bypass boot.sh with `docker run --entrypoint python …` to find
# the real error. Trade a few noisy warning lines for actionable
# failure logs.
python -c "import apps.api.main" || {
  log "FATAL: apps.api.main failed to import (traceback above)"
  exit 70
}

log "preflight ok — exec: $*"
exec "$@"
