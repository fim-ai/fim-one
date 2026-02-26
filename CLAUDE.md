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

## Post-Feature Checks (automated)

A PostToolUse hook (`scripts/post-commit-check.sh`) runs automatically after every `git commit` and checks:
- `example.env` vs `.env` key alignment
- `wiki/` changes that need `./scripts/sync-wiki.sh`
- Source module additions/removals that may require README Project Structure updates
- README vs wiki Roadmap sync
- New built-in tools that should be mentioned in README Key Features

No manual checklist needed. The hook will surface warnings when relevant.
