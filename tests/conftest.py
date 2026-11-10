"""Shared pytest fixtures for fim-one tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .fake_llm import FakeLLM


# ---------------------------------------------------------------------------
# FakeLLM -- the shared scripted LLM, re-exported so tests can keep importing
# it from conftest alongside EchoTool.  Implementation: tests/fake_llm.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EchoTool -- a trivial tool that echoes its input
# ---------------------------------------------------------------------------


class EchoTool(BaseTool):
    """A simple tool that returns its ``text`` argument verbatim."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text back."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo.",
                },
            },
            "required": ["text"],
        }

    async def run(self, **kwargs: Any) -> str:
        return kwargs.get("text", "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm() -> FakeLLM:
    """Return a ``FakeLLM`` with a single default final-answer response."""
    default_response = LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "type": "final_answer",
                    "reasoning": "No further action needed.",
                    "answer": "42",
                }
            ),
        ),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    return FakeLLM(responses=[default_response])


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    """Return a ``ToolRegistry`` pre-loaded with an ``EchoTool``."""
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


@pytest.fixture()
def mock_agent(mock_llm: FakeLLM, tool_registry: ToolRegistry) -> ReActAgent:
    """Return a ``ReActAgent`` wired to the fake LLM and echo tool registry."""
    return ReActAgent(llm=mock_llm, tools=tool_registry, max_iterations=5)


@pytest.fixture(autouse=True)
def _human_intervention_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with the Human Intervention switch at its default (off).

    The switch is read from the environment, so a developer's exported
    ``WORKFLOW_HUMAN_INTERVENTION_ENABLED`` would otherwise decide what the
    workflow tests see.
    """
    monkeypatch.delenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", raising=False)


@pytest.fixture()
def human_intervention_enabled(
    _human_intervention_default: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch Human Intervention workflow nodes on (they are off by default)."""
    monkeypatch.setenv("WORKFLOW_HUMAN_INTERVENTION_ENABLED", "true")


@pytest.fixture(autouse=True)
def _assembly_ports() -> None:
    """Register the assembly layer's ports the way the app does at start-up.

    The real web implementations, so a test that replaces one of their
    ingredients (the usage numbers, the plan check) sees it take effect. A
    test of the unregistered state unregisters the port itself.
    """
    from fim_one.web.services.assembly_ports import register_assembly_ports

    register_assembly_ports()
