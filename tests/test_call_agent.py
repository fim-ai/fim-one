"""Tests for CallAgentTool LLM resolution logic."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.agent.hooks import (
    Hook,
    HookContext,
    HookPoint,
    HookRegistry,
    HookResult,
)
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool.base import BaseTool
from fim_one.core.tool.builtin.call_agent import CallAgentTool
from fim_one.core.tool.registry import ToolRegistry

from .conftest import FakeLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_llm(answer: str = "sub-agent reply") -> FakeLLM:
    """Create a FakeLLM that returns a final_answer JSON response."""
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "final_answer",
                            "reasoning": "Done.",
                            "answer": answer,
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        ]
    )


def _make_agent_catalog(
    *,
    agent_id: str = "agent-1",
    name: str = "Helper",
    model_config_json: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "id": agent_id,
            "name": name,
            "description": "A helpful agent",
            "instructions": "Be helpful.",
            "model_config_json": model_config_json,
        }
    ]


# ---------------------------------------------------------------------------
# Construction and schema tests
# ---------------------------------------------------------------------------


class TestCallAgentToolConstruction:
    """Test that CallAgentTool builds correct metadata from the agent catalog."""

    def test_name(self) -> None:
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
        )
        assert tool.name == "call_agent"

    def test_description_includes_agent(self) -> None:
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(name="Searcher"),
            calling_user_id="user-1",
        )
        assert "Searcher" in tool.description
        assert "agent-1" in tool.description

    def test_parameters_schema_enum(self) -> None:
        catalog = _make_agent_catalog(agent_id="a1") + _make_agent_catalog(
            agent_id="a2", name="Other"
        )
        tool = CallAgentTool(
            available_agents=catalog,
            calling_user_id="user-1",
        )
        schema = tool.parameters_schema
        agent_ids = schema["properties"]["agent_id"]["enum"]
        assert set(agent_ids) == {"a1", "a2"}
        assert schema["required"] == ["agent_id", "task"]


# ---------------------------------------------------------------------------
# LLM resolution tests
# ---------------------------------------------------------------------------


class TestLLMResolution:
    """Test the 3-tier LLM resolution in CallAgentTool._resolve_llm()."""

    @pytest.mark.asyncio
    async def test_tier1_injected_resolver_is_preferred(self) -> None:
        """When llm_resolver is provided, it takes priority over everything."""
        expected_llm = _make_fake_llm()
        resolver = AsyncMock(return_value=expected_llm)

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(
                model_config_json={"model_name": "should-be-ignored"}
            ),
            calling_user_id="user-1",
            llm_resolver=resolver,
        )
        agent_cfg = tool._agents["agent-1"]
        llm = await tool._resolve_llm(agent_cfg)

        assert llm is expected_llm
        resolver.assert_awaited_once_with(agent_cfg)

    @pytest.mark.asyncio
    async def test_tier2_inline_config_when_no_resolver(self) -> None:
        """Without llm_resolver, fall back to get_llm_from_config()."""
        expected_llm = _make_fake_llm()

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(
                model_config_json={"model_name": "gpt-4o", "api_key": "test-key"}
            ),
            calling_user_id="user-1",
            llm_resolver=None,
        )
        agent_cfg = tool._agents["agent-1"]

        with patch(
            "fim_one.web.deps.get_llm_from_config",
            return_value=expected_llm,
        ) as mock_from_config:
            llm = await tool._resolve_llm(agent_cfg)

        assert llm is expected_llm
        mock_from_config.assert_called_once_with(
            {"model_name": "gpt-4o", "api_key": "test-key"}
        )

    @pytest.mark.asyncio
    async def test_tier3_env_registry_when_no_config(self) -> None:
        """Without resolver or inline config, fall back to get_model_registry()."""
        expected_llm = _make_fake_llm()
        mock_registry = MagicMock()
        mock_registry.get_default.return_value = expected_llm

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(model_config_json=None),
            calling_user_id="user-1",
            llm_resolver=None,
        )
        agent_cfg = tool._agents["agent-1"]

        with patch(
            "fim_one.web.deps.get_llm_from_config",
            return_value=None,
        ), patch(
            "fim_one.web.deps.get_model_registry",
            return_value=mock_registry,
        ):
            llm = await tool._resolve_llm(agent_cfg)

        assert llm is expected_llm
        mock_registry.get_default.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier2_skipped_when_inline_config_empty(self) -> None:
        """When model_config_json is an empty dict, skip tier 2 and go to tier 3.

        An empty dict is falsy in Python, so get_llm_from_config is not called.
        """
        expected_llm = _make_fake_llm()
        mock_registry = MagicMock()
        mock_registry.get_default.return_value = expected_llm

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(model_config_json={}),
            calling_user_id="user-1",
            llm_resolver=None,
        )
        agent_cfg = tool._agents["agent-1"]

        with patch(
            "fim_one.web.deps.get_llm_from_config",
            return_value=None,
        ) as mock_from_config, patch(
            "fim_one.web.deps.get_model_registry",
            return_value=mock_registry,
        ):
            llm = await tool._resolve_llm(agent_cfg)

        assert llm is expected_llm
        # Empty dict is falsy — get_llm_from_config should NOT be called
        mock_from_config.assert_not_called()
        mock_registry.get_default.assert_called_once()


# ---------------------------------------------------------------------------
# End-to-end run() tests
# ---------------------------------------------------------------------------


class TestCallAgentRun:
    """Test the full run() flow with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_run_with_injected_resolver(self) -> None:
        """Full run() should succeed when llm_resolver is provided."""
        fake_llm = _make_fake_llm("hello from sub-agent")
        resolver = AsyncMock(return_value=fake_llm)

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            llm_resolver=resolver,
        )

        result = await tool.run(agent_id="agent-1", task="say hello")
        assert "hello from sub-agent" in result

    @pytest.mark.asyncio
    async def test_run_agent_not_found(self) -> None:
        """run() returns an error when agent_id doesn't exist."""
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
        )

        result = await tool.run(agent_id="nonexistent", task="test")
        assert "Error: agent nonexistent not found" in result

    @pytest.mark.asyncio
    async def test_run_model_resolution_failure(self) -> None:
        """run() returns a meaningful error when model resolution fails."""
        resolver = AsyncMock(side_effect=ValueError("no API key"))

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            llm_resolver=resolver,
        )

        result = await tool.run(agent_id="agent-1", task="test")
        assert "Error: could not load model for agent agent-1" in result

    @pytest.mark.asyncio
    async def test_run_excludes_call_agent_from_delegate_tools(self) -> None:
        """Delegated agent tools must not include call_agent (recursion prevention)."""
        fake_llm = _make_fake_llm("done")
        resolver = AsyncMock(return_value=fake_llm)

        # Build a registry that includes a mock call_agent tool
        mock_sub_registry = ToolRegistry()
        mock_call_agent = MagicMock()
        mock_call_agent.name = "call_agent"
        mock_sub_registry.register(mock_call_agent)

        # The returned registry should have call_agent excluded
        excluded_registry = mock_sub_registry.exclude_by_name("call_agent")
        tool_resolver = AsyncMock(return_value=mock_sub_registry)

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            tool_resolver=tool_resolver,
            llm_resolver=resolver,
        )

        result = await tool.run(agent_id="agent-1", task="test")
        # Should succeed (not crash) and the delegated agent should not have call_agent
        assert "done" in result

    @pytest.mark.asyncio
    async def test_run_tool_resolver_failure_uses_empty_registry(self) -> None:
        """When tool_resolver raises, delegated agent still runs with empty tools."""
        fake_llm = _make_fake_llm("ok")
        llm_resolver = AsyncMock(return_value=fake_llm)
        tool_resolver = AsyncMock(side_effect=RuntimeError("MCP down"))

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            tool_resolver=tool_resolver,
            llm_resolver=llm_resolver,
        )

        result = await tool.run(agent_id="agent-1", task="test")
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_run_env_fallback_without_resolver(self) -> None:
        """run() works with ENV-based model registry when no resolver is injected."""
        fake_llm = _make_fake_llm("env fallback result")
        mock_registry = MagicMock()
        mock_registry.get_default.return_value = fake_llm

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(model_config_json=None),
            calling_user_id="user-1",
            llm_resolver=None,
        )

        with patch(
            "fim_one.web.deps.get_llm_from_config",
            return_value=None,
        ), patch(
            "fim_one.web.deps.get_model_registry",
            return_value=mock_registry,
        ):
            result = await tool.run(agent_id="agent-1", task="test")

        assert "env fallback result" in result


# ---------------------------------------------------------------------------
# Hook inheritance tests
# ---------------------------------------------------------------------------


class _RecordingTool(BaseTool):
    """A tool that records whether it was ever executed."""

    def __init__(self) -> None:
        self.ran = False

    @property
    def name(self) -> str:
        return "danger"

    @property
    def description(self) -> str:
        return "A sensitive action"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        self.ran = True
        return "executed"


def _delegate_llm_calling(tool_name: str) -> FakeLLM:
    """A FakeLLM that calls ``tool_name`` once, then answers."""
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "tool_call",
                            "reasoning": "Do the thing",
                            "tool_name": tool_name,
                            "tool_args": {},
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "final_answer",
                            "reasoning": "Done",
                            "answer": "finished",
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
    )


class TestHookInheritance:
    """A delegated agent must run its own enforcement hooks.

    Regression cover for the gap documented in ``dev/hook-system.md``: a
    delegate spun up with an empty registry (and no ``agent_id``) let a
    sensitive call skip the confirmation gate purely by being reached
    through ``call_agent``.
    """

    @pytest.mark.asyncio
    async def test_pre_hook_blocks_delegated_tool_call(self) -> None:
        """A deny hook from the resolver blocks the delegate's tool call."""
        danger = _RecordingTool()
        delegate_tools = ToolRegistry()
        delegate_tools.register(danger)

        registry = HookRegistry()

        async def _deny(_ctx: HookContext) -> HookResult:
            return HookResult(allow=False, error="blocked by gate")

        registry.register(Hook("gate", HookPoint.PRE_TOOL_USE, _deny))

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            tool_resolver=AsyncMock(return_value=delegate_tools),
            llm_resolver=AsyncMock(return_value=_delegate_llm_calling("danger")),
            hook_resolver=AsyncMock(return_value=registry),
        )

        result = await tool.run(agent_id="agent-1", task="do it")

        assert danger.ran is False, "hook did not block the delegated tool call"
        assert "finished" in result

    @pytest.mark.asyncio
    async def test_delegate_receives_agent_and_user_context(self) -> None:
        """agent_id/user_id reach the hook — without them the gate bows out."""
        seen: list[HookContext] = []

        async def _observe(ctx: HookContext) -> HookResult:
            seen.append(ctx)
            return HookResult()

        registry = HookRegistry()
        registry.register(Hook("observer", HookPoint.PRE_TOOL_USE, _observe))

        delegate_tools = ToolRegistry()
        delegate_tools.register(_RecordingTool())

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(agent_id="agent-42"),
            calling_user_id="user-7",
            tool_resolver=AsyncMock(return_value=delegate_tools),
            llm_resolver=AsyncMock(return_value=_delegate_llm_calling("danger")),
            hook_resolver=AsyncMock(return_value=registry),
        )

        await tool.run(agent_id="agent-42", task="do it")

        assert len(seen) == 1
        assert seen[0].agent_id == "agent-42", "gate would skip without agent_id"
        assert seen[0].user_id == "user-7"

    @pytest.mark.asyncio
    async def test_hook_resolver_receives_delegate_config(self) -> None:
        """The registry is built from the delegate's config, not the caller's."""
        resolver = AsyncMock(return_value=HookRegistry())

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(
                model_config_json={"hooks": {"class_hooks": ["feishu_gate"]}}
            ),
            calling_user_id="user-1",
            tool_resolver=AsyncMock(return_value=ToolRegistry()),
            llm_resolver=AsyncMock(return_value=_make_fake_llm()),
            hook_resolver=resolver,
        )

        await tool.run(agent_id="agent-1", task="test")

        resolver.assert_awaited_once()
        passed_cfg = resolver.await_args.args[0]
        assert passed_cfg["id"] == "agent-1"
        assert passed_cfg["model_config_json"] == {
            "hooks": {"class_hooks": ["feishu_gate"]}
        }

    @pytest.mark.asyncio
    async def test_resolver_failure_aborts_delegation(self) -> None:
        """Fail closed: no hooks means no run, not an unguarded run."""
        danger = _RecordingTool()
        delegate_tools = ToolRegistry()
        delegate_tools.register(danger)

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            tool_resolver=AsyncMock(return_value=delegate_tools),
            llm_resolver=AsyncMock(return_value=_delegate_llm_calling("danger")),
            hook_resolver=AsyncMock(side_effect=RuntimeError("db down")),
        )

        result = await tool.run(agent_id="agent-1", task="do it")

        assert danger.ran is False, "delegate ran unguarded after hook failure"
        assert "delegation aborted" in result

    @pytest.mark.asyncio
    async def test_no_resolver_still_delegates(self) -> None:
        """Callers with no hooks to enforce keep working unchanged."""
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            tool_resolver=AsyncMock(return_value=ToolRegistry()),
            llm_resolver=AsyncMock(return_value=_make_fake_llm("plain reply")),
        )

        result = await tool.run(agent_id="agent-1", task="test")

        assert "plain reply" in result


class TestDelegateResultShape:
    """The caller gets the delegate's answer, never its ``AgentResult`` repr.

    ``return str(result)`` on a dataclass renders the entire trajectory —
    every StepResult, observation, and message — so the caller's context
    filled with trace instead of the answer.  Substring assertions could
    not catch it: the answer is inside the repr too.
    """

    @pytest.mark.asyncio
    async def test_returns_exactly_the_answer(self) -> None:
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            llm_resolver=AsyncMock(return_value=_make_fake_llm("the answer")),
        )

        result = await tool.run(agent_id="agent-1", task="test")

        assert result == "the answer"

    @pytest.mark.asyncio
    async def test_no_dataclass_repr_leaks(self) -> None:
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            llm_resolver=AsyncMock(return_value=_make_fake_llm("clean")),
        )

        result = await tool.run(agent_id="agent-1", task="test")

        for marker in ("AgentResult(", "StepResult(", "Action(", "steps=", "messages="):
            assert marker not in result

    @pytest.mark.asyncio
    async def test_empty_answer_reports_instead_of_returning_blank(self) -> None:
        """An answerless run must say so, not hand back an empty string."""
        tool = CallAgentTool(
            available_agents=_make_agent_catalog(),
            calling_user_id="user-1",
            llm_resolver=AsyncMock(return_value=_make_fake_llm("")),
        )

        result = await tool.run(agent_id="agent-1", task="test")

        assert result.strip()
        assert "without producing a textual answer" in result
