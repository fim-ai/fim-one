# FIM Agent — Claude Code Instructions

## Project Overview

FIM Agent is a Python async agent framework (Dify alternative). Provider-agnostic, minimal-abstraction, protocol-first.

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
│   └── tools/       # Tool base classes
├── web/             # FastAPI backend API
frontend/            # Next.js portal (shadcn/ui)
```

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

## Code Conventions

- Type hints on all public functions
- Async-first: use `async def` for I/O-bound operations
- Tests alongside features: every new module gets a corresponding `tests/test_*.py`
- Keep imports in `__init__.py` minimal — only re-export public API

## Post-Feature Checklist

After completing a feature, always:
1. **Sync `example.env`** — if any new environment variable was added, update `example.env` with a placeholder and comment
2. **Update roadmap** — update **both** `README.md` Roadmap section **and** `wiki/Roadmap.md` to reflect what shipped
3. **Review docs** — check if README Key Features, Architecture, or wiki pages need updating for the new feature
