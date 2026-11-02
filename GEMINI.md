# GEMINI.md — FIM One Instructional Context

## Project Overview

FIM One is an AI agent platform for enterprise systems: agents plan and execute tasks across existing systems (ERP, CRM, OA, databases) through APIs, database connections, and MCP servers.

### Core Architecture
- **Backend**: FastAPI (Python 3.11+) asynchronous framework.
- **Agent Core**: ReAct reasoning loops and dynamic DAG planning with concurrent execution.
- **Provider Agnostic**: Protocol-first architecture using LiteLLM to support OpenAI, Anthropic, DeepSeek, Ollama, etc.
- **RAG Pipeline**: LanceDB vector store with hybrid retrieval (FTS + RRF) and reranking.
- **Frontend**: Next.js 15+ (App Router) with shadcn/ui and Tailwind CSS 4.
- **Database**: Dual-track support for SQLite (development) and PostgreSQL (production) via SQLAlchemy and Alembic.

## Building and Running

### Prerequisites
- **Python 3.11+** with `uv` package manager.
- **Node.js 18+** with `pnpm`.
- **Docker** (optional, for sandboxed code execution).

### Key Commands
- **Full Stack**: `./start.sh portal` (Production) or `./start.sh dev` (Development with hot-reload).
- **Backend Only**: `./start.sh api` or `uv run uvicorn fim_one.web:create_app --factory --host 0.0.0.0 --port 8000`.
- **Frontend Only**: `cd frontend && pnpm dev`.
- **Database Migrations**: `uv run alembic upgrade head`.
- **Testing**: `uv run pytest`.
- **Linting/Formatting**: `ruff check src/ tests/` and `mypy src/`.

## Development Conventions

### Git & Workflow
- **Atomic Commits**: Unrelated changes must be split into separate commits.
- **i18n Automation**: Only edit English source files (`messages/en/*.json`, `README.md`, `docs/*.mdx`). Other languages are auto-translated on commit via `scripts/translate.py`.
- **Worktrees**: Agents must commit on their branch; merge via `git merge`, never file copying.
- **Migrations**: Worktree agents must NOT run `alembic upgrade head`; only write migration files. Migrations are applied after merging to main.

### Backend (Python)
- **Async First**: Use `async def` for all I/O-bound operations.
- **Type Safety**: Mandatory type hints on all public functions.
- **Migrations**: All migrations must be idempotent and compatible with both SQLite and PostgreSQL (use helpers in `fim_one.migrations.helpers`).
- **Boolean Defaults**: Use `server_default=sa.text("FALSE")` or `sa.text("TRUE")` for PG compatibility.

### Frontend (Next.js)
- **i18n**: Never hardcode English strings; use `next-intl` (`useTranslations`).
- **UI Safety**: Never `rm -rf frontend/.next` while dev server is running.
- **Admin Tables**: Follow the `admin-users.tsx` pattern: actions in a "..." DropdownMenu, Lucide icons for all menu items, destructive actions last.
- **Dialogs**: Use `AlertDialog` siblings for dirty state protection; avoid native `confirm/alert`.
- **Styling**: Prefer `focus-visible:outline-*` over `ring-*` to avoid clipping.

## Project Structure

```
src/fim_one/
├── core/            # Agent logic (ReAct, Planner, Tools, RAG)
│   ├── agent/       # ReActAgent & Hook System
│   ├── workflow/    # DAG Engine & Node types
│   ├── model/       # LLM Providers & LiteLLM integration
│   └── tools/       # Base tools & Connector adapters
├── web/             # FastAPI App, API routes, and SSE endpoints
└── db/              # SQLAlchemy models & session management
frontend/            # Next.js Application
├── src/components/  # UI components (shadcn)
├── messages/        # i18n JSON files (en/ is source)
└── public/          # Static assets
docs/                # Documentation (Mintlify format)
tests/               # Comprehensive pytest suite
```
