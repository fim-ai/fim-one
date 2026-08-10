"""Tests for typed DAG steps: llm_direct execution path + step_type plumbing.

Covers:
- ``DAGPlanner._dict_to_steps`` parsing/normalizing ``step_type``.
- ``DAGExecutor`` running ``llm_direct`` steps as a single tool-less LLM
  call (no ReAct loop) and populating ``StepOutput.data``.
- ``DAGExecutor`` attaching typed metadata to react-step results.
- Checkpoint round-trip of ``step_type``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.planner import DAGExecutor, DAGPlanner, PlanStep
from fim_one.core.planner.checkpoint import DAGCheckpoint
from fim_one.core.planner.types import ExecutionPlan, StepOutput

from .conftest import FakeLLM


# ======================================================================
# Planner parsing
# ======================================================================


class TestPlannerStepTypeParsing:
    """``_dict_to_steps`` must parse and normalize ``step_type``."""

    def test_default_is_react(self) -> None:
        steps = DAGPlanner._dict_to_steps(
            {"steps": [{"id": "s1", "task": "do it"}]}
        )
        assert steps is not None
        assert steps[0].step_type == "react"

    def test_llm_direct_accepted(self) -> None:
        steps = DAGPlanner._dict_to_steps(
            {"steps": [{"id": "s1", "task": "merge", "step_type": "llm_direct"}]}
        )
        assert steps is not None
        assert steps[0].step_type == "llm_direct"

    def test_unknown_step_type_normalized_to_react(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            steps = DAGPlanner._dict_to_steps(
                {"steps": [{"id": "s1", "task": "x", "step_type": "workflow"}]}
            )
        assert steps is not None
        assert steps[0].step_type == "react"
        assert "unknown step_type" in caplog.text

    def test_llm_direct_with_tool_hint_forced_to_react(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            steps = DAGPlanner._dict_to_steps(
                {
                    "steps": [
                        {
                            "id": "s1",
                            "task": "search",
                            "step_type": "llm_direct",
                            "tool_hint": "web_search",
                        }
                    ]
                }
            )
        assert steps is not None
        assert steps[0].step_type == "react"
        assert steps[0].tool_hint == "web_search"

    def test_planning_prompt_mentions_step_type(self) -> None:
        from fim_one.core.planner.planner import _PLANNING_PROMPT

        assert "llm_direct" in _PLANNING_PROMPT
        assert "GRANULARITY" in _PLANNING_PROMPT


# ======================================================================
# Executor: llm_direct path
# ======================================================================


def _direct_llm(content: str, usage: dict[str, int] | None = None) -> FakeLLM:
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(role="assistant", content=content),
                usage=usage or {},
            )
        ]
    )


class _ExplodingAgent:
    """Fails the test if the executor falls back to the ReAct loop."""

    tools = MagicMock()

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("ReAct loop must not run for llm_direct steps")


class TestExecutorDirectStep:
    """``llm_direct`` steps run as one LLM call, never the agent loop."""

    def _make_executor(self, llm: FakeLLM) -> DAGExecutor:
        ex = DAGExecutor(
            agent=_ExplodingAgent(llm),  # type: ignore[arg-type]
            enable_tool_cache=False,
            enable_citation_verification=False,
        )
        ex._on_progress = None
        return ex

    async def test_direct_step_single_call(self) -> None:
        llm = _direct_llm(
            "| item | count |\n|---|---|\n| a | 1 |",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        ex = self._make_executor(llm)
        step = PlanStep(id="s2", task="merge results", step_type="llm_direct")

        await ex._execute_step(step, context="[s1] (completed) fetch\nResult: a=1")

        assert step.status == "completed"
        assert step.result is not None
        assert "| a | 1 |" in step.result.summary
        assert step.result.data == {"step_type": "llm_direct"}
        assert llm.call_count == 1
        assert step.usage is not None
        assert step.usage.llm_calls == 1
        assert step.usage.total_tokens == 15
        assert step.duration is None or step.duration >= 0

    async def test_direct_step_empty_output_fails(self) -> None:
        ex = self._make_executor(_direct_llm("   "))
        step = PlanStep(id="s2", task="merge", step_type="llm_direct")

        await ex._execute_step(step, context="")

        assert step.status == "failed"
        assert step.result is not None
        assert "empty output" in step.result.summary

    async def test_direct_step_llm_error_fails(self) -> None:
        class _BoomLLM(FakeLLM):
            async def chat(self, *args: Any, **kwargs: Any) -> LLMResult:
                raise RuntimeError("provider down")

        ex = self._make_executor(_BoomLLM())
        step = PlanStep(id="s2", task="merge", step_type="llm_direct")

        await ex._execute_step(step, context="")

        assert step.status == "failed"
        assert step.result is not None
        assert "provider down" in step.result.summary

    async def test_react_step_still_uses_agent(self) -> None:
        """A default step must still hit the agent loop (guard the branch)."""
        ex = self._make_executor(_direct_llm("unused"))
        step = PlanStep(id="s1", task="research")

        await ex._execute_step(step, context="")

        # _ExplodingAgent.run raises AssertionError, which the executor's
        # catch-all converts into a failed step — proving the loop was hit.
        assert step.status == "failed"
        assert step.result is not None
        assert "ReAct loop must not run" in step.result.summary


# ======================================================================
# Executor: react-step typed metadata
# ======================================================================


class TestReactStepMetadata:
    """React steps record iterations + tools_used in ``StepOutput.data``."""

    async def test_react_result_carries_metadata(self) -> None:
        from fim_one.core.agent.types import Action, AgentResult

        class _FakeAgent:
            tools = MagicMock()

            async def run(
                self,
                query: str,
                on_iteration: Any = None,
                on_thinking_delta: Any = None,
            ) -> Any:
                act = Action(
                    type="tool_call",
                    reasoning="",
                    tool_name="web_fetch",
                    tool_args={"url": "https://example.test"},
                )
                if on_iteration is not None:
                    on_iteration(1, act, None, None)
                    on_iteration(1, act, "page content", None)
                return AgentResult(answer="done", iterations=3)

        ex = DAGExecutor(
            agent=_FakeAgent(),  # type: ignore[arg-type]
            enable_tool_cache=False,
            enable_citation_verification=False,
        )
        ex._on_progress = None
        step = PlanStep(id="s1", task="fetch")

        await ex._execute_step(step, context="")

        assert step.status == "completed"
        assert step.result is not None
        assert step.result.data == {
            "step_type": "react",
            "iterations": 3,
            "tools_used": ["web_fetch"],
        }


# ======================================================================
# Checkpoint round-trip
# ======================================================================


class TestCheckpointStepType:
    """``step_type`` survives a checkpoint save/load cycle."""

    def test_round_trip(self, tmp_path: Any) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        steps = [
            PlanStep(
                id="s1",
                task="fetch",
                status="completed",
                result=StepOutput(summary="data"),
            ),
            PlanStep(
                id="s2",
                task="merge",
                step_type="llm_direct",
                status="completed",
                result=StepOutput(summary="merged"),
            ),
        ]
        plan = ExecutionPlan(goal="g", steps=steps)
        cp.save("conv1", "g", plan, round_num=1)

        restored = cp.load_completed_steps("conv1", "g")
        by_id = {s.id: s for s in restored}
        assert by_id["s1"].step_type == "react"
        assert by_id["s2"].step_type == "llm_direct"

    def test_legacy_checkpoint_without_step_type(self, tmp_path: Any) -> None:
        """Old checkpoint files (no step_type key) default to react."""
        import json as _json
        import time as _time

        from fim_one.core.planner.checkpoint import _goal_hash

        payload = {
            "goal_hash": _goal_hash("g"),
            "round": 1,
            "updated_at": _time.time(),
            "steps": [
                {
                    "id": "s1",
                    "task": "old step",
                    "dependencies": [],
                    "tool_hint": None,
                    "model_hint": None,
                    "status": "completed",
                    "summary": "ok",
                }
            ],
        }
        (tmp_path / "conv1.json").write_text(
            _json.dumps(payload), encoding="utf-8"
        )
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        restored = cp.load_completed_steps("conv1", "g")
        assert len(restored) == 1
        assert restored[0].step_type == "react"
