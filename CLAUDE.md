# FIM One — Claude Code Instructions

## Project Overview

AI-powered Connector Hub. Python async, provider-agnostic, protocol-first.

- **Package manager**: `uv` (not pip) · **Frontend**: Next.js + pnpm (`frontend/`)
- **Tests**: `uv run pytest` · **Launcher**: `./start.sh [portal|api]`

```
src/fim_one/
├── core/{agent,model,planner,memory,tools}/
├── web/             # FastAPI backend (agents, connectors, KB, chat)
frontend/            # Next.js portal (shadcn/ui)
```

## Git Rules (MANDATORY)

- **Commit scope**: (1) session has context → commit session's files; (2) fresh session no context → `git add -A`; (3) user specifies files inline → follow exactly. Always exclude sensitive files (.env, credentials).
- **Atomic commits**: split unrelated changes, even if user says "commit all".
- **NEVER `git stash --include-untracked`** with important untracked files — `git add` them first. Use `git stash pop` not `apply` + `drop`.
- **Worktrees**: clean tree before starting; agents commit on their branch; merge via `git merge`/`cherry-pick`, not file copying.
- **Worktree + migration**: worktree agents MUST NOT run `alembic upgrade head` (shared SQLite dev DB desyncs). Write migration files + ORM + code only; apply after merge-back.
- **Worktree merge-back**: orchestrator (main conversation) MUST `git merge <branch>` when agent finishes — no orphan branches.

## Frontend Build Safety

- **NEVER `rm -rf frontend/.next`** while dev server is running (kills HMR).
- Production builds → `.next-build` via `cd frontend && pnpm build`.

## Frontend UI Conventions

- **No native `confirm`/`alert`/`prompt`** — use `AlertDialog` / `Dialog` / Toast (sonner).
- **Navigation → `<Link>`**, not `<button onClick={router.push()}>`. For shadcn `<Button>`, `asChild` + `<Link>` inside.
- **Focus rings**: `focus-visible:outline-*`, **never** `focus-visible:ring-*` (ring = box-shadow, clipped by `overflow:hidden`). Ref: `ui/input.tsx`.
- **No `pl-*`/`pr-*` on form wrappers** with `<Input>`/`<Textarea>` (clips `shadow-xs`). Use `px-*` or `gap-*`.
- **No native `<select>`** — use shadcn `<Select>` with `<SelectTrigger className="w-full">`. Empty default → `__default__` sentinel (Radix treats `""` as unset).
- **Tab/filter state → URL query**: sync to `?tab=xxx` via `useSearchParams` + `router.replace`. Default tab = no param. Wrap in `<Suspense>`. Ref: `admin/page.tsx`.

## Admin List Page Actions (MANDATORY)

All admin data-table rows MUST follow `admin-users.tsx`:

- Row actions live in a **"..." `DropdownMenu`** — `MoreHorizontal` ghost button (`h-7 w-7 p-0`), last column, `text-right`.
- **NEVER** inline icon buttons (trash/edit/eye/power) in rows. **NEVER** clickable rows (`cursor-pointer` + row `onClick`) — use a "View Details" `DropdownMenuItem`.
- Last `<th>` = `{tc("actions")}` with `text-right font-medium text-muted-foreground`.
- Every `DropdownMenuItem` MUST have a lucide icon (`<Icon className="mr-2 h-4 w-4" />`).
- Order: View/Edit → Enable/Disable → **Delete last** with `variant="destructive"` + `DropdownMenuSeparator` before it.
- Dialogs/Sheets stay as table siblings; only the trigger moves into the dropdown.

## Error Feedback (MANDATORY)

Two-tier: inline for field errors, toast for system errors.

- **Field errors** (empty/format/conflict like "username taken") → inline `<p className="text-sm text-destructive">` below field. Use `fieldErrors` + `clearFieldError` on change. Add `aria-invalid`.
- **System/API errors** (5xx, network, auth, external down) → `toast.error(errMsg(err))`.
- **Success** → `toast.success()`.
- **Hybrid 400**: maps to a field → inline; else toast.
- Never silently close dialogs. Never only `console.error()`. Every user action gets visible feedback.

## Dirty State Protection (MANDATORY)

Create/edit forms MUST guard against accidental close (backdrop, X, navigation). AlertDialog is **sibling** of Dialog, never nested. Refs: `connector-settings-form.tsx` (modal), `agents/[id]/page.tsx` (full-page).

## i18n (MANDATORY)

All UI text via `next-intl` — **never hardcode English**. `useTranslations("{ns}")`. Shared strings → `useTranslations("common")`. New namespace = drop JSON in `messages/en/`, auto-discovered.

**English only, auto-sync the rest:**
- **NEVER manually edit** any non-English file: `messages/{zh,ja,ko,de,fr}/`, `docs/{zh,ja,ko,de,fr}/`, `README.{zh,ja,ko,de,fr}.md`. All auto-generated.
- **Only edit**: `messages/en/{ns}.json`, `docs/*.mdx` (root), `README.md`.
- Pre-commit hook runs `scripts/translate.py` (incremental). Full retrans: `uv run scripts/translate.py --all`. Setup: `bash scripts/setup-hooks.sh`.

**Adding a new locale:**
- Frontend: `SUPPORTED_LOCALES` in `frontend/src/i18n/request.ts`.
- Backend: all locale regex in `src/fim_one/web/schemas/auth.py` — `preferred_language` (`UpdateProfileRequest`) + `locale` (`Send{Verification,Login,Reset,Forgot}CodeRequest`). Miss this = silent 400 + locale won't persist.
- Docs nav: add to `LOCALES` in `scripts/build-docs-nav.py`, fill glossary, create empty `docs/nav-overrides/{locale}.json`.

**`docs/docs.json` is generated** — edit `docs/nav.template.json` / `scripts/docs-nav-glossary.json` / `docs/nav-overrides/*.json`. Pre-commit + `i18n-sync.yml` regenerate it.

## Alembic Migrations (MANDATORY — SQLite/PG dual-track)

Dev = SQLite, prod = PG. One migration set for both. `start.sh` runs `alembic upgrade head` on startup.

- **Every new ORM model/column MUST have a migration** — never `metadata.create_all()`, never ad-hoc `ALTER TABLE` in `engine.py`.
- **Idempotent**: use `table_exists()` / `table_has_column()` / `index_exists()` from `fim_one.migrations.helpers`.
- **Boolean defaults**: `server_default=sa.text("FALSE")`/`"TRUE"` — never `"0"`/`"1"` (PG rejects). Same for ORM model `server_default`.
- **Integer default**: `server_default="0"` OK. **Timestamp**: `sa.text('(CURRENT_TIMESTAMP)')` OK.
- **Timestamp columns MUST be `sa.DateTime(timezone=True)`** in BOTH the ORM model and the migration. The ORM writes tz-aware `datetime.now(UTC)`, which asyncpg rejects against a naive PG column — SQLite hides this, so it's a latent PG-only 500. A one-time scan (`l2n4p6r8t901`) already converted all columns that existed then; any new naive timestamp column re-introduces the bug (ref: `m5o7q9s1u234`).
- **JSON**: SQLite `json_extract(col, '$.key')` vs PG `col::json->>'key'`. Check `bind.dialect.name` in data migrations (ref: `b2d4e6f8a901`).
- **SQLite ALTER COLUMN**: use `op.batch_alter_table()` (SQLite can't ALTER).
- **Auto-apply**: after writing migration in main worktree (NOT agent worktree), immediately `uv run alembic upgrade head`.

## User Deletion File Cleanup (MANDATORY)

New user-owned module writing to disk → update `delete_user()` in `src/fim_one/web/api/admin.py`. ORM cascade only drops rows; files get orphaned.

Current registry (search `Clean up file-system resources`):

| Module | Path | Method |
|---|---|---|
| conversations | `data/sandbox/{conv_id}/`, `uploads/conversations/{conv_id}/` | `shutil.rmtree` |
| knowledge_bases | `uploads/kb/{kb_id}/`, `data/vector_store/user_{user_id}/` | `shutil.rmtree` |
| user uploads | `uploads/user_{user_id}/` | `shutil.rmtree` |
| avatar | `uploads/avatars/{user_id}_*` | `glob` + `unlink` |

If your module writes under `uploads/` or `data/`, add cleanup.

## Code Conventions

- Type hints on all public functions. Async-first for I/O. `__init__.py` imports minimal (public API only).

## Test Rules (MANDATORY)

- Every new module → `tests/test_{module}.py`. Every feat commit includes tests.
- All tests pass before commit: `uv run pytest tests/ -x -q`.
- Type checks pass: `uv run mypy <changed_files>` (strict=true; fix types, never `type: ignore`).
- No external services — mock DB/MCP/HTTP/LLM via `unittest.mock` / `AsyncMock`.
- Naming: `tests/test_{module}.py` · `Test{Feature}` · `test_{behavior}`.

## Task Completion Report (MANDATORY)

After any code change, report:
1. **What changed** — files + brief summary.
2. **How to test** — concrete steps (page + click, `uv run pytest tests/test_foo.py`, restart + check Z).

Communication only — doesn't replace automated tests.

## Post-Commit Documentation Sync (MANDATORY — DO NOT SKIP)

> After EVERY `git commit`, run the checklist below BEFORE responding or moving on. Non-negotiable.

**English first, translate on commit.** Edit EN; pre-commit hook translates to ZH/JA/KO/DE/FR.

- [ ] **`docs/changelog.mdx`** — append under `[Unreleased]` (`### Added/Changed/Fixed/Removed`). **User-facing only**. Skip: internal refactor, style, test count, doc typos, CI tweaks, dep bumps with no behavior change. One concise line — no file/class/test-count details.
- [ ] *(feat OR behavior-changing fix)* **`docs/roadmap.mdx`**. Treat `fix:` as `feat` when user-observable behavior changes (API errors disappearing, protocol compat, new observability, correctness guarantees). Pure internal fixes skip. Rules:
  1. **Check off** `- [ ]` → `- [x]` if commit satisfies it.
  2. **Insert new** significant items under fitting planned version; `- [x]` if just shipped, `- [ ]` if spawns follow-up.
  3. **Never touch** shipped (date-stamped) versions.
  4. **Deferred items** → `- [ ]` under right version. Internal planning docs are scratchpads, not a substitute.
  5. **Roadmap is an INDEX, not a blueprint.** Each entry is **one line, ≤150 chars** — names what ships and the user benefit. **No multi-sentence implementation prose, no nested sub-bullets describing schema/tradeoffs/rationale.** If you need to write more, you need a `dev/` file.
  6. **Detail goes to `dev/<topic>.md`** (single-topic) or `dev/<topic>/` (multi-file design). `dev/` is **not** shipped to Mintlify, so roadmap pointers MUST use a JSX comment: append ` {/* dev: dev/<topic>.md */}` to the section heading (e.g. `#### Hook System {/* dev: dev/hook-system.md */}`). Mintlify strips JSX comments at compile time → public docs stay clean; Claude reading the file source sees the pointer and can pick up the companion design doc on the next pass. **Never** use a visible bracketed link like `[详见 dev/...]` — that leaks internal structure to public readers. Existing dev/ companions: `dev/hook-system.md`, `dev/im-channels.md`, `dev/connector-rbac/`, `dev/agent-workspace.md`, `dev/public-api-phase2.md`, `dev/prompt-cache-followups.md`, `dev/channel-integration-sso.md`, `dev/agent-trace-layer.md`. **Rule applies forward** — don't audit/rewrite already-terse entries; existing verbose entries stay until they ship and get archived.
  7. **`dev/<topic>.md` is a planning-time artifact, not a per-commit artifact.** Create or extend it when **adding** a complex roadmap item (incremental planning — "let's do X in v0.9, here's the design"). When **checking off** an existing item at commit time (`[ ]` → `[x]`), do NOT touch the dev/ file. Implementation may have diverged from the design doc and that's fine — the source of truth is the code. After the version ships and the item is archived, the matching dev/ file may be moved to `dev/archive/` in a follow-up cleanup, but that's optional and never blocks a ship commit.
- [ ] *(feat only)* **`example.env`** — new env keys with placeholder + comment; sync `docs/configuration/environment-variables.mdx` table.
- [ ] *(feat only)* **`README.md`** — update Key Features / Project Structure if needed.
- [ ] **Chinese sync** — pre-commit hook handles `docs/zh/` + `README.zh.md`. Commit EN + ZH together.

## Cut Release (triggered by "what's next" / "接下来做什么")

**Version source chain**: About dialog (frontend) → `GET /api/version` → `fim_one.__version__` (`src/fim_one/__init__.py`) → must equal `pyproject.toml::version` → must equal highest ROADMAP **Shipped** version → must equal latest archived CHANGELOG version. **All five must agree at all times.** When they drift, fix in the same commit that surfaced the drift.

BEFORE answering:

1. **Archive** `CHANGELOG [Unreleased]` → `[vX.Y] - YYYY-MM-DD`. Add a fresh empty `[Unreleased]` above it.
2. **Mark shipped**: ROADMAP version heading gets date, moved under **Shipped Versions**.
3. **Bump version**: `pyproject.toml::version` AND `src/fim_one/__init__.py::__version__` match new shipped version.
4. Then answer with next priorities from first unfinished version's `- [ ]`.

**Roadmap vs Changelog (don't conflate them)**: Roadmap = capability index (per-version one-liners; trajectory + headline of what shipped). Changelog = release notes (per-version Added/Changed/Fixed prose; what a developer auditing the diff needs to know). Both are mandatory; they target different readers and different organizing dimensions. Cut-release writes both.
