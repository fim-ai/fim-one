"""Tests for token usage tracking and aggregation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from fim_agent.core.agent import ReActAgent
from fim_agent.core.model import ChatMessage, LLMResult
from fim_agent.core.model.usage import UsageSummary, UsageTracker
from fim_agent.core.planner.analyzer import PlanAnalyzer
from fim_agent.core.planner.executor import DAGExecutor
from fim_agent.core.planner.planner import DAGPlanner
from fim_agent.core.planner.types import ExecutionPlan, PlanStep
from fim_agent.core.tool import ToolRegistry

from .conftest import EchoTool, FakeLLM


# ======================================================================
# Helpers
# ======================================================================


def _make_usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    """Create a usage dict for an LLMResult."""
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _final_answer_result(
    answer: str,
    usage: dict[str, int] | None = None,
) -> LLMResult:
    """Create an LLMResult with a final_answer JSON payload."""
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "final_answer",
                "reasoning": "done",
                "answer": answer,
            }),
        ),
        usage=usage or {},
    )


def _tool_call_result(
    tool_name: str,
    tool_args: dict[str, Any],
    usage: dict[str, int] | None = None,
) -> LLMResult:
    """Create an LLMResult with a tool_call JSON payload."""
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "tool_call",
                "reasoning": "calling tool",
                "tool_name": tool_name,
                "tool_args": tool_args,
            }),
        ),
        usage=usage or {},
    )


# ======================================================================
# UsageSummary
# ======================================================================


class TestUsageSummary:
    """Tests for the UsageSummary dataclass."""

    def test_defaults(self) -> None:
        summary = UsageSummary()
        assert summary.prompt_tokens == 0
        assert summary.completion_tokens == 0
        assert summary.total_tokens == 0
        assert summary.llm_calls == 0

    def test_addition(self) -> None:
        a = UsageSummary(prompt_tokens=10, completion_tokens=5, total_tokens=15, llm_calls=1)
        b = UsageSummary(prompt_tokens=20, completion_tokens=10, total_tokens=30, llm_calls=2)
        c = a + b
        assert c.prompt_tokens == 30
        assert c.completion_tokens == 15
        assert c.total_tokens == 45
        assert c.llm_calls == 3
        # Originals unchanged
        assert a.prompt_tokens == 10
        assert b.prompt_tokens == 20

    def test_in_place_addition(self) -> None:
        a = UsageSummary(prompt_tokens=10, completion_tokens=5, total_tokens=15, llm_calls=1)
        b = UsageSummary(prompt_tokens=20, completion_tokens=10, total_tokens=30, llm_calls=2)
        a += b
        assert a.prompt_tokens == 30
        assert a.completion_tokens == 15
        assert a.total_tokens == 45
        assert a.llm_calls == 3


# ======================================================================
# UsageTracker
# ======================================================================


class TestUsageTracker:
    """Tests for the UsageTracker class."""

    async def test_empty_tracker(self) -> None:
        tracker = UsageTracker()
        summary = tracker.get_summary()
        assert summary.prompt_tokens == 0
        assert summary.completion_tokens == 0
        assert summary.total_tokens == 0
        assert summary.llm_calls == 0

    async def test_single_record(self) -> None:
        tracker = UsageTracker()
        await tracker.record(_make_usage(100, 50))
        summary = tracker.get_summary()
        assert summary.prompt_tokens == 100
        assert summary.completion_tokens == 50
        assert summary.total_tokens == 150
        assert summary.llm_calls == 1

    async def test_multiple_records(self) -> None:
        tracker = UsageTracker()
        await tracker.record(_make_usage(100, 50))
        await tracker.record(_make_usage(200, 100))
        await tracker.record(_make_usage(50, 25))
        summary = tracker.get_summary()
        assert summary.prompt_tokens == 350
        assert summary.completion_tokens == 175
        assert summary.total_tokens == 525
        assert summary.llm_calls == 3

    async def test_empty_usage_dict_ignored(self) -> None:
        tracker = UsageTracker()
        await tracker.record({})
        summary = tracker.get_summary()
        assert summary.llm_calls == 0

    async def test_partial_usage_dict(self) -> None:
        tracker = UsageTracker()
        await tracker.record({"prompt_tokens": 42})
        summary = tracker.get_summary()
        assert summary.prompt_tokens == 42
        assert summary.completion_tokens == 0
        assert summary.total_tokens == 0
        assert summary.llm_calls == 1

    async def test_reset(self) -> None:
        tracker = UsageTracker()
        await tracker.record(_make_usage(100, 50))
        await tracker.reset()
        summary = tracker.get_summary()
        assert summary.llm_calls == 0
        assert summary.prompt_tokens == 0

    async def test_model_name_recorded(self) -> None:
        tracker = UsageTracker()
        await tracker.record(_make_usage(10, 5), model="gpt-4o")
        # Model is stored internally; summary still works.
        summary = tracker.get_summary()
        assert summary.llm_calls == 1

    async def test_concurrent_records(self) -> None:
        """Verify tracker is safe under concurrent async access."""
        tracker = UsageTracker()

        async def _record_n(n: int) -> None:
            for _ in range(n):
                await tracker.record(_make_usage(10, 5))

        await asyncio.gather(
            _record_n(50),
            _record_n(50),
            _record_n(50),
        )

        summary = tracker.get_summary()
        assert summary.llm_calls == 150
        assert summary.prompt_tokens == 1500
        assert summary.completion_tokens == 750
        assert summary.total_tokens == 2250


# ======================================================================
# ReActAgent usage integration
# ======================================================================


class TestReActAgentUsage:
    """Verify that ReActAgent populates usage in AgentResult."""

    async def test_immediate_final_answer_usage(self) -> None:
        """Single LLM call returns final answer -- usage should reflect 1 call."""
        llm = FakeLLM(responses=[
            _final_answer_result("hello", _make_usage(100, 50)),
        ])
        agent = ReActAgent(llm=llm, tools=ToolRegistry())
        result = await agent.run("greet me")

        assert result.answer == "hello"
        assert result.usage is not None
        assert result.usage.llm_calls == 1
        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 50
        assert result.usage.total_tokens == 150

    async def test_tool_call_then_final_answer_usage(self) -> None:
        """Two LLM calls: tool_call + final_answer."""
        registry = ToolRegistry()
        registry.register(EchoTool())

        llm = FakeLLM(responses=[
            _tool_call_result("echo", {"text": "ping"}, _make_usage(100, 50)),
            _final_answer_result("pong", _make_usage(200, 80)),
        ])
        agent = ReActAgent(llm=llm, tools=registry)
        result = await agent.run("echo test")

        assert result.answer == "pong"
        assert result.usage is not None
        assert result.usage.llm_calls == 2
        assert result.usage.prompt_tokens == 300
        assert result.usage.completion_tokens == 130
        assert result.usage.total_tokens == 430

    async def test_multiple_iterations_usage(self) -> None:
        """Three LLM calls: two tool calls + final answer."""
        registry = ToolRegistry()
        registry.register(EchoTool())

        llm = FakeLLM(responses=[
            _tool_call_result("echo", {"text": "a"}, _make_usage(50, 20)),
            _tool_call_result("echo", {"text": "b"}, _make_usage(60, 25)),
            _final_answer_result("done", _make_usage(70, 30)),
        ])
        agent = ReActAgent(llm=llm, tools=registry)
        result = await agent.run("multi-step")

        assert result.usage is not None
        assert result.usage.llm_calls == 3
        assert result.usage.prompt_tokens == 180
        assert result.usage.completion_tokens == 75
        assert result.usage.total_tokens == 255

    async def test_max_iterations_usage(self) -> None:
        """Usage is tracked even when the agent hits max iterations."""
        registry = ToolRegistry()
        registry.register(EchoTool())

        llm = FakeLLM(responses=[
            _tool_call_result("echo", {"text": "loop"}, _make_usage(30, 10)),
        ])
        agent = ReActAgent(llm=llm, tools=registry, max_iterations=3)
        result = await agent.run("infinite loop")

        assert result.iterations == 3
        assert result.usage is not None
        assert result.usage.llm_calls == 3
        assert result.usage.prompt_tokens == 90
        assert result.usage.total_tokens == 120

    async def test_no_usage_data_from_llm(self) -> None:
        """When LLM returns empty usage, summary should still be present with zeros."""
        llm = FakeLLM(responses=[
            _final_answer_result("hello"),  # No usage dict
        ])
        agent = ReActAgent(llm=llm, tools=ToolRegistry())
        result = await agent.run("test")

        assert result.usage is not None
        assert result.usage.llm_calls == 0
        assert result.usage.prompt_tokens == 0


# ======================================================================
# DAGPlanner usage integration
# ======================================================================


class TestDAGPlannerUsage:
    """Verify that DAGPlanner captures planner LLM call usage."""

    async def test_planner_usage_in_plan(self) -> None:
        plan_json = json.dumps({
            "steps": [
                {"id": "step_1", "task": "Do something", "dependencies": []},
            ]
        })
        llm = FakeLLM(responses=[
            LLMResult(
                message=ChatMessage(role="assistant", content=plan_json),
                usage=_make_usage(200, 100),
            ),
        ])
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("test goal")

        assert plan.total_usage is not None
        assert plan.total_usage.llm_calls == 1
        assert plan.total_usage.prompt_tokens == 200
        assert plan.total_usage.completion_tokens == 100
        assert plan.total_usage.total_tokens == 300

    async def test_planner_no_usage(self) -> None:
        plan_json = json.dumps({
            "steps": [
                {"id": "step_1", "task": "Do something", "dependencies": []},
            ]
        })
        llm = FakeLLM(responses=[
            LLMResult(
                message=ChatMessage(role="assistant", content=plan_json),
                usage={},
            ),
        ])
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("test goal")

        assert plan.total_usage is None


# ======================================================================
# DAGExecutor usage integration
# ======================================================================


class TestDAGExecutorUsage:
    """Verify that DAGExecutor aggregates usage across steps."""

    async def test_executor_aggregates_step_usage(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())

        # Agent will run twice (once per step), each returning final answer.
        llm = FakeLLM(responses=[
            _final_answer_result("result1", _make_usage(100, 50)),
            _final_answer_result("result2", _make_usage(200, 80)),
        ])
        agent = ReActAgent(llm=llm, tools=registry)
        executor = DAGExecutor(agent=agent, max_concurrency=1)

        plan = ExecutionPlan(
            goal="test",
            steps=[
                PlanStep(id="step_1", task="Task 1", dependencies=[]),
                PlanStep(id="step_2", task="Task 2", dependencies=["step_1"]),
            ],
        )

        result_plan = await executor.execute(plan)

        # Per-step usage
        assert result_plan.steps[0].usage is not None
        assert result_plan.steps[0].usage.llm_calls == 1
        assert result_plan.steps[0].usage.prompt_tokens == 100

        assert result_plan.steps[1].usage is not None
        assert result_plan.steps[1].usage.llm_calls == 1
        assert result_plan.steps[1].usage.prompt_tokens == 200

        # Aggregate usage
        assert result_plan.total_usage is not None
        assert result_plan.total_usage.llm_calls == 2
        assert result_plan.total_usage.prompt_tokens == 300
        assert result_plan.total_usage.completion_tokens == 130
        assert result_plan.total_usage.total_tokens == 430

    async def test_executor_merges_with_planner_usage(self) -> None:
        """When plan already has total_usage from the planner, executor adds to it."""
        registry = ToolRegistry()

        llm = FakeLLM(responses=[
            _final_answer_result("done", _make_usage(50, 20)),
        ])
        agent = ReActAgent(llm=llm, tools=registry)
        executor = DAGExecutor(agent=agent)

        plan = ExecutionPlan(
            goal="test",
            steps=[
                PlanStep(id="step_1", task="Task 1", dependencies=[]),
            ],
            # Simulating usage already set by DAGPlanner
            total_usage=UsageSummary(
                prompt_tokens=200, completion_tokens=100, total_tokens=300, llm_calls=1,
            ),
        )

        result_plan = await executor.execute(plan)

        # Should be planner (1 call) + executor step (1 call)
        assert result_plan.total_usage is not None
        assert result_plan.total_usage.llm_calls == 2
        assert result_plan.total_usage.prompt_tokens == 250
        assert result_plan.total_usage.completion_tokens == 120
        assert result_plan.total_usage.total_tokens == 370


# ======================================================================
# PlanAnalyzer usage integration
# ======================================================================


class TestPlanAnalyzerUsage:
    """Verify that PlanAnalyzer captures its LLM call usage."""

    async def test_analyzer_usage(self) -> None:
        analysis_json = json.dumps({
            "achieved": True,
            "confidence": 0.95,
            "final_answer": "All done.",
            "reasoning": "Everything succeeded.",
        })
        llm = FakeLLM(responses=[
            LLMResult(
                message=ChatMessage(role="assistant", content=analysis_json),
                usage=_make_usage(300, 150),
            ),
        ])
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(
            goal="test",
            steps=[PlanStep(id="s1", task="t", status="completed", result="ok")],
        )
        analysis = await analyzer.analyze("test", plan)

        assert analysis.achieved is True
        assert analysis.usage is not None
        assert analysis.usage.llm_calls == 1
        assert analysis.usage.prompt_tokens == 300
        assert analysis.usage.completion_tokens == 150
        assert analysis.usage.total_tokens == 450

    async def test_analyzer_no_usage(self) -> None:
        analysis_json = json.dumps({
            "achieved": False,
            "confidence": 0.5,
            "final_answer": None,
            "reasoning": "Partial.",
        })
        llm = FakeLLM(responses=[
            LLMResult(
                message=ChatMessage(role="assistant", content=analysis_json),
                usage={},
            ),
        ])
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(goal="test", steps=[])
        analysis = await analyzer.analyze("test", plan)

        assert analysis.usage is None
