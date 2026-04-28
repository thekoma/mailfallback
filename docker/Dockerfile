# docker/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[postgres]"

COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN pip install --no-cache-dir -e .

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
