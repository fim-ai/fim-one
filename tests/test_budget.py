"""Tests for the model-aware input-budget helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from fim_one.core.memory.budget import (
    DEFAULT_INPUT_BUDGET,
    DEFAULT_MAX_OUTPUT_RESERVE,
    compute_input_budget,
    declared_input_budget,
    llm_input_budget,
)
from fim_one.core.model import OpenAICompatibleLLM
from fim_one.core.model.fallback import FallbackLLM

from .fake_llm import FakeLLM


class TestDeclaredInputBudget:
    def test_uses_the_models_own_window(self) -> None:
        llm = FakeLLM(context_size=200_000, max_output_tokens=32_000)
        assert declared_input_budget(llm) == compute_input_budget(200_000, 32_000)

    def test_stays_below_the_hard_limit(self) -> None:
        llm = FakeLLM(context_size=16_000, max_output_tokens=4_000)
        budget = declared_input_budget(llm)
        assert budget is not None
        assert budget < 16_000 - 4_000

    def test_no_window_metadata_is_none(self) -> None:
        assert declared_input_budget(FakeLLM()) is None

    def test_missing_max_output_reserves_the_default(self) -> None:
        llm = FakeLLM(context_size=200_000)
        assert declared_input_budget(llm) == compute_input_budget(
            200_000, DEFAULT_MAX_OUTPUT_RESERVE
        )

    def test_mock_attributes_are_not_a_window(self) -> None:
        # A MagicMock answers every attribute with another mock; that must
        # read as "unknown", not crash the arithmetic.
        assert declared_input_budget(MagicMock()) is None

    def test_bool_and_non_positive_are_not_a_window(self) -> None:
        llm = MagicMock()
        llm.context_size = True
        assert declared_input_budget(llm) is None
        llm.context_size = 0
        assert declared_input_budget(llm) is None


class TestLlmInputBudget:
    def test_unknown_model_falls_back_to_the_documented_default(self) -> None:
        # CONTEXT_GUARD_DEFAULT_BUDGET, 32 000 tokens unless overridden.
        assert llm_input_budget(FakeLLM()) == DEFAULT_INPUT_BUDGET
        assert llm_input_budget(None) == DEFAULT_INPUT_BUDGET

    def test_unknown_model_uses_the_callers_default(self) -> None:
        assert llm_input_budget(FakeLLM(), default=58_880) == 58_880

    def test_known_model_ignores_the_default(self) -> None:
        llm = FakeLLM(context_size=16_000, max_output_tokens=4_000)
        assert llm_input_budget(llm, default=58_880) == compute_input_budget(16_000, 4_000)


class TestMaxOutputTokensProperty:
    def _llm(self, context_size: int, max_tokens: int) -> OpenAICompatibleLLM:
        return OpenAICompatibleLLM(
            api_key="k",
            base_url="http://localhost/v1",
            model="m",
            default_max_tokens=max_tokens,
            context_size=context_size,
        )

    def test_openai_compatible_reports_its_default_max_tokens(self) -> None:
        llm = self._llm(128_000, 8_000)
        assert llm.max_output_tokens == 8_000
        assert declared_input_budget(llm) == compute_input_budget(128_000, 8_000)

    def test_fallback_wrapper_reports_the_primary(self) -> None:
        # chat.py wraps the main model in FallbackLLM; the wrapper used to
        # hide the output reserve, so the budget fell back to 64 000.
        wrapped = FallbackLLM(primary=self._llm(128_000, 8_000), fallback=self._llm(32_000, 4_000))
        assert wrapped.max_output_tokens == 8_000
        assert declared_input_budget(wrapped) == compute_input_budget(128_000, 8_000)
