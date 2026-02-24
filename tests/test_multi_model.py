"""Tests for multi-model support: ModelRegistry, ModelConfig, and DAGExecutor model selection."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_agent.core.agent import ReActAgent
from fim_agent.core.model import (
    BaseLLM,
    ChatMessage,
    LLMResult,
    ModelConfig,
    ModelRegistry,
    create_registry_from_configs,
)
from fim_agent.core.planner.executor import DAGExecutor
from fim_agent.core.planner.types import ExecutionPlan, PlanStep
from fim_agent.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helper: build a FakeLLM that immediately returns a final answer
# ======================================================================


def _make_fake_llm(answer: str = "42") -> FakeLLM:
    """Create a FakeLLM that returns a single final_answer response."""
    response = LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "final_answer",
                "reasoning": "Done.",
                "answer": answer,
            }),
        ),
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    return FakeLLM(responses=[response])


def _make_tool_registry() -> ToolRegistry:
    """Create a ToolRegistry with an EchoTool."""
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


# ======================================================================
# ModelRegistry
# ======================================================================


class TestModelRegistryRegister:
    """Verify registration and duplicate detection."""

    def test_register_single_model(self) -> None:
        registry = ModelRegistry()
        llm = _make_fake_llm()
        registry.register("alpha", llm)
        assert "alpha" in registry
        assert len(registry) == 1

    def test_register_with_roles(self) -> None:
        registry = ModelRegistry()
        llm = _make_fake_llm()
        registry.register("alpha", llm, roles=["general", "fast"])
        assert registry.get_by_role("general") is llm
        assert registry.get_by_role("fast") is llm

    def test_register_duplicate_name_raises(self) -> None:
        registry = ModelRegistry()
        llm = _make_fake_llm()
        registry.register("alpha", llm)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("alpha", _make_fake_llm())

    def test_register_multiple_models(self) -> None:
        registry = ModelRegistry()
        llm_a = _make_fake_llm("a")
        llm_b = _make_fake_llm("b")
        registry.register("alpha", llm_a, roles=["general"])
        registry.register("beta", llm_b, roles=["fast"])
        assert len(registry) == 2
        assert registry.get("alpha") is llm_a
        assert registry.get("beta") is llm_b


class TestModelRegistryGet:
    """Verify exact name lookup."""

    def test_get_existing(self) -> None:
        registry = ModelRegistry()
        llm = _make_fake_llm()
        registry.register("alpha", llm)
        assert registry.get("alpha") is llm

    def test_get_missing_raises(self) -> None:
        registry = ModelRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nonexistent")


class TestModelRegistryGetByRole:
    """Verify role-based lookup."""

    def test_get_by_role_returns_first_registered(self) -> None:
        registry = ModelRegistry()
        llm_a = _make_fake_llm("a")
        llm_b = _make_fake_llm("b")
        registry.register("alpha", llm_a, roles=["fast"])
        registry.register("beta", llm_b, roles=["fast"])
        # Should return the first one registered for this role.
        assert registry.get_by_role("fast") is llm_a

    def test_get_by_role_missing_raises(self) -> None:
        registry = ModelRegistry()
        registry.register("alpha", _make_fake_llm())
        with pytest.raises(KeyError, match="No model registered for role"):
            registry.get_by_role("vision")


class TestModelRegistryGetDefault:
    """Verify default model resolution."""

    def test_default_prefers_general_role(self) -> None:
        registry = ModelRegistry()
        llm_fast = _make_fake_llm("fast")
        llm_general = _make_fake_llm("general")
        # Register fast first, then general.
        registry.register("fast-model", llm_fast, roles=["fast"])
        registry.register("general-model", llm_general, roles=["general"])
        # Default should be the one with "general" role, not the first.
        assert registry.get_default() is llm_general

    def test_default_falls_back_to_first_registered(self) -> None:
        registry = ModelRegistry()
        llm_first = _make_fake_llm("first")
        llm_second = _make_fake_llm("second")
        registry.register("first", llm_first, roles=["fast"])
        registry.register("second", llm_second, roles=["compact"])
        # No "general" role, so default is the first registered.
        assert registry.get_default() is llm_first

    def test_default_empty_registry_raises(self) -> None:
        registry = ModelRegistry()
        with pytest.raises(RuntimeError, match="empty"):
            registry.get_default()


class TestModelRegistryListModels:
    """Verify model listing."""

    def test_list_empty(self) -> None:
        registry = ModelRegistry()
        assert registry.list_models() == []

    def test_list_preserves_insertion_order(self) -> None:
        registry = ModelRegistry()
        registry.register("charlie", _make_fake_llm())
        registry.register("alpha", _make_fake_llm())
        registry.register("bravo", _make_fake_llm())
        assert registry.list_models() == ["charlie", "alpha", "bravo"]


class TestModelRegistryDunder:
    """Verify __len__, __contains__, __repr__."""

    def test_len(self) -> None:
        registry = ModelRegistry()
        assert len(registry) == 0
        registry.register("a", _make_fake_llm())
        assert len(registry) == 1

    def test_contains(self) -> None:
        registry = ModelRegistry()
        registry.register("a", _make_fake_llm())
        assert "a" in registry
        assert "b" not in registry

    def test_repr(self) -> None:
        registry = ModelRegistry()
        registry.register("alpha", _make_fake_llm())
        registry.register("beta", _make_fake_llm())
        assert "alpha" in repr(registry)
        assert "beta" in repr(registry)


# ======================================================================
# ModelConfig and create_registry_from_configs
# ======================================================================


class TestModelConfig:
    """Verify ModelConfig dataclass defaults."""

    def test_defaults(self) -> None:
        cfg = ModelConfig(name="test", api_key="sk-test")
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.7
        assert cfg.roles == []

    def test_custom_values(self) -> None:
        cfg = ModelConfig(
            name="fast",
            api_key="sk-fast",
            base_url="https://custom.api/v1",
            model="gpt-4o-mini",
            temperature=0.3,
            roles=["fast", "compact"],
        )
        assert cfg.name == "fast"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.roles == ["fast", "compact"]


class TestCreateRegistryFromConfigs:
    """Verify factory function creates registries correctly."""

    def test_creates_registry_with_models(self) -> None:
        configs = [
            ModelConfig(name="main", api_key="sk-1", roles=["general"]),
            ModelConfig(
                name="fast",
                api_key="sk-2",
                model="gpt-4o-mini",
                roles=["fast"],
            ),
        ]
        registry = create_registry_from_configs(configs)
        assert len(registry) == 2
        assert registry.list_models() == ["main", "fast"]
        # Each model should be an OpenAICompatibleLLM.
        from fim_agent.core.model import OpenAICompatibleLLM

        assert isinstance(registry.get("main"), OpenAICompatibleLLM)
        assert isinstance(registry.get("fast"), OpenAICompatibleLLM)

    def test_empty_configs(self) -> None:
        registry = create_registry_from_configs([])
        assert len(registry) == 0

    def test_duplicate_names_raises(self) -> None:
        configs = [
            ModelConfig(name="dup", api_key="sk-1"),
            ModelConfig(name="dup", api_key="sk-2"),
        ]
        with pytest.raises(ValueError, match="already registered"):
            create_registry_from_configs(configs)

    def test_roles_are_propagated(self) -> None:
        configs = [
            ModelConfig(name="v", api_key="sk-1", roles=["vision", "general"]),
        ]
        registry = create_registry_from_configs(configs)
        # Should be reachable by both roles.
        assert registry.get_by_role("vision") is registry.get("v")
        assert registry.get_by_role("general") is registry.get("v")


# ======================================================================
# PlanStep model_hint field
# ======================================================================


class TestPlanStepModelHint:
    """Verify the model_hint field on PlanStep."""

    def test_default_is_none(self) -> None:
        step = PlanStep(id="s1", task="do something")
        assert step.model_hint is None

    def test_can_set_model_hint(self) -> None:
        step = PlanStep(id="s1", task="do something", model_hint="fast")
        assert step.model_hint == "fast"


# ======================================================================
# DAGExecutor with ModelRegistry
# ======================================================================


class TestDAGExecutorModelSelection:
    """Verify DAGExecutor selects per-step agents based on model_hint."""

    async def test_no_registry_uses_default_agent(self) -> None:
        """Without a registry, all steps use the default agent."""
        llm = _make_fake_llm("default-answer")
        tools = _make_tool_registry()
        agent = ReActAgent(llm=llm, tools=tools, max_iterations=5)

        executor = DAGExecutor(agent=agent, max_concurrency=2)
        plan = ExecutionPlan(
            goal="test",
            steps=[PlanStep(id="s1", task="do something", model_hint="fast")],
        )
        result = await executor.execute(plan)
        assert result.steps[0].status == "completed"
        assert result.steps[0].result == "default-answer"

    async def test_registry_with_matching_hint(self) -> None:
        """When a step has model_hint and registry has a matching role,
        the executor should use the role-specific LLM."""
        default_llm = _make_fake_llm("default-answer")
        fast_llm = _make_fake_llm("fast-answer")
        tools = _make_tool_registry()

        default_agent = ReActAgent(llm=default_llm, tools=tools, max_iterations=5)
        registry = ModelRegistry()
        registry.register("default-model", default_llm, roles=["general"])
        registry.register("fast-model", fast_llm, roles=["fast"])

        executor = DAGExecutor(
            agent=default_agent,
            max_concurrency=2,
            model_registry=registry,
        )
        plan = ExecutionPlan(
            goal="test",
            steps=[PlanStep(id="s1", task="quick task", model_hint="fast")],
        )
        result = await executor.execute(plan)
        assert result.steps[0].status == "completed"
        # The fast LLM should have been used.
        assert result.steps[0].result == "fast-answer"
        # Verify the fast LLM was actually called.
        assert fast_llm.call_count == 1
        # Default LLM should not have been called for this step.
        assert default_llm.call_count == 0

    async def test_registry_with_unmatched_hint_falls_back(self) -> None:
        """When model_hint role is not in the registry, fall back to default."""
        default_llm = _make_fake_llm("default-answer")
        tools = _make_tool_registry()
        default_agent = ReActAgent(llm=default_llm, tools=tools, max_iterations=5)

        registry = ModelRegistry()
        registry.register("only-model", _make_fake_llm("other"), roles=["general"])

        executor = DAGExecutor(
            agent=default_agent,
            max_concurrency=2,
            model_registry=registry,
        )
        plan = ExecutionPlan(
            goal="test",
            steps=[
                PlanStep(id="s1", task="vision task", model_hint="vision"),
            ],
        )
        result = await executor.execute(plan)
        assert result.steps[0].status == "completed"
        # Should fall back to default agent's LLM.
        assert result.steps[0].result == "default-answer"
        assert default_llm.call_count == 1

    async def test_mixed_hints_across_steps(self) -> None:
        """Multiple steps with different (or no) model_hints use correct LLMs."""
        default_llm = _make_fake_llm("default-answer")
        fast_llm = _make_fake_llm("fast-answer")
        vision_llm = _make_fake_llm("vision-answer")
        tools = _make_tool_registry()

        default_agent = ReActAgent(llm=default_llm, tools=tools, max_iterations=5)
        registry = ModelRegistry()
        registry.register("default-model", default_llm, roles=["general"])
        registry.register("fast-model", fast_llm, roles=["fast"])
        registry.register("vision-model", vision_llm, roles=["vision"])

        executor = DAGExecutor(
            agent=default_agent,
            max_concurrency=3,
            model_registry=registry,
        )
        plan = ExecutionPlan(
            goal="multi-model test",
            steps=[
                PlanStep(id="s1", task="quick calc", model_hint="fast"),
                PlanStep(id="s2", task="look at image", model_hint="vision"),
                PlanStep(id="s3", task="general task"),  # no hint
            ],
        )
        result = await executor.execute(plan)

        step_results = {s.id: s for s in result.steps}
        assert step_results["s1"].result == "fast-answer"
        assert step_results["s2"].result == "vision-answer"
        assert step_results["s3"].result == "default-answer"

        assert fast_llm.call_count == 1
        assert vision_llm.call_count == 1
        assert default_llm.call_count == 1

    async def test_step_with_none_hint_uses_default(self) -> None:
        """Steps with model_hint=None should use the default agent."""
        default_llm = _make_fake_llm("default-answer")
        tools = _make_tool_registry()
        default_agent = ReActAgent(llm=default_llm, tools=tools, max_iterations=5)

        registry = ModelRegistry()
        registry.register("fast-model", _make_fake_llm("fast"), roles=["fast"])

        executor = DAGExecutor(
            agent=default_agent,
            max_concurrency=2,
            model_registry=registry,
        )
        plan = ExecutionPlan(
            goal="test",
            steps=[PlanStep(id="s1", task="normal task", model_hint=None)],
        )
        result = await executor.execute(plan)
        assert result.steps[0].result == "default-answer"
        assert default_llm.call_count == 1


class TestDAGExecutorResolveAgent:
    """Unit tests for the _resolve_agent helper."""

    def test_no_registry_returns_default_agent(self) -> None:
        llm = _make_fake_llm()
        tools = _make_tool_registry()
        agent = ReActAgent(llm=llm, tools=tools)
        executor = DAGExecutor(agent=agent)

        step = PlanStep(id="s1", task="task", model_hint="fast")
        resolved = executor._resolve_agent(step)
        assert resolved is agent

    def test_no_hint_returns_default_agent(self) -> None:
        llm = _make_fake_llm()
        tools = _make_tool_registry()
        agent = ReActAgent(llm=llm, tools=tools)
        registry = ModelRegistry()
        registry.register("m", _make_fake_llm(), roles=["fast"])
        executor = DAGExecutor(agent=agent, model_registry=registry)

        step = PlanStep(id="s1", task="task")
        resolved = executor._resolve_agent(step)
        assert resolved is agent

    def test_matching_hint_returns_new_agent(self) -> None:
        default_llm = _make_fake_llm()
        fast_llm = _make_fake_llm()
        tools = _make_tool_registry()
        agent = ReActAgent(llm=default_llm, tools=tools, max_iterations=10)

        registry = ModelRegistry()
        registry.register("fast-model", fast_llm, roles=["fast"])
        executor = DAGExecutor(agent=agent, model_registry=registry)

        step = PlanStep(id="s1", task="task", model_hint="fast")
        resolved = executor._resolve_agent(step)
        # Should be a different agent instance.
        assert resolved is not agent
        # But should use the fast LLM.
        assert resolved._llm is fast_llm
        # And share the same tools.
        assert resolved._tools is agent._tools
        # And same max_iterations.
        assert resolved._max_iterations == agent._max_iterations

    def test_unmatched_hint_returns_default_agent(self) -> None:
        llm = _make_fake_llm()
        tools = _make_tool_registry()
        agent = ReActAgent(llm=llm, tools=tools)

        registry = ModelRegistry()
        registry.register("m", _make_fake_llm(), roles=["general"])
        executor = DAGExecutor(agent=agent, model_registry=registry)

        step = PlanStep(id="s1", task="task", model_hint="nonexistent")
        resolved = executor._resolve_agent(step)
        assert resolved is agent
