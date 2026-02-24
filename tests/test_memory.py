"""Tests for conversation memory implementations and ReActAgent integration."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_agent.core.agent import ReActAgent
from fim_agent.core.memory import BaseMemory, SummaryMemory, WindowMemory
from fim_agent.core.model import ChatMessage, LLMResult
from fim_agent.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helpers
# ======================================================================


def _final_answer_response(answer: str, reasoning: str = "done") -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "final_answer",
                    "reasoning": reasoning,
                    "answer": answer,
                }
            ),
        ),
    )


def _tool_call_response(
    tool_name: str,
    tool_args: dict[str, Any],
    reasoning: str = "calling tool",
) -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "tool_call",
                    "reasoning": reasoning,
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                }
            ),
        ),
    )


# ======================================================================
# WindowMemory
# ======================================================================


class TestWindowMemory:
    """Tests for the sliding-window memory implementation."""

    async def test_add_and_get_messages(self) -> None:
        """Messages added via add_message are returned by get_messages."""
        mem = WindowMemory(max_messages=10)
        msg = ChatMessage(role="user", content="hello")
        await mem.add_message(msg)

        result = await mem.get_messages()
        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].content == "hello"

    async def test_window_sliding(self) -> None:
        """Oldest non-system messages are evicted when the window is full."""
        mem = WindowMemory(max_messages=3)

        for i in range(5):
            await mem.add_message(ChatMessage(role="user", content=f"msg-{i}"))

        result = await mem.get_messages()
        assert len(result) == 3
        # Only the last 3 messages should remain.
        assert result[0].content == "msg-2"
        assert result[1].content == "msg-3"
        assert result[2].content == "msg-4"

    async def test_system_message_preserved(self) -> None:
        """System messages are never evicted by the sliding window."""
        mem = WindowMemory(max_messages=2)

        await mem.add_message(ChatMessage(role="system", content="I am the system"))
        for i in range(4):
            await mem.add_message(ChatMessage(role="user", content=f"msg-{i}"))

        result = await mem.get_messages()
        # System message + last 2 non-system messages.
        assert len(result) == 3
        assert result[0].role == "system"
        assert result[0].content == "I am the system"
        assert result[1].content == "msg-2"
        assert result[2].content == "msg-3"

    async def test_multiple_system_messages_preserved(self) -> None:
        """Multiple system messages all survive window trimming."""
        mem = WindowMemory(max_messages=2)

        await mem.add_message(ChatMessage(role="system", content="sys-1"))
        await mem.add_message(ChatMessage(role="system", content="sys-2"))
        for i in range(5):
            await mem.add_message(ChatMessage(role="user", content=f"u-{i}"))

        result = await mem.get_messages()
        system_msgs = [m for m in result if m.role == "system"]
        non_system = [m for m in result if m.role != "system"]

        assert len(system_msgs) == 2
        assert len(non_system) == 2
        assert non_system[0].content == "u-3"
        assert non_system[1].content == "u-4"

    async def test_clear(self) -> None:
        """clear() removes all messages."""
        mem = WindowMemory(max_messages=10)
        await mem.add_message(ChatMessage(role="user", content="hello"))
        await mem.clear()

        result = await mem.get_messages()
        assert result == []

    async def test_get_messages_returns_copy(self) -> None:
        """get_messages returns a copy, not the internal list."""
        mem = WindowMemory(max_messages=10)
        await mem.add_message(ChatMessage(role="user", content="hello"))

        result = await mem.get_messages()
        result.append(ChatMessage(role="user", content="external"))

        internal = await mem.get_messages()
        assert len(internal) == 1

    async def test_mixed_roles_windowing(self) -> None:
        """Window applies to all non-system roles (user and assistant)."""
        mem = WindowMemory(max_messages=3)

        await mem.add_message(ChatMessage(role="user", content="q1"))
        await mem.add_message(ChatMessage(role="assistant", content="a1"))
        await mem.add_message(ChatMessage(role="user", content="q2"))
        await mem.add_message(ChatMessage(role="assistant", content="a2"))
        await mem.add_message(ChatMessage(role="user", content="q3"))

        result = await mem.get_messages()
        assert len(result) == 3
        # q1, a1, q2, a2, q3 -> last 3 = q2, a2, q3
        assert result[0].content == "q2"
        assert result[1].content == "a2"
        assert result[2].content == "q3"


# ======================================================================
# SummaryMemory
# ======================================================================


class TestSummaryMemory:
    """Tests for the LLM-based summarization memory."""

    async def test_no_summarization_below_threshold(self) -> None:
        """Messages below threshold are stored verbatim without summarization."""
        llm = FakeLLM(responses=[])
        mem = SummaryMemory(llm=llm, summary_threshold=10, keep_recent=4)

        for i in range(5):
            await mem.add_message(ChatMessage(role="user", content=f"msg-{i}"))

        result = await mem.get_messages()
        assert len(result) == 5
        assert llm.call_count == 0

    async def test_summarization_triggers_at_threshold(self) -> None:
        """Summarization fires when non-system messages exceed the threshold."""
        summary_response = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="User discussed topics A, B, and C.",
            ),
        )
        llm = FakeLLM(responses=[summary_response])
        mem = SummaryMemory(llm=llm, summary_threshold=5, keep_recent=2)

        for i in range(6):
            await mem.add_message(ChatMessage(role="user", content=f"msg-{i}"))

        assert llm.call_count == 1

        result = await mem.get_messages()
        # Should have: 1 summary message + 2 recent messages = 3 total.
        assert len(result) == 3
        assert result[0].role == "system"
        assert "[Conversation summary]" in result[0].content
        assert "User discussed topics A, B, and C." in result[0].content
        # Recent messages preserved.
        assert result[1].content == "msg-4"
        assert result[2].content == "msg-5"

    async def test_system_messages_preserved_during_summarization(self) -> None:
        """Original system messages survive summarization."""
        summary_response = LLMResult(
            message=ChatMessage(role="assistant", content="Summary here."),
        )
        llm = FakeLLM(responses=[summary_response])
        mem = SummaryMemory(llm=llm, summary_threshold=4, keep_recent=2)

        await mem.add_message(ChatMessage(role="system", content="original-system"))
        for i in range(5):
            await mem.add_message(ChatMessage(role="user", content=f"msg-{i}"))

        result = await mem.get_messages()
        system_msgs = [m for m in result if m.role == "system"]
        assert len(system_msgs) == 2  # original + summary
        assert system_msgs[0].content == "original-system"
        assert "[Conversation summary]" in system_msgs[1].content

    async def test_clear(self) -> None:
        """clear() removes all messages including summaries."""
        llm = FakeLLM(responses=[])
        mem = SummaryMemory(llm=llm, summary_threshold=20)

        await mem.add_message(ChatMessage(role="user", content="hello"))
        await mem.clear()

        result = await mem.get_messages()
        assert result == []

    async def test_get_messages_returns_copy(self) -> None:
        """get_messages returns a copy, not the internal list."""
        llm = FakeLLM(responses=[])
        mem = SummaryMemory(llm=llm, summary_threshold=20)
        await mem.add_message(ChatMessage(role="user", content="hello"))

        result = await mem.get_messages()
        result.append(ChatMessage(role="user", content="external"))

        internal = await mem.get_messages()
        assert len(internal) == 1


# ======================================================================
# ReActAgent + Memory integration
# ======================================================================


class TestReActAgentWithMemory:
    """Integration tests for ReActAgent with conversation memory."""

    async def test_memory_provides_history_to_llm(self) -> None:
        """Previous conversation history is included in the messages sent to the LLM."""
        # Use a capturing LLM so we can inspect the messages it receives.
        captured_messages: list[list[ChatMessage]] = []

        class CapturingLLM(FakeLLM):
            async def chat(self, messages, **kwargs):
                captured_messages.append(list(messages))
                return await super().chat(messages, **kwargs)

        llm = CapturingLLM(responses=[_final_answer_response("second answer")])
        registry = ToolRegistry()

        memory = WindowMemory(max_messages=20)
        # Simulate a previous turn stored in memory.
        await memory.add_message(ChatMessage(role="user", content="first question"))
        await memory.add_message(
            ChatMessage(role="assistant", content="first answer"),
        )

        agent = ReActAgent(llm=llm, tools=registry, memory=memory)
        result = await agent.run("second question")

        assert result.answer == "second answer"

        # Inspect what the LLM received: system + history + new query.
        msgs = captured_messages[0]
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"
        assert msgs[1].content == "first question"
        assert msgs[2].role == "assistant"
        assert msgs[2].content == "first answer"
        assert msgs[3].role == "user"
        assert msgs[3].content == "second question"

    async def test_memory_saves_query_and_final_answer(self) -> None:
        """After run(), the user query and final answer are saved to memory."""
        llm = FakeLLM(responses=[_final_answer_response("the answer")])
        registry = ToolRegistry()
        memory = WindowMemory(max_messages=20)

        agent = ReActAgent(llm=llm, tools=registry, memory=memory)
        await agent.run("what is 2+2?")

        stored = await memory.get_messages()
        assert len(stored) == 2
        assert stored[0].role == "user"
        assert stored[0].content == "what is 2+2?"
        assert stored[1].role == "assistant"
        assert stored[1].content == "the answer"

    async def test_memory_does_not_save_intermediate_steps(self) -> None:
        """Intermediate tool calls and observations are NOT saved to cross-session memory."""
        llm = FakeLLM(
            responses=[
                _tool_call_response("echo", {"text": "intermediate"}),
                _final_answer_response("final result"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        memory = WindowMemory(max_messages=20)

        agent = ReActAgent(llm=llm, tools=registry, memory=memory)
        await agent.run("do something")

        stored = await memory.get_messages()
        # Only 2 messages: the user query and the final answer.
        assert len(stored) == 2
        assert stored[0].role == "user"
        assert stored[0].content == "do something"
        assert stored[1].role == "assistant"
        assert stored[1].content == "final result"
        # Verify no tool call or observation content leaked into memory.
        for msg in stored:
            assert "intermediate" not in (msg.content or "")

    async def test_no_memory_stateless_behavior(self) -> None:
        """Without memory, the agent behaves statelessly (no history)."""
        captured_messages: list[list[ChatMessage]] = []

        class CapturingLLM(FakeLLM):
            async def chat(self, messages, **kwargs):
                captured_messages.append(list(messages))
                return await super().chat(messages, **kwargs)

        llm = CapturingLLM(
            responses=[
                _final_answer_response("a1"),
                _final_answer_response("a2"),
            ]
        )
        registry = ToolRegistry()

        agent = ReActAgent(llm=llm, tools=registry)  # No memory.
        await agent.run("q1")
        await agent.run("q2")

        # Second call should only have system + user, no history from first call.
        msgs = captured_messages[1]
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"
        assert msgs[1].content == "q2"

    async def test_multi_turn_accumulation(self) -> None:
        """Memory accumulates across multiple run() calls."""
        llm = FakeLLM(
            responses=[
                _final_answer_response("answer-1"),
                _final_answer_response("answer-2"),
                _final_answer_response("answer-3"),
            ]
        )
        registry = ToolRegistry()
        memory = WindowMemory(max_messages=20)

        agent = ReActAgent(llm=llm, tools=registry, memory=memory)
        await agent.run("question-1")
        await agent.run("question-2")
        await agent.run("question-3")

        stored = await memory.get_messages()
        # 3 turns x 2 messages each = 6 messages.
        assert len(stored) == 6
        assert stored[0].content == "question-1"
        assert stored[1].content == "answer-1"
        assert stored[2].content == "question-2"
        assert stored[3].content == "answer-2"
        assert stored[4].content == "question-3"
        assert stored[5].content == "answer-3"

    async def test_memory_with_max_iterations_exceeded(self) -> None:
        """Memory is saved even when the agent hits the iteration limit."""
        llm = FakeLLM(
            responses=[_tool_call_response("echo", {"text": "loop"})]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        memory = WindowMemory(max_messages=20)

        agent = ReActAgent(
            llm=llm, tools=registry, max_iterations=2, memory=memory,
        )
        result = await agent.run("infinite loop")

        stored = await memory.get_messages()
        assert len(stored) == 2
        assert stored[0].role == "user"
        assert stored[0].content == "infinite loop"
        assert stored[1].role == "assistant"
        assert "unable to complete" in stored[1].content.lower()

    async def test_system_messages_from_memory_excluded(self) -> None:
        """System messages stored in memory are not duplicated into the prompt."""
        captured_messages: list[list[ChatMessage]] = []

        class CapturingLLM(FakeLLM):
            async def chat(self, messages, **kwargs):
                captured_messages.append(list(messages))
                return await super().chat(messages, **kwargs)

        llm = CapturingLLM(responses=[_final_answer_response("ok")])
        registry = ToolRegistry()

        memory = WindowMemory(max_messages=20)
        # Simulate a system message in memory (e.g. from SummaryMemory).
        await memory.add_message(
            ChatMessage(role="system", content="[summary]: old context"),
        )
        await memory.add_message(ChatMessage(role="user", content="prev-q"))
        await memory.add_message(ChatMessage(role="assistant", content="prev-a"))

        agent = ReActAgent(llm=llm, tools=registry, memory=memory)
        await agent.run("new question")

        msgs = captured_messages[0]
        # Should have: agent's system prompt, then prev-q, prev-a, new question.
        # Memory system messages are filtered out to avoid conflicts.
        system_msgs = [m for m in msgs if m.role == "system"]
        assert len(system_msgs) == 1  # Only the agent's own system prompt.
        assert msgs[1].content == "prev-q"
        assert msgs[2].content == "prev-a"
        assert msgs[3].content == "new question"
