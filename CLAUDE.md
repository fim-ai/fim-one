# FIM One — Claude Code Instructions

## Project Overview

FIM One is an AI-powered Connector Hub. Python async framework, provider-agnostic, protocol-first.

- **Package manager**: `uv` (not pip)
- **Frontend**: Next.js + pnpm (in `frontend/`)
- **Tests**: `uv run pytest`
- **Launcher**: `./start.sh [portal|api]`

## Architecture

```
src/fim_one/
├── core/
│   ├── agent/       # ReActAgent (JSON mode + native function calling)
│   ├── model/       # BaseLLM, OpenAICompatibleLLM, ModelRegistry, retry, rate limiting, usage tracking
│   ├── planner/     # DAGPlanner → DAGExecutor → PlanAnalyzer
│   ├── memory/      # WindowMemory, SummaryMemory
│   └── tools/       # Tool base classes, ConnectorToolAdapter
├── web/             # FastAPI backend API (agents, connectors, KB, chat)
frontend/            # Next.js portal (shadcn/ui)
```

## Git Rules (MANDATORY)

- **Commit scope**: (1) if the current session has context, commit all files involved in the session's tasks; (2) if this is a fresh session with no prior discussion, commit everything (`git add -A`); (3) if the user provides an inline instruction specifying which files to commit, follow that scope exactly. All three cases must exclude sensitive files (.env, credentials, etc.)
- **Atomic commits**: always split unrelated changes into separate commits, even if user says "commit all"
- **NEVER `git stash --include-untracked`** with important untracked files — `git add` them first; use `git stash pop` not `apply` + `drop`
- **Worktrees**: clean working tree before starting; agents MUST commit on their branch; merge via `git merge`/`git cherry-pick`, not file copying
- **Worktree + DB migration**: worktree agents MUST NOT run `alembic upgrade head` or any migration command — only write migration files + ORM model changes + business code. Migrations are applied after merging back to main (via `start.sh` auto-upgrade or manual `alembic upgrade head`). Reason: worktree shares the same SQLite dev DB; running migrations there will break the running main process and desync `alembic_version`.
- **Worktree merge-back**: after a worktree agent completes, the orchestrator (main conversation) MUST merge the feature branch back to the current branch via `git merge <branch>` — do NOT leave orphan branches or ask the user to merge manually

## Frontend Build Safety

- **NEVER `rm -rf frontend/.next`** while dev server is running — kills Turbopack HMR
- Production builds → `.next-build` (separate dir). To build: `cd frontend && pnpm build`

## Frontend UI Conventions

- **No native dialogs** (`confirm`/`alert`/`prompt`) — use `AlertDialog` / `Dialog` / Toast (sonner)
- **Navigation → `<Link>`**, not `<button onClick={router.push()}>`. For shadcn `<Button>`, use `asChild` + `<Link>` inside
- **Focus rings**: use `focus-visible:outline-*` pattern, **never** `focus-visible:ring-*` — `ring` is `box-shadow`, gets clipped by `overflow:hidden`. See `ui/input.tsx` for reference.
- **No `pl-*`/`pr-*` on form wrappers** containing `<Input>`/`<Textarea>` — clips `shadow-xs`. Use `px-*` or `gap-*` instead.
- **No native `<select>`** — use shadcn `<Select>` with `<SelectTrigger className="w-full">`. For empty defaults, use `__default__` sentinel (Radix treats `""` as unset).
- **Tab/filter state → URL query param**: any page with tabs or filter switching MUST sync to `?tab=xxx` via `useSearchParams` + `router.replace`. Default tab uses no param (clean URL). Wrap component in `<Suspense>`. See `admin/page.tsx`, `settings/page.tsx`, `artifacts/page.tsx` for reference.

## Admin List Page Actions Convention (MANDATORY)

All admin pages that display a data table **MUST** follow the `admin-users.tsx` pattern for row actions:

- **Actions MUST live in a "..." `DropdownMenu`** — one `MoreHorizontal` ghost button (`h-7 w-7 p-0`) in the last column, right-aligned (`text-right`)
- **NEVER expose inline icon buttons** (trash, edit, eye, power, toggle) directly in table rows
- **NEVER make rows clickable** (`cursor-pointer` + row `onClick`) — use a DropdownMenuItem instead (e.g. "View Details")
- **Column header**: last `<th>` must be `{tc("actions")}` with `text-right font-medium text-muted-foreground`
- **Every `DropdownMenuItem` MUST have a lucide icon** (`<Icon className="mr-2 h-4 w-4" />`) — no text-only menu items
- **Item order**: safe actions first (View, Edit), then state-toggle (Enable/Disable), then destructive last (Delete) with `variant="destructive"` and a `DropdownMenuSeparator` before it
- **Dialogs/Sheets stay as siblings** of the table — only the trigger moves into the dropdown, never the modal itself
- **Reference**: `admin-users.tsx` (full pattern), `admin-api-keys.tsx` (minimal pattern)

## Error Feedback Convention (MANDATORY)

**Two-tier strategy — inline for field errors, toast for system errors:**

- **Field-level validation errors** (empty required field, invalid format, value conflict like "username taken") → **inline `<p className="text-sm text-destructive">` below the field**. Use `fieldErrors` state + `clearFieldError` on input change. Add `aria-invalid` for accessibility.
- **System-level / API errors** (500, network timeout, auth failure, external service down) → **`toast.error(errMsg(err))`**
- **Success feedback** → **`toast.success()`**
- **Hybrid 400 responses**: parse the error — if it maps to a specific field (e.g., `email_already_registered`), show inline; otherwise toast.

**Still mandatory**: never silently close dialogs, never use only `console.error()`. Every user action MUST have visible feedback (inline or toast).

## Dirty State Protection (MANDATORY)

Create/edit forms MUST guard against accidental close (modal backdrop, X button, page navigation). AlertDialog must be **sibling** of Dialog, never nested. References: `connector-settings-form.tsx` (modal pattern), `agents/[id]/page.tsx` (full-page pattern).

## i18n (MANDATORY)

All UI text must use `next-intl` — **never hardcode English strings**. Use `useTranslations("{ns}")` in components. Shared strings (Save/Cancel/Delete) → `useTranslations("common")`. New namespace = just drop JSON files, auto-discovered via `fs.readdirSync`.

**Translation workflow — English only, auto-sync the rest:**
- **NEVER manually edit any non-English file** — this includes `messages/zh/`, `messages/ja/`, `messages/ko/`, `messages/de/`, `messages/fr/`, `docs/zh/`, `docs/ja/`, `docs/ko/`, `docs/de/`, `docs/fr/`, `README.zh.md`, `README.ja.md`, `README.ko.md`, `README.de.md`, `README.fr.md`. ALL of these are auto-generated.
- **Only edit English sources**: `messages/en/{ns}.json`, `docs/*.mdx` (root level), `README.md`
- Other locales are **auto-translated on commit** via the pre-commit hook (`scripts/hooks/pre-commit`)
- The hook calls `scripts/translate.py` which translates new/changed keys incrementally using the project's Fast LLM
- To add a new namespace: create `messages/en/{ns}.json` only; other locale files are generated automatically
- To force full retranslation: `uv run scripts/translate.py --all`
- Hook setup (run once after clone): `bash scripts/setup-hooks.sh`

**Adding a new locale — full-stack checklist:**
- Frontend: add to `SUPPORTED_LOCALES` in `frontend/src/i18n/request.ts`
- Backend: update ALL locale regex patterns in `src/fim_one/web/schemas/auth.py` — includes `preferred_language` (`UpdateProfileRequest`) and `locale` (`SendVerificationCodeRequest`, `SendLoginCodeRequest`, `SendResetCodeRequest`, `SendForgotCodeRequest`). Missing this causes silent 400 rejections and locale won't persist after refresh.
- Docs: add a full `navigation.languages[]` entry in `docs/docs.json` — copy an existing locale block, update `"language"`, translate tab/group names, and prefix all page paths with `{locale}/`. Missing this means the locale won't appear in Mintlify's language switcher even if translated files exist.

## Alembic Migration Rules (MANDATORY — SQLite/PG dual-track)

Dev uses SQLite, production uses PostgreSQL. One set of migration files must work on both. Alembic is the **single source of truth** — `start.sh` runs `alembic upgrade head` on every startup.

- **Every new ORM model MUST have a migration** — never rely on `metadata.create_all()`. If you add a `__tablename__`, write a corresponding `op.create_table()` migration.
- **Every new column MUST have a migration** — no ad-hoc `ALTER TABLE` in `engine.py`.
- **All migrations MUST be idempotent** — use `table_exists()`, `table_has_column()`, `index_exists()` from `fim_one.migrations.helpers`. Legacy DBs (created by `create_all()` with no `alembic_version`) run ALL migrations from scratch.
- **Boolean defaults**: always use `server_default=sa.text("FALSE")` / `sa.text("TRUE")`. Never `"0"` / `"1"` — PG rejects integer literals for Boolean columns.
- **Integer defaults**: `server_default="0"` is fine for both engines.
- **Timestamps**: `server_default=sa.text('(CURRENT_TIMESTAMP)')` works on both.
- **JSON operators**: SQLite uses `json_extract(col, '$.key')`, PG uses `col::json->>'key'`. When writing data migrations with JSON, check `bind.dialect.name` (see `b2d4e6f8a901` for reference).
- **ORM model `server_default`**: same rule — use `"FALSE"` / `"TRUE"` for Boolean columns in model definitions, so future auto-generated migrations inherit correct defaults.
- **SQLite ALTER COLUMN**: SQLite cannot `ALTER COLUMN` — use `op.batch_alter_table()` to modify column constraints (e.g., nullable). Always test both dialects.
- **Auto-apply after creation**: After writing a migration file (in the main worktree, NOT in agent worktrees), **immediately run `uv run alembic upgrade head`** to apply it. Never leave migrations unapplied — it causes runtime errors that look unrelated.

## User Deletion File Cleanup (MANDATORY)

When adding a new user-owned module that writes files to disk, you **MUST** also update `delete_user()` in `src/fim_one/web/api/admin.py` to clean up those files. ORM cascade only deletes DB rows — files on disk are orphaned unless explicitly removed.

Current cleanup registry (search `Clean up file-system resources` in `admin.py`):

| # | Module | Disk path | Cleanup method |
|---|--------|-----------|----------------|
| 1 | conversations | `data/sandbox/{conv_id}/`, `uploads/conversations/{conv_id}/` | `shutil.rmtree` per conv |
| 2 | knowledge_bases | `uploads/kb/{kb_id}/`, `data/vector_store/user_{user_id}/` | `shutil.rmtree` per KB + user vector dir |
| 3 | user uploads | `uploads/user_{user_id}/` | `shutil.rmtree` |
| 4 | avatar | `uploads/avatars/{user_id}_*` | `glob` + `unlink` |

**Checklist for new modules**: if your module calls `mkdir`, `write_bytes`, `write_text`, `shutil.copy`, or `open(..., "w")` with a path under `uploads/` or `data/`, add corresponding cleanup to `delete_user()`.

## Code Conventions

- Type hints on all public functions
- Async-first: `async def` for I/O-bound operations
- Keep `__init__.py` imports minimal — only re-export public API

## Test Rules (MANDATORY)

- **Every new module MUST have a corresponding test file** — `src/fim_one/core/foo/bar.py` → `tests/test_bar.py`. No exceptions.
- **Every feat commit MUST include tests** for the new functionality — features without tests will not be accepted.
- **All existing tests MUST pass before committing** — run `uv run pytest tests/ -x -q` and fix any failures before `git commit`.
- **Type checks MUST pass** — run `uv run mypy <changed_files>` on all new/modified `.py` files. The project uses `mypy strict=true`; do not add `type: ignore` comments to bypass errors — fix the actual types.
- **Tests MUST NOT depend on external services** — mock databases, MCP servers, HTTP endpoints, and LLM calls. Use `unittest.mock` / `AsyncMock`.
- **Test naming convention** — file: `tests/test_{module}.py`, class: `Test{Feature}`, method: `test_{behavior_under_test}`.

## Task Completion Report (MANDATORY)

After finishing any code change, ALWAYS report to the user:

1. **What changed** — list the files modified and a brief summary of each change
2. **How to test** — provide concrete steps or commands the user can run to verify the change works (e.g., "open page X and click Y", "run `uv run pytest tests/test_foo.py`", "restart the dev server and check Z")

This is a communication requirement only — it does NOT replace automated tests. Automated tests (CI, pre-commit hooks, `uv run pytest`) still run independently as usual.

## Post-Commit Documentation Sync (MANDATORY — DO NOT SKIP)

> **CRITICAL**: After EVERY `git commit`, you MUST immediately run the checklist below BEFORE responding to the user or moving to the next task. This is non-negotiable. Failure to do this is a bug in your behavior.

**English first, translate on commit.** During development only edit English versions. On commit, translate the staged diff into Chinese counterparts (`docs/*.mdx` → `docs/zh/*.mdx`, `README.md` → `README.zh.md`).

After every commit, update docs silently (do NOT ask the user). Run ALL applicable items as a checklist:

- [ ] **`docs/changelog.mdx`** — append under `[Unreleased]` (`### Added/Changed/Fixed/Removed`). **Only user-facing changes**: new features, behavior changes, bug fixes that affect users. **Skip**: pure internal refactoring, code style/formatting, test count updates, doc typo fixes, CI config tweaks, dependency bumps with no behavior change. One concise line per change — no implementation details (file names, class names, test counts). CHANGELOG is for users, not developers.
- [ ] *(feat only)* **`docs/roadmap.mdx`** — ROADMAP is the intent list (what's planned). Three rules:
  1. **Check off**: if this commit satisfies a `- [ ]` planned item → change it to `- [x]`
  2. **Insert new**: if this is a significant new feature NOT in the roadmap → add it under the most fitting planned version as `- [x]` (just shipped) or `- [ ]` if it spawns follow-up work. Use judgment — don't add every small thing; only user-facing features worth planning around
  3. **Never touch** shipped (date-stamped) versions retroactively
- [ ] *(feat only)* **`example.env`** — add any new env keys with placeholder + comment; then **sync `docs/configuration/environment-variables.mdx`** — add the new variable(s) to the correct section table
- [ ] *(feat only)* **`README.md`** — update Key Features and Project Structure if needed
- [ ] **Chinese sync** — translate the staged English doc diff into the corresponding `docs/zh/` and `README.zh.md` files. Commit EN + ZH together.

## Cut Release (triggered by user asking "what's next")

When the user asks "what's next on the roadmap?" or "接下来做什么", BEFORE answering:

1. **Archive**: move all content from `CHANGELOG [Unreleased]` → `CHANGELOG [vX.Y] (YYYY-MM-DD)`
2. **Mark shipped**: in ROADMAP, add the date to the version heading and move it under **Shipped Versions**
3. **Fresh start**: open a new empty `[Unreleased]` section in CHANGELOG
4. **Then answer**: suggest the next priorities from the first unfinished planned version's `- [ ]` items

<!-- GT I18N RULES START -->

- **gtx-cli**: v2.6.31

# General Translation (GT) Internationalization Rules

This project is using [General Translation](https://generaltranslation.com/docs/overview.md) for internationalization (i18n) and translations. General Translation is a developer-first localization stack, built for the world's best engineering teams to ship apps in every language with ease.

## Configuration

The General Translation configuration file is called `gt.config.json`. It is usually located in the root or src directory of a project.

```json
{
  "defaultLocale": "en",
  "locales": ["es", "fr", "de"],
  "files": {
    "json": {
      "include": ["./**/[locale]/*.json"]
    }
  }
}
```

The API reference for the config file can be found at <https://generaltranslation.com/docs/cli/reference/config.md>.

## Translation

Run `npx gtx-cli translate` to create translation files for your project. You must have an API key to do this.

## Documentation

<https://generaltranslation.com/llms.txt>

<!-- GT I18N RULES END -->
