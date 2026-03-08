# ============================================================================
# FIM Agent — Multi-stage Docker Build
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

# Copy Node.js binary from build stage (avoids apt-get network issues)
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# Copy Python virtual environment + source
COPY --from=python-build /app/.venv /app/.venv
COPY --from=python-build /app/src /app/src
COPY --from=python-build /app/alembic.ini /app/
COPY --from=python-build /app/pyproject.toml /app/

# Copy Alembic migrations (needed for `alembic upgrade head`)
COPY src/fim_agent/migrations/ /app/src/fim_agent/migrations/

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

# Copy entrypoint
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Put the venv on PATH so `uvicorn`, `alembic` etc. are available
ENV PATH="/app/.venv/bin:$PATH"
# Prevent Python from buffering stdout/stderr (important for Docker logs)
ENV PYTHONUNBUFFERED=1

# Compile .py → .pyc (in-place with -b) and remove source files for protection.
# Migrations are kept as-is because Alembic reads them at runtime.
RUN python -m compileall -b /app/src/fim_agent/ \
    && find /app/src/fim_agent/ -name "*.py" ! -path "*/migrations/*" -delete

EXPOSE 3000

CMD ["/app/docker-entrypoint.sh"]
