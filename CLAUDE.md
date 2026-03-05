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
- **Focus rings**: use `focus-visible:outline-offset-[-2px]`, **never** `outline-offset-0` — `0` gets clipped by container overflow. Already fixed in `ui/input.tsx` + `ui/textarea.tsx`; do not revert.
- **No `pl-*`/`pr-*` on form wrappers** containing `<Input>`/`<Textarea>` — asymmetric padding clips `shadow-xs`. Use parent `px-*` or `gap-*`/`space-y-*` instead.

## Toast Feedback Convention (MANDATORY)

Every user-triggered API call (create, update, delete, upload, toggle, etc.) MUST show toast:
- Success → `toast.success("...")` (sonner)
- Failure → `toast.error(errMsg(err))`

**NEVER** silently close a dialog. **NEVER** use only `console.error()`. **NEVER** use flash-and-disappear inline state (`setSaved(true); setTimeout(...)`) — always use `toast.success()`.

## Dirty State Protection Convention (MANDATORY)

Forms with meaningful user input MUST guard against accidental close.

**Applies to**: Modal/Drawer/Sheet create/edit forms, full-page editor forms.
**Does NOT apply to**: Read-only drawers, search dialogs, inline panels.

### Modal/Drawer pattern — key rules
- X button + Cancel both call the same `handleClose`
- `onInteractOutside` (not `onPointerDownOutside`): if dirty → `e.preventDefault()` + show confirm
- AlertDialog is a **sibling** of Dialog, never nested inside `DialogContent`
- Empty form → backdrop closes directly (no confirm)

Reference: `frontend/src/components/connectors/connector-settings-form.tsx`

### Full-page form pattern — key rules
- Child form exposes `onDirtyChange` callback → page tracks `formDirty`
- When dirty: swap `<Link>` back button → `<button>` that opens leave-confirm dialog
- Add `beforeunload` handler while dirty (browser refresh warning)

Reference: `frontend/src/app/agents/[id]/page.tsx`

## Code Conventions

- Type hints on all public functions
- Async-first: `async def` for I/O-bound operations
- Tests alongside features: every new module → `tests/test_*.py`
- Keep `__init__.py` imports minimal — only re-export public API

## Post-Commit Documentation Sync (MANDATORY)

After every commit, update docs silently (do NOT ask the user):

1. **`wiki/CHANGELOG.md`** — append under `[Unreleased]` (`### Added/Changed/Fixed/Removed`)
2. *(feat only)* **`wiki/Roadmap.md`** — check off completed items; add new user-facing items under current version (never retroactively add to already-shipped versions)
3. *(feat only)* **`example.env`** — add any new env keys with placeholder + comment
4. *(feat only)* **`README.md`** — update Key Features and Project Structure if needed
5. If any `wiki/*.md` changed → run `./scripts/sync-wiki.sh`

**Version alignment**: CHANGELOG `[Unreleased]` and Roadmap must use the same version numbers.
