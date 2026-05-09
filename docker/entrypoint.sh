#!/bin/sh
set -e

# Ensure config volumes are writable by vmail (uid 1000)
chown -R 1000:1000 /confs 2>/dev/null || true

echo "Running database migrations..."
gosu 1000:1000 uv run alembic upgrade head

echo "Starting mailfallback..."
exec gosu 1000:1000 uv run uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
