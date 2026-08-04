"""Tests for output-truncation continuation (finish_reason == "length")."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fim_one.core.agent import AgentResult, ReActAgent
from fim_one.core.model import BaseLLM, ChatMessage, LLMResult, StreamChunk
from fim_one.core.tool import ToolRegistry

from .conftest import EchoTool


class TruncatingLLM(BaseLLM):
    """Streams scripted ``(content, finish_reason)`` responses in order.

    Each script entry becomes one full stream: content deltas followed by
    a final chunk carrying the finish_reason.  When the scripts run out,
    the last one repeats.
    """

    def __init__(self, scripts: list[tuple[str, str]]) -> None:
        self._scripts = scripts
        self.calls = 0
        self.seen_messages: list[list[ChatMessage]] = []

    @property
    def abilities(self) -> dict[str, bool]:
        return {"tool_call": True, "streaming": True}

    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs: Any,
    ) -> LLMResult:
        idx = min(self.calls, len(self._scripts) - 1)
        self.calls += 1
        content, finish = self._scripts[idx]
        return LLMResult(
            message=ChatMessage(role="assistant", content=content),
            finish_reason=finish,
        )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        idx = min(self.calls, len(self._scripts) - 1)
        self.calls += 1
        self.seen_messages.append(list(messages))
        content, finish = self._scripts[idx]
        yield StreamChunk(delta_content=content)
        yield StreamChunk(finish_reason=finish, usage={"total_tokens": 1})


class TestNativeLoopContinuation:
    async def test_truncated_answer_is_stitched(self) -> None:
        llm = TruncatingLLM(
            [
                ("The answer begins ", "length"),
                ("and here it ends.", "stop"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("long question")

        assert result.answer == "The answer begins and here it ends."
        # The continuation prompt was injected between the two segments.
        assert any(
            m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("[Output truncated]")
            for m in result.messages
        )

    async def test_continuation_bounded(self) -> None:
        # Every response is truncated — the loop must not continue forever.
        llm = TruncatingLLM([("chunk ", "length")])
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            completion_check=False,
            enable_plan_tool=False,
            max_iterations=20,
        )

        result = await agent.run("q")

        # 3 continuations max → 4 segments stitched, then the loop accepts
        # the (still truncated) answer instead of spinning.
        assert result.answer == "chunk " * 4
        prompts = [
            m
            for m in result.messages
            if m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("[Output truncated]")
        ]
        assert len(prompts) == 3

    async def test_no_continuation_on_stop(self) -> None:
        llm = TruncatingLLM([("complete answer", "stop")])
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("q")

        assert result.answer == "complete answer"
        assert llm.calls == 1


class TestStreamAnswerContinuation:
    async def test_synthesis_stream_continues_past_truncation(self) -> None:
        llm = TruncatingLLM(
            [
                ("first half ", "length"),
                ("second half", "stop"),
            ]
        )
        agent = ReActAgent(
            llm=llm,
            tools=ToolRegistry(),
            enable_plan_tool=False,
        )
        # steps=[] → not ended_with_answer → synthesis path streams.
        result = AgentResult(answer="", steps=[], iterations=1, messages=[])

        chunks = [c async for c in agent.stream_answer("q", result)]

        assert "".join(chunks) == "first half second half"
        # The second call's message list carries the partial assistant
        # output plus the continuation prompt.
        second_call = llm.seen_messages[1]
        assert any(
            m.role == "assistant" and m.content == "first half " for m in second_call
        )
        assert any(
            m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("[Output truncated]")
            for m in second_call
        )

    async def test_synthesis_stops_cleanly_without_truncation(self) -> None:
        llm = TruncatingLLM([("whole answer", "stop")])
        agent = ReActAgent(
            llm=llm,
            tools=ToolRegistry(),
            enable_plan_tool=False,
        )
        result = AgentResult(answer="", steps=[], iterations=1, messages=[])

        chunks = [c async for c in agent.stream_answer("q", result)]

        assert "".join(chunks) == "whole answer"
        assert llm.calls == 1


class TruncatedToolCallLLM(BaseLLM):
    """First turn is a tool call the output limit cut off, then a normal turn."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[ChatMessage]] = []

    @property
    def abilities(self) -> dict[str, bool]:
        return {"tool_call": True, "streaming": True}

    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs: Any,
    ) -> LLMResult:  # pragma: no cover - native loop streams
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        self.seen_messages.append(list(messages))
        if self.calls == 1:
            # The model narrated its intent, then blew the budget writing the
            # tool call — the shim drops the half-written call and reports it.
            yield StreamChunk(delta_content="I will now write the file.")
            yield StreamChunk(finish_reason="length", truncated_tool_call=True)
        else:
            yield StreamChunk(delta_content="Wrote it in two smaller parts.")
            yield StreamChunk(finish_reason="stop")


class TestTruncatedToolCallRetry:
    async def test_preamble_is_not_accepted_as_the_answer(self) -> None:
        llm = TruncatedToolCallLLM()
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            use_native_tools=True,
            completion_check=False,
            enable_plan_tool=False,
        )

        result = await agent.run("write a big html file")

        # The truncated turn only announced the work — it must not stand in
        # for the work itself.
        assert result.answer == "Wrote it in two smaller parts."
        assert llm.calls == 2
        assert any(
            m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("[Tool call truncated]")
            for m in result.messages
        )
