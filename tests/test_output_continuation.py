"""Tests for output-truncation continuation (finish_reason == "length")."""

from __future__ import annotations

from fim_one.core.agent import AgentResult, ReActAgent
from fim_one.core.tool import ToolRegistry

from .conftest import EchoTool
from .fake_llm import NATIVE_TOOLS, FakeLLM, answer, truncated_tool_call


class TestNativeLoopContinuation:
    async def test_truncated_answer_is_stitched(self) -> None:
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                answer("The answer begins ", finish_reason="length"),
                answer("and here it ends.", finish_reason="stop"),
            ],
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
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[answer("chunk ", finish_reason="length")],
        )
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
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[answer("complete answer", finish_reason="stop")],
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

        result = await agent.run("q")

        assert result.answer == "complete answer"
        assert llm.call_count == 1


class TestStreamAnswerContinuation:
    async def test_synthesis_stream_continues_past_truncation(self) -> None:
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                answer("first half ", finish_reason="length"),
                answer("second half", finish_reason="stop"),
            ],
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
        second_call = llm.all_messages[1]
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
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[answer("whole answer", finish_reason="stop")],
        )
        agent = ReActAgent(
            llm=llm,
            tools=ToolRegistry(),
            enable_plan_tool=False,
        )
        result = AgentResult(answer="", steps=[], iterations=1, messages=[])

        chunks = [c async for c in agent.stream_answer("q", result)]

        assert "".join(chunks) == "whole answer"
        assert llm.call_count == 1


class TestTruncatedToolCallRetry:
    async def test_preamble_is_not_accepted_as_the_answer(self) -> None:
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                # The model narrated its intent, then blew the budget writing
                # the tool call; the shim drops it and reports the truncation.
                truncated_tool_call("I will now write the file."),
                answer("Wrote it in two smaller parts."),
            ],
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

        result = await agent.run("write a big html file")

        # The truncated turn only announced the work — it must not stand in
        # for the work itself.
        assert result.answer == "Wrote it in two smaller parts."
        assert llm.call_count == 2
        assert any(
            m.role == "user"
            and isinstance(m.content, str)
            and m.content.startswith("[Tool call truncated]")
            for m in result.messages
        )
