# FIM Agent — Claude Code Instructions

## Project Overview

FIM Agent is an AI-powered Connector Hub. Python async framework, provider-agnostic, protocol-first.

- **Package manager**: `uv` (not pip)
- **Frontend**: Next.js + pnpm (in `frontend/`)
- **Tests**: `uv run pytest`
- **Launcher**: `./start.sh [portal|api]`

## Architecture

```
src/fim_agent/
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

- **Atomic commits**: always split unrelated changes into separate commits, even if user says "commit all"
- **NEVER `git stash --include-untracked`** with important untracked files — `git add` them first; use `git stash pop` not `apply` + `drop`
- **Worktrees**: clean working tree before starting; agents MUST commit on their branch; merge via `git merge`/`git cherry-pick`, not file copying

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

All UI text must use `next-intl` — **never hardcode English strings**. Add keys to both `messages/en/{ns}.json` and `messages/zh/{ns}.json`, use `useTranslations("{ns}")` in components. Shared strings (Save/Cancel/Delete) → `useTranslations("common")`. New namespace = just drop JSON files, auto-discovered via `fs.readdirSync`.

## Code Conventions

- Type hints on all public functions
- Async-first: `async def` for I/O-bound operations
- Tests alongside features: every new module → `tests/test_*.py`
- Keep `__init__.py` imports minimal — only re-export public API

## Post-Commit Documentation Sync (MANDATORY)

After every commit, update docs silently (do NOT ask the user):

1. **`docs/changelog.mdx`** — append under `[Unreleased]` (`### Added/Changed/Fixed/Removed`)
2. *(feat only)* **`docs/roadmap.mdx`** — check off completed items; add new user-facing items under current version (never retroactively add to already-shipped versions)
3. *(feat only)* **`example.env`** — add any new env keys with placeholder + comment; then **sync `docs/configuration/environment-variables.mdx`** — add the new variable(s) to the correct section table
4. *(feat only)* **`README.md`** — update Key Features and Project Structure if needed

**Version alignment**: CHANGELOG `[Unreleased]` and Roadmap must use the same version numbers.

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
