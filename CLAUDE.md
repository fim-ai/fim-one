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

## Git Commit Rules (MANDATORY)

### Atomic Commits

- **Always split unrelated changes into separate commits**, even if the user says "commit all"
- Group by feature/fix/refactor — one logical change per commit
- This applies to both user-requested commits and post-work commits
- Example: markitdown integration + sidebar fix + interrupt UI = 3 separate commits
- Makes `git log`, `git bisect`, and code review much easier

## Git Safety Rules (MANDATORY)

These rules exist because of a real data loss incident. **Do not skip them.**

### Stash

- **NEVER use `git stash --include-untracked`** when there are important untracked files
- Before any stash: run `git status` and review untracked files
- Important untracked files must be `git add`-ed or committed to a temp branch first
- Use `git stash pop` instead of `git stash apply` + `git stash drop`
- Before `git stash drop`: always confirm all content has been restored

### Parallel Development (Worktrees)

Before starting parallel worktree development:
1. Commit ALL important untracked files (or at least `git add` them)
2. Ensure `.gitignore` covers `node_modules/` and other large generated dirs
3. Working tree must be clean (`git status` shows nothing important)
4. Worktree agents MUST commit their changes on their branch (not leave uncommitted changes)
5. Merge via `git merge` / `git cherry-pick`, not manual file copying

## Frontend Build Safety

- **NEVER run `rm -rf frontend/.next`** while the dev server is running — this kills Turbopack HMR
- Production builds use a separate dir: `.next-build` (configured via `distDir` in `next.config.ts`)
- To build: just run `cd frontend && pnpm build` — the build script handles cleaning `.next-build`
- **NEVER run `rm -rf .next && next build`** — use `pnpm build` which only cleans `.next-build`

## Frontend UI Conventions

- **NEVER use native browser dialogs** (`window.confirm()`, `window.alert()`, `window.prompt()`). Always use shadcn/ui components instead:
  - Confirmations → `AlertDialog` (`@/components/ui/alert-dialog`)
  - Notifications → Toast or inline feedback
  - Input prompts → `Dialog` (`@/components/ui/dialog`)

## Code Conventions

- Type hints on all public functions
- Async-first: use `async def` for I/O-bound operations
- Tests alongside features: every new module gets a corresponding `tests/test_*.py`
- Keep imports in `__init__.py` minimal — only re-export public API

## Post-Commit Documentation Sync (MANDATORY)

After every `feat:` commit, you MUST check and update these files before moving on:

1. **`example.env`** — compare with `.env`, any new key must be added with placeholder and comment
2. **`wiki/Roadmap.md`** — check off `[ ]` items that this feature completes; if the feature has no matching entry, insert a new item under the appropriate version/category and mark it as done
3. **`README.md` Roadmap section** — update Shipped/Next summary if a milestone version was completed
4. **`README.md` Key Features** — add entry if this is a new user-facing capability
5. **`README.md` Project Structure** — update if new modules/directories were added under `src/`
6. **`wiki/` pages** — update if architecture, execution modes, or competitive positioning changed
7. **Wiki sync** — if any `wiki/*.md` file was modified, run `./scripts/sync-wiki.sh`

Do NOT ask the user whether to do these checks. Just do them silently after each feat commit.
