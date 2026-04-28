#!/bin/sh
# docker/entrypoint.sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting mailfallback..."
exec uvicorn mailfallback.app:app --host 0.0.0.0 --port 8000
