"""Tests for the ReAct agent loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_agent.core.agent import AgentResult, ReActAgent
from fim_agent.core.agent.react import ReActAgent as _ReActAgentDirect
from fim_agent.core.agent.types import Action, StepResult
from fim_agent.core.model import ChatMessage, LLMResult
from fim_agent.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helpers
# ======================================================================


def _final_answer_response(answer: str, reasoning: str = "done") -> LLMResult:
    """Create an ``LLMResult`` whose content is a final_answer JSON action."""
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
    """Create an ``LLMResult`` whose content is a tool_call JSON action."""
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
# ReActAgent.run() -- end-to-end async tests
# ======================================================================


class TestReActAgentRun:
    """Integration tests for the full ReAct loop."""

    async def test_immediate_final_answer(self) -> None:
        """LLM returns a final_answer on the first call."""
        llm = FakeLLM(responses=[_final_answer_response("hello world")])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("greet me")

        assert result.answer == "hello world"
        assert result.iterations == 1
        assert len(result.steps) == 1
        assert result.steps[0].action.type == "final_answer"

    async def test_one_tool_call_then_final_answer(self) -> None:
        """LLM does one tool call, observes the result, then answers."""
        llm = FakeLLM(
            responses=[
                _tool_call_response("echo", {"text": "ping"}),
                _final_answer_response("pong"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("echo test")

        assert result.answer == "pong"
        assert result.iterations == 2
        assert len(result.steps) == 2
        # First step is the tool call
        assert result.steps[0].action.type == "tool_call"
        assert result.steps[0].observation == "ping"
        assert result.steps[0].error is None
        # Second step is the final answer
        assert result.steps[1].action.type == "final_answer"

    async def test_multiple_tool_calls(self) -> None:
        """LLM does two tool calls before providing a final answer."""
        llm = FakeLLM(
            responses=[
                _tool_call_response("echo", {"text": "first"}),
                _tool_call_response("echo", {"text": "second"}),
                _final_answer_response("done"),
            ]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("multi-step")

        assert result.answer == "done"
        assert result.iterations == 3
        assert result.steps[0].observation == "first"
        assert result.steps[1].observation == "second"

    async def test_unknown_tool_produces_error(self) -> None:
        """Calling a tool not in the registry produces an error step."""
        llm = FakeLLM(
            responses=[
                _tool_call_response("nonexistent", {}),
                _final_answer_response("fallback"),
            ]
        )
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("try bad tool")

        assert result.answer == "fallback"
        assert result.steps[0].error is not None
        assert "Unknown tool" in result.steps[0].error

    async def test_max_iterations_protection(self) -> None:
        """Agent stops when max_iterations is exceeded."""
        # LLM always returns tool calls, never a final answer
        llm = FakeLLM(
            responses=[_tool_call_response("echo", {"text": "loop"})]
        )
        registry = ToolRegistry()
        registry.register(EchoTool())
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=3)

        result = await agent.run("infinite loop")

        assert result.iterations == 3
        assert "unable to complete" in result.answer.lower()
        assert len(result.steps) == 3

    async def test_custom_system_prompt(self) -> None:
        """A custom system prompt replaces the default template."""
        llm = FakeLLM(responses=[_final_answer_response("custom")])
        registry = ToolRegistry()
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            system_prompt="You are a test bot.",
        )

        result = await agent.run("test")
        assert result.answer == "custom"
        # Verify the LLM was called (no crash from custom prompt)
        assert llm.call_count == 1

    async def test_json_parse_retry_on_malformed_output(self) -> None:
        """When LLM returns non-JSON, agent asks it to re-format and retries."""
        # First response: raw text (not JSON) -> triggers retry
        malformed = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="Here is my analysis report...",
            ),
        )
        # Second response: properly formatted JSON after the retry prompt
        proper = _final_answer_response("Here is my analysis report...")

        llm = FakeLLM(responses=[malformed, proper])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry)

        result = await agent.run("analyze something")

        assert result.answer == "Here is my analysis report..."
        assert result.iterations == 2  # One retry
        assert llm.call_count == 2

    async def test_json_parse_retry_still_fails_fallback(self) -> None:
        """If retry also produces non-JSON, fallback to raw content."""
        malformed1 = LLMResult(
            message=ChatMessage(role="assistant", content="Raw text 1"),
        )
        malformed2 = LLMResult(
            message=ChatMessage(role="assistant", content="Raw text 2"),
        )

        llm = FakeLLM(responses=[malformed1, malformed2])
        registry = ToolRegistry()
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=2)

        result = await agent.run("test")

        # Second malformed response should be used as final answer via fallback
        assert result.answer == "Raw text 2"
        assert "could not parse" in result.steps[-1].action.reasoning.lower()


# ======================================================================
# ReActAgent._parse_action -- unit tests
# ======================================================================


class TestParseAction:
    """Unit tests for the ``_parse_action`` internal method."""

    def _make_agent(self) -> ReActAgent:
        """Create an agent instance to access ``_parse_action``."""
        llm = FakeLLM(responses=[])
        registry = ToolRegistry()
        return ReActAgent(llm=llm, tools=registry)

    def test_valid_final_answer_json(self) -> None:
        agent = self._make_agent()
        content = json.dumps(
            {
                "type": "final_answer",
                "reasoning": "I know the answer",
                "answer": "42",
            }
        )
        action = agent._parse_action(content)
        assert action.type == "final_answer"
        assert action.answer == "42"
        assert action.reasoning == "I know the answer"

    def test_valid_tool_call_json(self) -> None:
        agent = self._make_agent()
        content = json.dumps(
            {
                "type": "tool_call",
                "reasoning": "Need to compute",
                "tool_name": "python_exec",
                "tool_args": {"code": "print(1+1)"},
            }
        )
        action = agent._parse_action(content)
        assert action.type == "tool_call"
        assert action.tool_name == "python_exec"
        assert action.tool_args == {"code": "print(1+1)"}

    def test_malformed_json_fallback_to_final_answer(self) -> None:
        agent = self._make_agent()
        action = agent._parse_action("this is not JSON at all")
        assert action.type == "final_answer"
        assert action.answer == "this is not JSON at all"
        assert "could not parse" in action.reasoning.lower()

    def test_empty_string_fallback(self) -> None:
        agent = self._make_agent()
        action = agent._parse_action("")
        assert action.type == "final_answer"
        assert action.answer == ""

    def test_json_without_type_defaults_to_final_answer(self) -> None:
        agent = self._make_agent()
        content = json.dumps({"reasoning": "hmm", "answer": "guess"})
        action = agent._parse_action(content)
        assert action.type == "final_answer"
        assert action.answer == "guess"

    def test_unknown_type_defaults_to_final_answer(self) -> None:
        agent = self._make_agent()
        content = json.dumps(
            {"type": "unknown_action", "reasoning": "x", "answer": "y"}
        )
        action = agent._parse_action(content)
        assert action.type == "final_answer"
        assert action.answer == "y"

    def test_tool_call_with_empty_tool_args(self) -> None:
        agent = self._make_agent()
        content = json.dumps(
            {
                "type": "tool_call",
                "reasoning": "try it",
                "tool_name": "echo",
                "tool_args": None,
            }
        )
        action = agent._parse_action(content)
        assert action.type == "tool_call"
        assert action.tool_args == {}

    def test_tool_call_missing_tool_args(self) -> None:
        agent = self._make_agent()
        content = json.dumps(
            {
                "type": "tool_call",
                "reasoning": "try it",
                "tool_name": "echo",
            }
        )
        action = agent._parse_action(content)
        assert action.type == "tool_call"
        assert action.tool_args == {}


# ======================================================================
# Action / StepResult / AgentResult dataclasses
# ======================================================================


class TestAgentTypes:
    """Verify agent-layer dataclass creation."""

    def test_action_tool_call(self) -> None:
        action = Action(
            type="tool_call",
            reasoning="need data",
            tool_name="echo",
            tool_args={"text": "hi"},
        )
        assert action.type == "tool_call"
        assert action.tool_name == "echo"
        assert action.answer is None

    def test_action_final_answer(self) -> None:
        action = Action(
            type="final_answer",
            reasoning="done",
            answer="result",
        )
        assert action.type == "final_answer"
        assert action.tool_name is None

    def test_step_result_with_observation(self) -> None:
        action = Action(type="tool_call", reasoning="r", tool_name="t")
        step = StepResult(action=action, observation="output")
        assert step.observation == "output"
        assert step.error is None

    def test_step_result_with_error(self) -> None:
        action = Action(type="tool_call", reasoning="r", tool_name="t")
        step = StepResult(action=action, error="failed")
        assert step.error == "failed"
        assert step.observation is None

    def test_agent_result_defaults(self) -> None:
        result = AgentResult(answer="ok")
        assert result.steps == []
        assert result.iterations == 0
