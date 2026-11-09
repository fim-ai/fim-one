"""Tests for input-budget computation and the startup budget invariant check."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.core.memory.context_guard import (
    BUDGET_SAFETY_FACTOR,
    ContextGuard,
    compute_input_budget,
)
from fim_one.core.planner.executor import DAGExecutor
from fim_one.web.deps import (
    ChatContextBudgets,
    _compute_input_budget,
    resolve_chat_context_budgets,
    warn_on_context_budget_mismatch,
)

from .fake_llm import FakeLLM


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


def _model(context_size: int | None, max_output_tokens: int | None = None) -> FakeLLM:
    return FakeLLM(context_size=context_size, max_output_tokens=max_output_tokens)


class TestResolveChatContextBudgets:
    """Chat budgets come from the models a turn resolved, not a global."""

    async def test_small_main_large_fast(self) -> None:
        main = _model(16_000, 4_000)
        fast = _model(200_000, 8_000)
        budgets = await resolve_chat_context_budgets(main, fast, MagicMock())
        assert budgets.main == compute_input_budget(16_000, 4_000)
        assert budgets.fast == compute_input_budget(200_000, 8_000)
        # One message list goes to both models, so the smaller window rules.
        assert budgets.shared == budgets.main

    async def test_large_main_small_fast(self) -> None:
        main = _model(1_000_000, 64_000)
        fast = _model(32_000, 4_000)
        budgets = await resolve_chat_context_budgets(main, fast, MagicMock())
        assert budgets.main == compute_input_budget(1_000_000, 64_000)
        assert budgets.fast == compute_input_budget(32_000, 4_000)
        assert budgets.shared == budgets.fast

    async def test_system_default_is_ignored_when_the_model_declares_a_window(
        self,
    ) -> None:
        # The agent picked a 16k model while the system "general" model is
        # 272k: the old code budgeted the 16k model at the 272k figure.
        system_general = compute_input_budget(272_000, 64_000)
        main = _model(16_000, 4_000)
        budgets = await resolve_chat_context_budgets(
            main, main, MagicMock(), general_default=system_general
        )
        assert budgets.shared == compute_input_budget(16_000, 4_000)

    async def test_fallback_wrapped_main_keeps_its_own_window(self) -> None:
        from fim_one.core.model.fallback import FallbackLLM

        main = _model(16_000, 4_000)
        fast = _model(200_000, 8_000)
        wrapped = FallbackLLM(primary=main, fallback=fast)
        budgets = await resolve_chat_context_budgets(wrapped, fast, MagicMock())
        assert budgets.main == compute_input_budget(16_000, 4_000)

    async def test_unknown_models_use_the_supplied_system_budgets(self) -> None:
        db = MagicMock()
        db.execute = AsyncMock(side_effect=AssertionError("no query expected"))
        budgets = await resolve_chat_context_budgets(
            _model(None), _model(None), db, general_default=58_880, fast_default=29_440
        )
        assert budgets == ChatContextBudgets(main=58_880, fast=29_440)

    async def test_unknown_models_fall_back_to_the_system_role_config(self) -> None:
        general = SimpleNamespace(context_size=272_000, max_output_tokens=64_000)
        fast = SimpleNamespace(context_size=128_000, max_output_tokens=32_000)
        # Call order: general budget lookup, then fast budget lookup.
        db = _db_returning(general, fast)
        budgets = await resolve_chat_context_budgets(_model(None), _model(None), db)
        assert budgets.main == compute_input_budget(272_000, 64_000)
        assert budgets.fast == compute_input_budget(128_000, 32_000)

    async def test_unknown_model_without_any_config_uses_the_env_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Documented default: a 128 000-token window less 64 000 output.
        for name in (
            "LLM_CONTEXT_SIZE",
            "LLM_MAX_OUTPUT_TOKENS",
            "FAST_LLM_CONTEXT_SIZE",
            "FAST_LLM_MAX_OUTPUT_TOKENS",
        ):
            monkeypatch.delenv(name, raising=False)
        db = _db_returning(None, None, None)
        budgets = await resolve_chat_context_budgets(_model(None), _model(None), db)
        assert budgets.main == compute_input_budget(128_000, 64_000)
        assert budgets.fast == compute_input_budget(128_000, 64_000)

    async def test_no_fast_model_means_one_budget(self) -> None:
        main = _model(16_000, 4_000)
        budgets = await resolve_chat_context_budgets(main, None, MagicMock())
        assert budgets.fast == budgets.main == budgets.shared


class TestExecutorModelAwareGuard:
    """DAG steps get a guard sized to the step model, via the shared helper."""

    def _executor(self, guard: ContextGuard) -> DAGExecutor:
        return DAGExecutor(agent=MagicMock(), context_guard=guard)

    def test_step_guard_uses_the_step_models_own_output_reserve(self) -> None:
        # The executor used to read a private attribute that only one LLM
        # class has; any other model was charged a 64 000-token reserve,
        # which floors a 32k model's budget at 4 000.
        base = ContextGuard(compact_llm=_model(128_000, 8_000), default_budget=50_000)
        step_llm = _model(32_000, 4_000)

        guard = self._executor(base)._build_model_aware_guard(step_llm)

        assert guard is not None and guard is not base
        assert guard._default_budget == compute_input_budget(32_000, 4_000)

    def test_step_guard_keeps_the_summarisers_budget(self) -> None:
        base = ContextGuard(
            compact_llm=_model(None), default_budget=50_000, compact_input_budget=4_321
        )

        guard = self._executor(base)._build_model_aware_guard(_model(32_000, 4_000))

        assert guard is not None
        assert guard.compact_input_budget == 4_321

    def test_unknown_step_model_reuses_the_existing_guard(self) -> None:
        base = ContextGuard(compact_llm=_model(128_000, 8_000), default_budget=50_000)
        assert self._executor(base)._build_model_aware_guard(_model(None)) is base


class TestEscalatedMainModel:
    """Domain escalation swaps the main model after budgets were resolved."""

    def test_smaller_escalated_model_lowers_the_shared_budget(self) -> None:
        # General 200k, reasoning 64k: the reasoning model writes the final
        # answer from the same message list, so its window must count.
        budgets = ChatContextBudgets(
            main=compute_input_budget(200_000, 8_000),
            fast=compute_input_budget(200_000, 8_000),
        )
        escalated = budgets.with_main_model(_model(64_000, 8_000))
        assert escalated.main == compute_input_budget(64_000, 8_000)
        assert escalated.shared == compute_input_budget(64_000, 8_000)
        assert escalated.fast == budgets.fast

    def test_larger_escalated_model_is_still_capped_by_the_fast_model(self) -> None:
        budgets = ChatContextBudgets(main=50_000, fast=20_000)
        escalated = budgets.with_main_model(_model(1_000_000, 64_000))
        assert escalated.main == compute_input_budget(1_000_000, 64_000)
        assert escalated.shared == 20_000

    def test_unknown_escalated_model_keeps_the_replaced_models_budget(self) -> None:
        budgets = ChatContextBudgets(main=50_000, fast=20_000)
        assert budgets.with_main_model(_model(None)) == budgets


class TestOutputReserveLargerThanWindow:
    """A small model whose max output was left at the 64k default."""

    def test_reserve_as_large_as_the_window_reserves_half_instead(self) -> None:
        # Without this, a 32k fast model was budgeted at the 4 000 floor and,
        # as the smaller of the two chat models, starved the whole turn.
        assert compute_input_budget(32_000, 64_000) == int(16_000 * BUDGET_SAFETY_FACTOR)
        assert compute_input_budget(64_000, 64_000) == int(32_000 * BUDGET_SAFETY_FACTOR)

    def test_a_consistent_reserve_is_taken_as_configured(self) -> None:
        assert compute_input_budget(100_000, 64_000) == int(36_000 * BUDGET_SAFETY_FACTOR)

    async def test_small_fast_model_with_default_max_output_does_not_starve_chat(
        self,
    ) -> None:
        main = _model(200_000, 64_000)
        fast = _model(32_000, 64_000)
        budgets = await resolve_chat_context_budgets(main, fast, MagicMock())
        assert budgets.shared == int(16_000 * BUDGET_SAFETY_FACTOR)
