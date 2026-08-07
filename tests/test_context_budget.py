"""Tests for input-budget computation and the startup budget invariant check."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fim_one.core.memory.context_guard import (
    BUDGET_SAFETY_FACTOR,
    compute_input_budget,
)
from fim_one.web.deps import (
    _compute_input_budget,
    warn_on_context_budget_mismatch,
)


class TestComputeInputBudget:
    def test_safety_margin_applied(self) -> None:
        assert compute_input_budget(272_000, 64_000) == int(
            (272_000 - 64_000) * BUDGET_SAFETY_FACTOR
        )

    def test_budget_stays_below_hard_limit(self) -> None:
        # The margin must leave real headroom below the API hard limit,
        # otherwise any token-estimation error becomes an HTTP 400.
        assert compute_input_budget(272_000, 64_000) < 272_000 - 64_000

    def test_floor_at_4k(self) -> None:
        assert compute_input_budget(8_000, 7_000) == 4_000
        assert compute_input_budget(1_000, 64_000) == 4_000

    def test_deps_wrapper_delegates(self) -> None:
        assert _compute_input_budget(272_000, 64_000) == compute_input_budget(
            272_000, 64_000
        )


def _db_returning(*cfgs: object) -> MagicMock:
    """Mock AsyncSession whose successive execute() calls yield *cfgs*."""
    db = MagicMock()
    results = []
    for cfg in cfgs:
        res = MagicMock()
        res.scalar_one_or_none.return_value = cfg
        results.append(res)
    db.execute = AsyncMock(side_effect=results)
    return db


def _mismatch_records(caplog) -> list[logging.LogRecord]:  # type: ignore[no-untyped-def]
    return [r for r in caplog.records if "input capacity" in r.getMessage()]


class TestBudgetInvariantCheck:
    async def test_warns_when_fast_capacity_below_general_budget(
        self, caplog,  # type: ignore[no-untyped-def]
    ) -> None:
        general = SimpleNamespace(context_size=272_000, max_output_tokens=64_000)
        fast = SimpleNamespace(context_size=128_000, max_output_tokens=32_000)
        # Call order: general budget lookup, then fast capacity lookup.
        db = _db_returning(general, fast)
        with caplog.at_level(logging.WARNING, logger="fim_one.web.deps"):
            await warn_on_context_budget_mismatch(db)
        assert _mismatch_records(caplog)

    async def test_silent_when_fast_capacity_sufficient(
        self, caplog,  # type: ignore[no-untyped-def]
    ) -> None:
        general = SimpleNamespace(context_size=272_000, max_output_tokens=64_000)
        fast = SimpleNamespace(context_size=272_000, max_output_tokens=32_000)
        db = _db_returning(general, fast)
        with caplog.at_level(logging.WARNING, logger="fim_one.web.deps"):
            await warn_on_context_budget_mismatch(db)
        assert not _mismatch_records(caplog)

    async def test_fast_falls_back_to_general_row(
        self, caplog,  # type: ignore[no-untyped-def]
    ) -> None:
        # No fast row configured: capacity comes from the general row, which
        # by construction equals the budget's base and passes the check.
        general = SimpleNamespace(context_size=272_000, max_output_tokens=64_000)
        db = _db_returning(general, None, general)
        with caplog.at_level(logging.WARNING, logger="fim_one.web.deps"):
            await warn_on_context_budget_mismatch(db)
        assert not _mismatch_records(caplog)
