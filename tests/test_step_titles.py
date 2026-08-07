"""Tests for per-iteration step-title generation (``step_title`` SSE event).

``_generate_step_title`` labels one ReAct iteration via the fast LLM so the
frontend can show a rotating one-line header while the agent works.  The
helper must be failure-proof (return ``None``, never raise) and reject
degenerate labels (multi-line, over-long) rather than ship them to the UI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.web.api.chat import _generate_step_title


def _llm_returning(text: str) -> MagicMock:
    fake_result = MagicMock()
    fake_result.message.content = text
    fake_result.usage = {}
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=fake_result)
    return llm


class TestGenerateStepTitle:
    @pytest.mark.asyncio
    async def test_returns_clean_label(self) -> None:
        llm = _llm_returning("核实定价数据并准备比较分析")
        title = await _generate_step_title(llm, "DS 涨价吗", "reasoning...", "web_search")
        assert title == "核实定价数据并准备比较分析"

    @pytest.mark.asyncio
    async def test_strips_wrapping_quotes(self) -> None:
        llm = _llm_returning('"Checking current API pricing"')
        title = await _generate_step_title(llm, "q", "r", "web_search")
        assert title == "Checking current API pricing"

    @pytest.mark.asyncio
    async def test_rejects_multiline_output(self) -> None:
        llm = _llm_returning("First line\nSecond line")
        assert await _generate_step_title(llm, "q", "r", "t") is None

    @pytest.mark.asyncio
    async def test_rejects_overlong_output(self) -> None:
        llm = _llm_returning("x" * 200)
        assert await _generate_step_title(llm, "q", "r", "t") is None

    @pytest.mark.asyncio
    async def test_rejects_empty_output(self) -> None:
        llm = _llm_returning("")
        assert await _generate_step_title(llm, "q", "r", "t") is None

    @pytest.mark.asyncio
    async def test_swallows_llm_failure(self) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        assert await _generate_step_title(llm, "q", "r", "t") is None

    @pytest.mark.asyncio
    async def test_truncates_inputs_not_output(self) -> None:
        """Long reasoning is clipped in the prompt, not fatal."""
        llm = _llm_returning("A label")
        title = await _generate_step_title(llm, "q" * 5000, "r" * 5000, "t")
        assert title == "A label"
        sent = llm.chat.call_args.args[0]
        user_msg = sent[1].content
        assert len(user_msg) < 2000
