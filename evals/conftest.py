"""Eval-suite conftest: load real LLM credentials or skip everything.

Evals hit a real LLM endpoint.  Credentials come from the project root
``.env`` (same keys the app uses: ``LLM_API_KEY`` / ``LLM_BASE_URL`` /
``LLM_MODEL``).  When no key is available the whole directory is skipped
rather than failed, so an accidental ``pytest evals/`` on a machine
without credentials stays green.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Fill os.environ from .env without clobbering explicitly-set variables,
# so ``LLM_MODEL=... uv run pytest evals/`` can still override per-run.
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("LLM_API_KEY"):
        return
    skip = pytest.mark.skip(
        reason="LLM_API_KEY not set — behavioral evals need a real LLM"
    )
    for item in items:
        item.add_marker(skip)


# ---------------------------------------------------------------------------
# Eval freshness stamp (consumed by the pre-commit hook)
#
# A green run writes evals/.eval-stamp — a fingerprint of the
# behavior-sensitive files (system prompt, tool descriptions, ReAct loop;
# list lives in scripts/eval_stamp.py). The pre-commit hook rejects
# commits that change those files without a matching stamp, so "changed
# the prompt, forgot to run the evals" can't slip through.
# ---------------------------------------------------------------------------

_passed_count = 0


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _passed_count
    if report.when == "call" and report.passed:
        _passed_count += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Stamp only a genuinely green run with at least one real pass —
    # an all-skipped run (no LLM_API_KEY) proves nothing.
    if exitstatus != 0 or _passed_count == 0:
        return
    subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "scripts" / "eval_stamp.py"), "--write"],
        check=False,
    )
