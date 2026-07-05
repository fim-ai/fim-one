"""Eval-suite conftest: load real LLM credentials or skip everything.

Evals hit a real LLM endpoint.  Credentials come from the project root
``.env`` (same keys the app uses: ``LLM_API_KEY`` / ``LLM_BASE_URL`` /
``LLM_MODEL``).  When no key is available the whole directory is skipped
rather than failed, so an accidental ``pytest evals/`` on a machine
without credentials stays green.
"""

from __future__ import annotations

import os
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
