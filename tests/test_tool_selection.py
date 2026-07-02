"""Tests for two-phase tool selection and compact tool catalog."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import (
    TOOL_SELECTION_THRESHOLD,
    _TOOL_SELECTION_MAX,
)
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helpers
# ======================================================================


class _StubTool(BaseTool):
    """A configurable stub tool for testing."""

    def __init__(self, name: str, description: str = "A stub tool.") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "The input value.",
                },
                "optional_flag": {
                    "type": "boolean",
                    "description": "An optional flag.",
                },
            },
            "required": ["input"],
        }

    async def run(self, **kwargs: Any) -> str:
        return f"stub:{self._name}"


def _make_large_registry(n: int = 15) -> ToolRegistry:
    """Create a registry with *n* stub tools."""
    reg = ToolRegistry()
    for i in range(n):
        reg.register(_StubTool(f"tool_{i}", f"Description for tool {i}."))
    return reg


def _selection_response(tool_names: list[str]) -> LLMResult:
    """Create an LLMResult that returns a tool selection JSON."""
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({"tools": tool_names}),
        ),
    )


def _final_answer_response(answer: str) -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "final_answer",
                    "reasoning": "done",
                    "answer": answer,
                }
            ),
        ),
    )


# ======================================================================
# ToolRegistry.to_compact_catalog
# ======================================================================


class TestCompactCatalog:
    """Tests for ToolRegistry.to_compact_catalog()."""

    def test_empty_registry(self) -> None:
        reg = ToolRegistry()
        assert reg.to_compact_catalog() == ""

    def test_basic_catalog(self) -> None:
        reg = ToolRegistry()
        reg.register(_StubTool("alpha", "Does alpha things."))
        reg.register(_StubTool("beta", "Does beta things."))
        catalog = reg.to_compact_catalog()
        assert "- alpha: Does alpha things." in catalog
        assert "- beta: Does beta things." in catalog

    def test_long_description_truncated(self) -> None:
        long_desc = "A" * 100
        reg = ToolRegistry()
        reg.register(_StubTool("long_tool", long_desc))
        catalog = reg.to_compact_catalog()
        # Should be truncated to 77 chars + "..."
        line = catalog.strip()
        # Extract description part after "- long_tool: "
        desc_part = line.split(": ", 1)[1]
        assert len(desc_part) == 80
        assert desc_part.endswith("...")

    def test_newlines_stripped(self) -> None:
        reg = ToolRegistry()
        reg.register(_StubTool("nl_tool", "Line one.\nLine two."))
        catalog = reg.to_compact_catalog()
        assert "\n" not in catalog.split("- nl_tool: ")[1].split("\n")[0]
        assert "Line one. Line two." in catalog


# ======================================================================
# ToolRegistry.to_openai_tools_compact
# ======================================================================


class TestOpenAIToolsCompact:
    """Tests for ToolRegistry.to_openai_tools_compact()."""

    def test_empty_registry(self) -> None:
        reg = ToolRegistry()
        assert reg.to_openai_tools_compact() == []

    def test_description_truncated(self) -> None:
        long_desc = "B" * 250
        reg = ToolRegistry()
        reg.register(_StubTool("long", long_desc))
        result = reg.to_openai_tools_compact()
        assert len(result) == 1
        desc = result[0]["function"]["description"]
        assert len(desc) == 200
        assert desc.endswith("...")

    def test_non_required_param_description_stripped(self) -> None:
        reg = ToolRegistry()
        reg.register(_StubTool("s", "Short desc."))
        result = reg.to_openai_tools_compact()
        params = result[0]["function"]["parameters"]
        # Required param "input" keeps its description
        assert "description" in params["properties"]["input"]
        # Optional param "optional_flag" loses its description
        assert "description" not in params["properties"]["optional_flag"]

    def test_original_schema_not_mutated(self) -> None:
        tool = _StubTool("s", "Short desc.")
        reg = ToolRegistry()
        reg.register(tool)
        # Call compact version
        reg.to_openai_tools_compact()
        # Original schema should still have descriptions on optional params
        assert "description" in tool.parameters_schema["properties"]["optional_flag"]


# ======================================================================
# ToolRegistry.filter_by_names
# ======================================================================


class TestFilterByNames:
    """Tests for ToolRegistry.filter_by_names()."""

    def test_basic_filter(self) -> None:
        reg = _make_large_registry(5)
        filtered = reg.filter_by_names(["tool_0", "tool_2", "tool_4"])
        assert len(filtered) == 3
        assert "tool_0" in filtered
        assert "tool_2" in filtered
        assert "tool_4" in filtered
        assert "tool_1" not in filtered

    def test_unknown_names_ignored(self) -> None:
        reg = _make_large_registry(3)
        filtered = reg.filter_by_names(["tool_0", "nonexistent", "bogus"])
        assert len(filtered) == 1
        assert "tool_0" in filtered

    def test_empty_names_returns_empty(self) -> None:
        reg = _make_large_registry(3)
        filtered = reg.filter_by_names([])
        assert len(filtered) == 0

    def test_all_names_returns_all(self) -> None:
        reg = _make_large_registry(3)
        filtered = reg.filter_by_names(["tool_0", "tool_1", "tool_2"])
        assert len(filtered) == 3


# ======================================================================
# Tool selection phase in ReActAgent
# ======================================================================


class TestToolSelection:
    """Tests for the two-phase tool selection in ReActAgent.run()."""

    async def test_skip_selection_below_threshold(self) -> None:
        """When tool count <= threshold, selection is skipped entirely."""
        reg = ToolRegistry()
        reg.register(EchoTool())
        # Only 1 tool, well below threshold
        llm = FakeLLM(responses=[_final_answer_response("hello")])
        agent = ReActAgent(llm=llm, tools=reg)

        result = await agent.run("greet me")
        assert result.answer == "hello"
        # Only 1 LLM call (no selection call)
        assert llm.call_count == 1

    async def test_selection_triggered_above_threshold(self) -> None:
        """When tool count > threshold, selection phase runs first."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        # First call: selection phase returns tool_0 and tool_1
        # Second call: final answer
        llm = FakeLLM(
            responses=[
                _selection_response(["tool_0", "tool_1"]),
                _final_answer_response("done"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        result = await agent.run("test query")
        assert result.answer == "done"
        # 2 LLM calls: selection + final answer
        assert llm.call_count == 2

    async def test_selection_fallback_on_bad_json(self) -> None:
        """If selection returns non-JSON, fall back to all tools."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        # structured_llm_call uses 3-level degradation. With FakeLLM
        # (no native FC, no json_mode), the plain_text level fires:
        #   Call 1: first attempt -> "not json" -> extract_json fails
        #   Call 2: reformat-prompt retry -> still garbage -> all levels
        #           exhausted, default_value=None returned -> fallback
        #   Call 3: main ReAct loop -> final answer
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="not json"),
                ),
                LLMResult(
                    message=ChatMessage(role="assistant", content="still not json"),
                ),
                _final_answer_response("fallback"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        result = await agent.run("test")
        assert result.answer == "fallback"
        assert llm.call_count == 3

    async def test_selection_fallback_on_empty_list(self) -> None:
        """If selection returns empty tool list, fall back to all tools."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        llm = FakeLLM(
            responses=[
                _selection_response([]),
                _final_answer_response("ok"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        result = await agent.run("test")
        assert result.answer == "ok"

    async def test_selection_fallback_on_all_bogus_names(self) -> None:
        """If all selected names are invalid, fall back to all tools."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        llm = FakeLLM(
            responses=[
                _selection_response(["nonexistent_tool"]),
                _final_answer_response("ok"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        result = await agent.run("test")
        assert result.answer == "ok"

    async def test_selection_caps_at_max(self) -> None:
        """Selection should cap at _TOOL_SELECTION_MAX tools."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        # Request more than _TOOL_SELECTION_MAX tools
        many_names = [f"tool_{i}" for i in range(_TOOL_SELECTION_MAX + 5)]
        llm = FakeLLM(
            responses=[
                _selection_response(many_names),
                _final_answer_response("done"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        # Capture the effective tools via the system prompt
        result = await agent.run("test")
        assert result.answer == "done"

    async def test_selection_emits_phase_event(self) -> None:
        """The selecting_tools phase event is emitted via on_iteration."""
        reg = _make_large_registry(TOOL_SELECTION_THRESHOLD + 1)

        llm = FakeLLM(
            responses=[
                _selection_response(["tool_0"]),
                _final_answer_response("done"),
            ]
        )
        agent = ReActAgent(llm=llm, tools=reg)

        events: list[tuple] = []

        def on_iteration(iteration, action, obs, err, step_result):
            events.append((iteration, action.tool_name, action.tool_args))

        result = await agent.run("test", on_iteration=on_iteration)
        assert result.answer == "done"

        # First event should be the selecting_tools phase
        assert len(events) >= 1
        assert events[0][1] == "__selecting_tools__"
        # +1 for the auto-registered update_plan tool (request_tools is
        # registered only after selection, so it is not counted here).
        assert events[0][2]["total"] == TOOL_SELECTION_THRESHOLD + 2


# ======================================================================
# Constants sanity checks
# ======================================================================


class TestConstants:
    """Verify module-level constants are sensible."""

    def test_threshold_is_positive(self) -> None:
        assert TOOL_SELECTION_THRESHOLD > 0

    def test_max_is_positive(self) -> None:
        assert _TOOL_SELECTION_MAX > 0

    def test_max_less_than_threshold(self) -> None:
        assert _TOOL_SELECTION_MAX < TOOL_SELECTION_THRESHOLD
