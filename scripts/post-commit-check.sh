#!/usr/bin/env bash
# Post-commit documentation freshness check.
# Called by Claude Code PostToolUse hook after Bash tool calls.
# Reads hook context from stdin (JSON), only acts on successful git commit commands.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INPUT=$(cat)

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
EXIT_CODE=$(echo "$INPUT" | jq -r '.tool_response.exitCode // 1')

# Only act on successful git commit
if [[ "$COMMAND" != *"git commit"* ]] || [ "$EXIT_CODE" -ne 0 ]; then
  exit 0
fi

WARNINGS=""

# ---------- 1. example.env vs .env key alignment ----------
if [ -f "$REPO_ROOT/.env" ] && [ -f "$REPO_ROOT/example.env" ]; then
  ENV_KEYS=$(grep -v '^#' "$REPO_ROOT/.env" | grep '=' | cut -d'=' -f1 | sort)
  EXAMPLE_KEYS=$(grep -v '^#' "$REPO_ROOT/example.env" | grep '=' | cut -d'=' -f1 | sort)
  MISSING=$(comm -23 <(echo "$ENV_KEYS") <(echo "$EXAMPLE_KEYS"))
  if [ -n "$MISSING" ]; then
    WARNINGS="${WARNINGS}\n- example.env is missing keys that exist in .env: ${MISSING//$'\n'/, }"
  fi
fi

# ---------- 2. wiki/ files changed but not synced ----------
WIKI_CHANGED=$(git diff HEAD~1 --name-only -- wiki/ 2>/dev/null || true)
if [ -n "$WIKI_CHANGED" ]; then
  WARNINGS="${WARNINGS}\n- wiki/ files were modified in this commit (${WIKI_CHANGED//$'\n'/, }). Run ./scripts/sync-wiki.sh to push to GitHub Wiki."
fi

# ---------- 3. src/ structure changed => check README Project Structure ----------
SRC_CHANGED=$(git diff HEAD~1 --diff-filter=AD --name-only -- 'src/fim_agent/**/__init__.py' 2>/dev/null || true)
if [ -n "$SRC_CHANGED" ]; then
  WARNINGS="${WARNINGS}\n- Source modules were added/removed. Check if README.md Project Structure section needs updating."
fi

# ---------- 4. Roadmap-related files changed ----------
ROADMAP_CHANGED=$(git diff HEAD~1 --name-only -- wiki/Roadmap.md 2>/dev/null || true)
README_CHANGED=$(git diff HEAD~1 --name-only -- README.md 2>/dev/null || true)
if [ -n "$ROADMAP_CHANGED" ] && [ -z "$README_CHANGED" ]; then
  WARNINGS="${WARNINGS}\n- wiki/Roadmap.md was updated but README.md was not. Check if the Roadmap summary in README needs syncing."
fi
if [ -n "$README_CHANGED" ] && [ -z "$ROADMAP_CHANGED" ]; then
  WARNINGS="${WARNINGS}\n- README.md was updated but wiki/Roadmap.md was not. Check if the wiki Roadmap needs syncing."
fi

# ---------- 5. New tool added => check README Key Features ----------
TOOL_ADDED=$(git diff HEAD~1 --diff-filter=A --name-only -- 'src/fim_agent/core/tool/builtin/*.py' 2>/dev/null || true)
if [ -n "$TOOL_ADDED" ]; then
  WARNINGS="${WARNINGS}\n- New built-in tool(s) added (${TOOL_ADDED//$'\n'/, }). Check if README Key Features and Project Structure mention them."
fi

# ---------- Output ----------
if [ -n "$WARNINGS" ]; then
  printf '{"additionalContext":"Post-commit doc check found issues:%b"}' "$WARNINGS"
fi

exit 0
