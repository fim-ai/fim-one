#!/usr/bin/env python3
"""Behavioral-eval freshness stamp.

The eval suite (``evals/``) hits a real LLM: token cost, minutes of wall
time, non-deterministic results. It therefore cannot run inside the
pre-commit hook. Instead, this script gives the hook a zero-cost proxy:

- After a green ``uv run pytest evals/ -q`` run, conftest calls
  ``eval_stamp.py --write`` which records a fingerprint (sha256) of every
  behavior-sensitive file as it existed in the working tree.
- The pre-commit hook calls ``eval_stamp.py --check-staged``. If the
  commit stages a change to any watched file and the staged content does
  not match the stamped fingerprint, the commit is rejected with a
  reminder to run the evals (or bypass with ``SKIP_EVALS=1``).

The watch list lives HERE and only here — conftest and the hook both go
through this script, so the two sides can never drift.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAMP_FILE = PROJECT_ROOT / "evals" / ".eval-stamp"

# Files whose changes alter agent *behavior* and therefore require a fresh
# behavioral-eval pass before commit (see evals/README.md "When to run").
WATCHED_EXACT = [
    "src/fim_one/core/agent/system_prompt.py",
    "src/fim_one/core/agent/react.py",
]
WATCHED_GLOBS = [
    "src/fim_one/core/tool/builtin/*.py",
]


def watched_files() -> list[str]:
    """All watched paths (repo-relative, sorted, existing or not)."""
    paths = set(WATCHED_EXACT)
    for pattern in WATCHED_GLOBS:
        paths.update(
            str(p.relative_to(PROJECT_ROOT))
            for p in PROJECT_ROOT.glob(pattern)
        )
    return sorted(paths)


def _hash(contents: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for path, data in contents:
        h.update(path.encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def worktree_hash() -> str:
    """Fingerprint of watched files as they exist on disk (eval time)."""
    contents = []
    for rel in watched_files():
        p = PROJECT_ROOT / rel
        contents.append((rel, p.read_bytes() if p.is_file() else b""))
    return _hash(contents)


def staged_hash() -> str:
    """Fingerprint of watched files as they exist in the git index."""
    contents = []
    for rel in watched_files():
        proc = subprocess.run(
            ["git", "show", f":{rel}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        contents.append((rel, proc.stdout if proc.returncode == 0 else b""))
    return _hash(contents)


def staged_watched() -> list[str]:
    """Watched files that are staged for the current commit."""
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMDR"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    staged = set(proc.stdout.split())
    return [f for f in watched_files() if f in staged]


def cmd_write() -> int:
    STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(worktree_hash() + "\n")
    return 0


def cmd_check_staged() -> int:
    dirty = staged_watched()
    if not dirty:
        return 0
    stamped = STAMP_FILE.read_text().strip() if STAMP_FILE.is_file() else ""
    if stamped == staged_hash():
        return 0
    print("", file=sys.stderr)
    print(
        "ERROR: commit changes agent-behavior files without a fresh "
        "behavioral-eval pass:",
        file=sys.stderr,
    )
    for f in dirty:
        print(f"  {f}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "These files shape model behavior (system prompt / tool "
        "descriptions / ReAct loop).",
        file=sys.stderr,
    )
    print("Run the behavioral evals, then re-commit:", file=sys.stderr)
    print("", file=sys.stderr)
    print("  uv run pytest evals/ -q        # ~2 min, real LLM", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "A green run stamps evals/.eval-stamp and the commit will pass.",
        file=sys.stderr,
    )
    print(
        "Bypass (emergency / no LLM_API_KEY / agent worktree):",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print("  SKIP_EVALS=1 git commit ...", file=sys.stderr)
    print("", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("--write", "--check-staged"):
        print(
            "usage: eval_stamp.py --write | --check-staged", file=sys.stderr
        )
        return 2
    return cmd_write() if sys.argv[1] == "--write" else cmd_check_staged()


if __name__ == "__main__":
    sys.exit(main())
