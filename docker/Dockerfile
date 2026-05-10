FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       isync ca-certificates curl restic libsasl2-modules-kdexoauth2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 vmail && useradd -u 1000 -g vmail -m -s /usr/sbin/nologin vmail \
    && mkdir -p /data /confs/dovecot /confs/webmail \
    && chown -R 1000:1000 /data /confs

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN uv sync --frozen --no-dev \
    && chown -R 1000:1000 /app

USER 1000

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
