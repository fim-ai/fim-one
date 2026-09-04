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
from typing import Any

import pytest
from dotenv import load_dotenv

from .harness import LEDGER

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Dataset tiers, as pytest markers (registered in pyproject.toml).  A case
# carries exactly one; anything unmarked lands in "untiered" so it shows up
# in the report instead of disappearing.
_TIERS = ("regression", "challenge")
_UNTIERED = "untiered"

# Fill os.environ from .env without clobbering explicitly-set variables,
# so ``LLM_MODEL=... uv run pytest evals/`` can still override per-run.
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _tier_of(item: pytest.Item) -> str:
    """The tier marker on *item*, or ``untiered``."""
    for tier in _TIERS:
        if item.get_closest_marker(tier) is not None:
            return tier
    return _UNTIERED


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        _tier_by_nodeid[item.nodeid] = _tier_of(item)
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
_skipped_count = 0
_deselected_count = 0

# Per-tier bookkeeping for the pass-rate breakdown and the metrics report.
_tier_by_nodeid: dict[str, str] = {}
_outcome_by_nodeid: dict[str, str] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _passed_count, _skipped_count
    if report.when == "call" and report.passed:
        _passed_count += 1
        _outcome_by_nodeid[report.nodeid] = "passed"
    if report.failed:
        _outcome_by_nodeid[report.nodeid] = "failed"
    if report.skipped:
        _skipped_count += 1
        _outcome_by_nodeid.setdefault(report.nodeid, "skipped")


def pytest_deselected(items: list[pytest.Item]) -> None:
    global _deselected_count
    _deselected_count += len(items)


def _tier_report() -> dict[str, dict[str, Any]]:
    """Pass rate per tier — a regression miss and a challenge miss are not
    the same event, so they are never averaged into one number."""
    report: dict[str, dict[str, Any]] = {}
    for nodeid, tier in _tier_by_nodeid.items():
        bucket = report.setdefault(
            tier, {"passed": 0, "failed": 0, "skipped": 0, "total": 0}
        )
        bucket[_outcome_by_nodeid.get(nodeid, "skipped")] += 1
        bucket["total"] += 1
    for bucket in report.values():
        ran = bucket["passed"] + bucket["failed"]
        bucket["pass_rate"] = round(bucket["passed"] / ran, 3) if ran else None
    return report


def _write_metrics() -> None:
    """Dump per-case cost and execution facts collected by the harness."""
    tiers = _tier_report()
    for nodeid, tier in _tier_by_nodeid.items():
        LEDGER.annotate(
            nodeid,
            tier=tier,
            outcome=_outcome_by_nodeid.get(nodeid, "skipped"),
        )
    path = Path(
        os.environ.get("EVAL_METRICS_PATH")
        or _PROJECT_ROOT / "evals" / ".eval-metrics.json"
    )
    written = LEDGER.write(path, extra={"tiers": tiers})
    if written is None:
        return
    for tier, bucket in sorted(tiers.items()):
        rate = bucket["pass_rate"]
        print(
            f"eval tier {tier}: {bucket['passed']}/{bucket['total']} passed"
            + (f" (pass rate {rate})" if rate is not None else " (none ran)"),
            file=sys.stderr,
        )
    print(f"eval metrics: {written}", file=sys.stderr)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _write_metrics()
    # Stamp only a genuinely green run with at least one real pass —
    # an all-skipped run (no LLM_API_KEY) proves nothing.
    if exitstatus != 0 or _passed_count == 0:
        return
    # ...and only a COMPLETE run. `pytest evals/ -k restyle` exits 0 having
    # exercised one case; stamping there would certify the whole watch list
    # against a run that never touched most of it, and the hook would then
    # wave through the very prompt change the other cases exist to catch.
    if _skipped_count or _deselected_count:
        print(
            "eval stamp: skipped "
            f"({_skipped_count} skipped, {_deselected_count} deselected) — "
            "stamping needs a full green run",
            file=sys.stderr,
        )
        return
    subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "scripts" / "eval_stamp.py"), "--write"],
        check=False,
    )
