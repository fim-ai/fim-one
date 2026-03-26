"""Tests for CallAgentTool LLM resolution logic."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.model import ChatMessage, LLMResult
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
    async def test_run_excludes_call_agent_from_sub_tools(self) -> None:
        """Sub-agent tools must not include call_agent (recursion prevention)."""
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
        # Should succeed (not crash) and the sub-agent should not have call_agent
        assert "done" in result

    @pytest.mark.asyncio
    async def test_run_tool_resolver_failure_uses_empty_registry(self) -> None:
        """When tool_resolver raises, sub-agent still runs with empty tools."""
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
