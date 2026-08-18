#!/usr/bin/env bash
# Install git hooks for this repo.
# Run once after cloning: bash scripts/setup-hooks.sh

set -e

HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# core.hooksPath overrides .git/hooks entirely. If it points anywhere but
# here — a stale absolute path left over from moving or copying the clone —
# git runs no hooks at all and says nothing: no translations, no locale
# guard, no eval stamp. Clear it so the symlink below is what actually runs.
CONFIGURED_HOOKS_PATH="$(git config --get core.hooksPath || true)"
if [ -n "$CONFIGURED_HOOKS_PATH" ] && [ "$CONFIGURED_HOOKS_PATH" != "$HOOKS_DIR" ]; then
  echo "core.hooksPath was set to $CONFIGURED_HOOKS_PATH — clearing it"
  git config --unset core.hooksPath
fi

ln -sf "$SCRIPT_DIR/hooks/pre-commit" "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit"

# The symlink target is absolute, so it also breaks when the clone moves.
# Verify the hook is actually reachable rather than assuming ln succeeded.
if [ ! -x "$HOOKS_DIR/pre-commit" ]; then
  echo "ERROR: $HOOKS_DIR/pre-commit is not executable after install" >&2
  exit 1
fi

# Install MDX validator dependencies. Optional: the hook skips MDX validation
# when they are missing, so a failure here must not abort the hook install
# (which has already happened above) or make this script look like it failed.
if command -v pnpm >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/validate-mdx/package.json" ]; then
  if (cd "$SCRIPT_DIR/validate-mdx" && pnpm install --silent >/dev/null 2>&1); then
    echo "MDX validator installed"
  elif [ -d "$SCRIPT_DIR/validate-mdx/node_modules" ]; then
    echo "MDX validator: install failed, keeping the existing node_modules"
  else
    echo "MDX validator dependencies unavailable — MDX validation will be skipped" >&2
  fi
fi

echo "Git hooks installed"
