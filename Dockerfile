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

# Docker CLI — enables CODE_EXEC_BACKEND=docker (runs sandboxed code via host Docker)
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

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

# Copy entrypoint
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Put the venv on PATH so `uvicorn`, `alembic` etc. are available
ENV PATH="/app/.venv/bin:$PATH"
# Prevent Python from buffering stdout/stderr (important for Docker logs)
ENV PYTHONUNBUFFERED=1

# Compile .py → .pyc (in-place with -b) and remove source files for protection.
# Migrations are kept as-is because Alembic reads them at runtime.
RUN python -m compileall -b /app/src/fim_one/ \
    && find /app/src/fim_one/ -name "*.py" ! -path "*/migrations/*" -delete

EXPOSE 3000

CMD ["/app/docker-entrypoint.sh"]
