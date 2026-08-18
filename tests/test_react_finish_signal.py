"""Tests for the finish-signal (FINAL-first) answering path.

When ``finish_signal=True`` the native loop advertises a synthetic
``finish`` tool.  A pure finish turn ends the loop WITHOUT an inline
answer (``AgentResult.finish_signaled``), and ``stream_answer()``
continues the same native history as a genuinely token-streamed turn.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import (
    _FINISH_ACK_PROMPT,
    _FINISH_CHECK_PROMPT,
    _FINISH_DEFER_PROMPT,
    _FINISH_TOOL_NAME,
)
from fim_one.core.agent.types import AgentResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool
from .fake_llm import NATIVE_TOOLS, FakeLLM, answer, tool_call, tool_calls


class FinishNamedTool(BaseTool):
    """A registry tool that clashes with the synthetic finish name."""

    @property
    def name(self) -> str:
        return _FINISH_TOOL_NAME

    @property
    def description(self) -> str:
        return "A user tool that happens to be called finish."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        return "real finish tool ran"


def _make_agent(
    llm: FakeLLM,
    *,
    finish_signal: bool = True,
    completion_check: bool = False,
    tools: ToolRegistry | None = None,
) -> ReActAgent:
    registry = tools
    if registry is None:
        registry = ToolRegistry()
        registry.register(EchoTool())
    return ReActAgent(
        llm=llm,
        tools=registry,
        max_iterations=8,
        completion_check=completion_check,
        finish_signal=finish_signal,
    )


class TestFinishToolAdvertised:
    def test_payload_includes_finish_when_enabled(self) -> None:
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=True)
        payload = agent._build_tools_payload()
        assert payload is not None
        names = [entry["function"]["name"] for entry in payload]
        assert _FINISH_TOOL_NAME in names
        assert agent._finish_tool_active is True

    def test_payload_excludes_finish_when_disabled(self) -> None:
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=False)
        payload = agent._build_tools_payload()
        assert payload is not None
        names = [entry["function"]["name"] for entry in payload]
        assert _FINISH_TOOL_NAME not in names
        assert agent._finish_tool_active is False

    def test_name_clash_disables_signal(self) -> None:
        registry = ToolRegistry()
        registry.register(FinishNamedTool())
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=True, tools=registry)
        payload = agent._build_tools_payload()
        assert payload is not None
        names = [entry["function"]["name"] for entry in payload]
        assert names.count(_FINISH_TOOL_NAME) == 1
        assert agent._finish_tool_active is False


class TestFinishPromptSelection:
    def test_finish_prompt_used_when_enabled(self) -> None:
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=True)
        prefix, _suffix = agent._build_system_prompt_split_native()
        assert "FINAL ANSWER PROTOCOL" in prefix

    def test_default_prompt_without_signal(self) -> None:
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=False)
        prefix, _suffix = agent._build_system_prompt_split_native()
        assert "FINAL ANSWER PROTOCOL" not in prefix

    def test_default_prompt_on_name_clash(self) -> None:
        registry = ToolRegistry()
        registry.register(FinishNamedTool())
        agent = _make_agent(FakeLLM(abilities=NATIVE_TOOLS, responses=[]), finish_signal=True, tools=registry)
        prefix, _suffix = agent._build_system_prompt_split_native()
        assert "FINAL ANSWER PROTOCOL" not in prefix


class TestFinishSignalHandoff:
    @pytest.mark.asyncio
    async def test_pure_finish_turn_hands_off(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call("echo", {"text": "hi"}, call_id="c1"),
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c2"),
            ],
        )
        agent = _make_agent(llm)
        callbacks: list[Any] = []

        def on_iteration(
            iteration: int,
            action: Any,
            observation: Any,
            error: Any,
            step_result: Any = None,
        ) -> None:
            callbacks.append((iteration, action))

        result = await agent.run("question", on_iteration=on_iteration)

        assert result.finish_signaled is True
        assert result.answer == ""
        # The handoff closes the iteration UI via a final_answer callback.
        assert callbacks[-1][1].type == "final_answer"
        assert callbacks[-1][1].answer == ""
        # The finish tool_use is paired with the ack tool_result.
        last = result.messages[-1]
        assert last.role == "tool"
        assert last.tool_call_id == "c2"
        assert last.content == _FINISH_ACK_PROMPT
        # The finish call itself never reaches the tool executor.
        assert all(
            step.action.tool_name != _FINISH_TOOL_NAME for step in result.steps
        )

    @pytest.mark.asyncio
    async def test_finish_disabled_treats_finish_as_unknown_tool(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c1"),
                answer("done inline"),
            ],
        )
        agent = _make_agent(llm, finish_signal=False)
        result = await agent.run("question")

        assert result.finish_signaled is False
        assert result.answer == "done inline"

    @pytest.mark.asyncio
    async def test_mixed_batch_defers_finish(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_calls(
                    [
                        ("c1", "echo", {"text": "hi"}),
                        ("c2", _FINISH_TOOL_NAME, {}),
                    ]
                ),
                answer("inline answer after deferral"),
            ],
        )
        agent = _make_agent(llm)
        result = await agent.run("question")

        # The deferred signal did not end the loop.
        assert result.finish_signaled is False
        assert result.answer == "inline answer after deferral"
        deferrals = [
            m
            for m in result.messages
            if m.role == "tool" and m.content == _FINISH_DEFER_PROMPT
        ]
        assert len(deferrals) == 1
        assert deferrals[0].tool_call_id == "c2"
        # The echo call still ran.
        assert any(
            step.action.tool_name == "echo" and step.observation == "hi"
            for step in result.steps
        )

    @pytest.mark.asyncio
    async def test_completion_checklist_via_finish_result(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call("echo", {"text": "a"}, call_id="c1"),
                tool_call("echo", {"text": "b"}, call_id="c2"),
                tool_call("echo", {"text": "c"}, call_id="c3"),
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c4"),
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c5"),
            ],
        )
        agent = _make_agent(llm, completion_check=True)
        result = await agent.run("question")

        assert result.finish_signaled is True
        # First finish got the re-call checklist as its tool_result...
        checklist_replies = [
            m
            for m in result.messages
            if m.role == "tool"
            and isinstance(m.content, str)
            and m.content.startswith(_FINISH_CHECK_PROMPT)
        ]
        assert len(checklist_replies) == 1
        assert checklist_replies[0].tool_call_id == "c4"
        # ...and the second finish was accepted.
        assert result.messages[-1].content == _FINISH_ACK_PROMPT
        assert result.messages[-1].tool_call_id == "c5"


class TestStreamAnswerFinishBranch:
    @pytest.mark.asyncio
    async def test_streams_native_continuation(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call("echo", {"text": "hi"}, call_id="c1"),
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c2"),
                answer("the real streamed answer"),
            ],
        )
        agent = _make_agent(llm)
        result = await agent.run("question")
        assert result.finish_signaled is True

        chunks = [
            chunk async for chunk in agent.stream_answer("question", result)
        ]
        assert "".join(chunks) == "the real streamed answer"
        assert result.answer == "the real streamed answer"

        # The handoff call continues the native history with the loop's
        # tools payload and tool_choice="none".
        handoff = llm.stream_calls[-1]
        assert handoff.tool_choice == "none"
        assert handoff.tools is not None
        tool_names = [entry["function"]["name"] for entry in handoff.tools]
        assert _FINISH_TOOL_NAME in tool_names
        # The full trajectory (finish ack included) is in the request.
        assert any(
            m.role == "tool" and m.content == _FINISH_ACK_PROMPT
            for m in handoff.messages
        )

    @pytest.mark.asyncio
    async def test_language_directive_folded_into_instruction(self) -> None:
        """The directive must ride inside a write-the-answer instruction.

        A bare "(Language note: ...)" user message reads as the newest user
        request and the model answers IT instead of the original question.
        """
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call("echo", {"text": "hi"}, call_id="c1"),
                tool_call(_FINISH_TOOL_NAME, {}, call_id="c2"),
                answer("最终答案"),
            ],
        )
        agent = _make_agent(llm)
        result = await agent.run("question")

        chunks = [
            chunk
            async for chunk in agent.stream_answer(
                "question",
                result,
                language_directive="Respond in Simplified Chinese.",
            )
        ]
        assert "".join(chunks) == "最终答案"
        last_user = [
            m for m in llm.stream_calls[-1].messages if m.role == "user"
        ][-1]
        assert "final answer" in last_user.content
        assert "Respond in Simplified Chinese." in last_user.content

    @pytest.mark.asyncio
    async def test_replay_path_untouched_without_signal(self) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=
            [
                tool_call("echo", {"text": "hi"}, call_id="c1"),
                answer("inline final answer"),
            ],
        )
        agent = _make_agent(llm, finish_signal=False)
        result = await agent.run("question")
        assert result.finish_signaled is False

        stream_calls_before = len(llm.stream_calls)
        chunks = [
            chunk async for chunk in agent.stream_answer("question", result)
        ]
        assert "".join(chunks) == "inline final answer"
        # Approach A replays the buffered answer without another LLM call.
        assert len(llm.stream_calls) == stream_calls_before
