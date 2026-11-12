# ============================================================================
# FIM One — Multi-stage Docker Build
# Produces a single image running API (uvicorn :8000) + Frontend (Next.js :3000)
# ============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS python-build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-dev --no-install-project

# Copy source and install the project itself
COPY src/ src/
COPY alembic.ini README.md ./
RUN uv sync --frozen --all-extras --no-dev

# ---------------------------------------------------------------------------
# Stage 2: Frontend build
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend-build

RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

WORKDIR /app/frontend

# Install dependencies first (layer cache)
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY frontend/ ./
ENV NEXT_OUTPUT=standalone
RUN pnpm build

# ---------------------------------------------------------------------------
# Stage 3: Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# No Docker CLI in this image. With CODE_EXEC_BACKEND=docker the application
# talks to the sandbox proxy itself (DOCKER_HOST=tcp://docker-socket-proxy:2375,
# see core/tool/sandbox/docker_api.py); it is never given the Docker daemon's
# socket, and nothing it starts has a `docker` to run.

# Unprivileged users. The API (and anything it starts: stdio MCP servers,
# the local code-execution backend) runs as `fim`; the Next.js frontend as
# `fimweb`. Fixed ids so that the ownership of ./data and ./uploads on the
# host is predictable.
RUN groupadd --gid 10001 fim \
    && useradd --uid 10001 --gid fim --home-dir /home/fim --create-home --shell /usr/sbin/nologin fim \
    && groupadd --gid 10002 fimweb \
    && useradd --uid 10002 --gid fimweb --home-dir /home/fimweb --create-home --shell /usr/sbin/nologin fimweb

# Copy Node.js binary from build stage (avoids apt-get network issues)
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# Copy Python virtual environment + source
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/src /app/src
COPY --from=python-build /app/alembic.ini /app/
COPY --from=python-build /app/pyproject.toml /app/

# Copy Alembic migrations (needed for `alembic upgrade head`)
COPY src/fim_one/migrations/ /app/src/fim_one/migrations/

# Copy Next.js standalone output
# standalone/ contains server.js + minimal node_modules + .next-build/server/
COPY --from=frontend-build /app/frontend/.next-build/standalone/ /app/frontend/
# Static assets (JS/CSS bundles) — not included in standalone by default
COPY --from=frontend-build /app/frontend/.next-build/static /app/frontend/.next-build/static
# Public assets (favicon, fonts, images)
COPY --from=frontend-build /app/frontend/public /app/frontend/public
# i18n message files — runtime-read by next-intl via fs.readdirSync(),
# not statically traced by Next.js standalone bundler
COPY --from=frontend-build /app/frontend/messages /app/frontend/messages

# CJK font embedded into PDF exports. The slim base ships no fonts at all, and
# Debian's Noto CJK packages are OpenType/CFF, which ReportLab cannot embed.
# A failed download only degrades export typography, so it never fails the build.
COPY scripts/fetch_export_fonts.py /tmp/fetch_export_fonts.py
RUN python /tmp/fetch_export_fonts.py /usr/share/fonts/truetype/fim-one \
    && rm /tmp/fetch_export_fonts.py

# The sandbox proxy: one standard-library file, run by the
# `docker-socket-proxy` service from this same image (no application import).
COPY src/fim_one/core/tool/sandbox/docker_proxy.py /app/docker_proxy.py

# Copy entrypoint
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# The key store logic the entrypoint runs as root (standard library only).
COPY src/fim_one/core/security/container_keys.py /app/container_keys.py

# What the unprivileged users may write besides the mounted volumes:
#   /app/tmp              scratch space of tools used outside a conversation
#   frontend build cache  Next.js image and fetch cache
# The key store (/var/lib/fim-one/secrets) is root's and private: keys that
# are not set in the environment are kept there, outside data/ (which the
# API's user owns), so that user can neither read nor replace them. Mount a
# volume on it (the bundled compose file does) or the keys go with the
# container.
RUN mkdir -p /app/tmp /app/data /app/uploads /app/frontend/.next-build/cache \
        /var/lib/fim-one/secrets \
    && chown fim:fim /app/tmp /app/data /app/uploads \
    && chown -R fimweb:fimweb /app/frontend/.next-build/cache \
    && chmod 700 /var/lib/fim-one/secrets

# Tells the application it runs in a container (core/security/host_access.py).
ENV FIM_IN_CONTAINER=1

# Put the venv on PATH so `uvicorn`, `alembic` etc. are available
ENV PATH="/app/.venv/bin:$PATH"
# Prevent Python from buffering stdout/stderr (important for Docker logs)
ENV PYTHONUNBUFFERED=1

# Compile .py → .pyc (in-place with -b) and remove source files for protection.
# Migrations are kept as-is because Alembic reads them at runtime.
RUN python -m compileall -b /app/src/fim_one/ \
    && find /app/src/fim_one/ -name "*.py" ! -path "*/migrations/*" -delete

EXPOSE 3000

# The entrypoint starts as root only to fix the ownership of the mounted
# volumes, then drops to `fim` / `fimweb` for everything it runs.
CMD ["/app/docker-entrypoint.sh"]
