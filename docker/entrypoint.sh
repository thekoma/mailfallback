#!/bin/sh
set -e

echo "Running database migrations..."
uv run alembic upgrade head

echo "Starting mailfallback..."
exec uv run uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
