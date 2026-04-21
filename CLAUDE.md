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
- Docs: new `navigation.languages[]` entry in `docs/docs.json` — copy existing block, update `"language"`, translate tab/group names, prefix paths with `{locale}/`.

**Adding doc page/group — MANDATORY nav sync across 5 locales:**
`docs/docs.json` is NOT auto-translated. When EN changes pages/groups, mirror the change in all 5 locale sections (`zh`, `ja`, `ko`, `de`, `fr`) in the same commit. Translate group names using canonical terms from `frontend/messages/{locale}/<ns>.json`. Verify: `grep -c "<new-path>" docs/docs.json` = 6.

## Alembic Migrations (MANDATORY — SQLite/PG dual-track)

Dev = SQLite, prod = PG. One migration set for both. `start.sh` runs `alembic upgrade head` on startup.

- **Every new ORM model/column MUST have a migration** — never `metadata.create_all()`, never ad-hoc `ALTER TABLE` in `engine.py`.
- **Idempotent**: use `table_exists()` / `table_has_column()` / `index_exists()` from `fim_one.migrations.helpers`.
- **Boolean defaults**: `server_default=sa.text("FALSE")`/`"TRUE"` — never `"0"`/`"1"` (PG rejects). Same for ORM model `server_default`.
- **Integer default**: `server_default="0"` OK. **Timestamp**: `sa.text('(CURRENT_TIMESTAMP)')` OK.
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
- [ ] *(feat only)* **`example.env`** — new env keys with placeholder + comment; sync `docs/configuration/environment-variables.mdx` table.
- [ ] *(feat only)* **`README.md`** — update Key Features / Project Structure if needed.
- [ ] **Chinese sync** — pre-commit hook handles `docs/zh/` + `README.zh.md`. Commit EN + ZH together.

## Cut Release (triggered by "what's next" / "接下来做什么")

BEFORE answering:

1. **Archive** `CHANGELOG [Unreleased]` → `[vX.Y] (YYYY-MM-DD)`.
2. **Mark shipped**: ROADMAP version heading gets date, moved under **Shipped Versions**.
3. **Bump version**: `pyproject.toml::version` AND `src/fim_one/__init__.py::__version__` match new shipped version. Both feed `/api/version` (About dialog). Never drift.
4. **Fresh** empty `[Unreleased]` in CHANGELOG.
5. Then answer with next priorities from first unfinished version's `- [ ]`.

**Version Sync Invariant (always, not just at cut-release)**: `pyproject.toml::version` = `__version__` = highest ROADMAP **Shipped** version. Fix drift in the same commit as the work that surfaced it.
