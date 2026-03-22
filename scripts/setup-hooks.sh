#!/usr/bin/env bash
# Install git hooks for this repo.
# Run once after cloning: bash scripts/setup-hooks.sh

set -e

HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

ln -sf "$SCRIPT_DIR/hooks/pre-commit" "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit"

# Install MDX validator dependencies
if command -v pnpm >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/validate-mdx/package.json" ]; then
  (cd "$SCRIPT_DIR/validate-mdx" && pnpm install --silent 2>/dev/null)
  echo "MDX validator installed"
fi

echo "Git hooks installed"
