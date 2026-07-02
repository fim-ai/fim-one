"""Tests for incremental DAG replan and step-level checkpoints.

Design: dev/incremental-dag.md.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.planner import DAGExecutor, DAGPlanner, ExecutionPlan, PlanStep, StepOutput
from fim_one.core.planner.checkpoint import DAGCheckpoint

from .conftest import FakeLLM


def _completed_step(step_id: str, task: str, summary: str) -> PlanStep:
    step = PlanStep(id=step_id, task=task)
    step.status = "completed"
    step.result = StepOutput(summary=summary, evidence=f"evidence for {step_id}")
    return step


class _CountingAgent:
    """Fake agent that records which step queries it executed."""

    tools = MagicMock()

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def run(
        self,
        query: str,
        on_iteration: Any = None,
        on_thinking_delta: Any = None,
    ) -> Any:
        from fim_one.core.agent.types import AgentResult

        self.queries.append(query)
        return AgentResult(answer="step done", iterations=1)


class TestExecutorSkipsCompleted:
    async def test_carried_steps_not_reexecuted(self) -> None:
        agent = _CountingAgent()
        executor = DAGExecutor(
            agent=agent,
            enable_tool_cache=False,
            enable_citation_verification=False,
        )
        done = _completed_step("step_1", "research topic", "research findings")
        pending = PlanStep(id="step_2", task="write report", dependencies=["step_1"])
        plan = ExecutionPlan(goal="g", steps=[done, pending])

        result = await executor.execute(plan)

        # Only the pending step ran.
        assert len(agent.queries) == 1
        assert result.steps[0].status == "completed"
        assert result.steps[0].result is not None
        assert result.steps[0].result.summary == "research findings"
        assert result.steps[1].status == "completed"
        # The carried step's result was available as dependency context.
        assert "research findings" in agent.queries[0]

    async def test_all_completed_plan_is_noop(self) -> None:
        agent = _CountingAgent()
        executor = DAGExecutor(
            agent=agent,
            enable_tool_cache=False,
            enable_citation_verification=False,
        )
        plan = ExecutionPlan(
            goal="g",
            steps=[_completed_step("s1", "a", "ra"), _completed_step("s2", "b", "rb")],
        )

        await executor.execute(plan)

        assert agent.queries == []


class TestPlannerCarryover:
    def _fake_planner(self, new_steps: list[dict[str, Any]]) -> DAGPlanner:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content=json.dumps({"steps": new_steps}),
                    ),
                )
            ]
        )
        return DAGPlanner(llm=llm)

    async def test_carryover_prepended_and_deps_valid(self) -> None:
        planner = self._fake_planner(
            [{"id": "s3", "task": "final report", "dependencies": ["s1", "s2"]}],
        )
        carryover = [
            _completed_step("s1", "research A", "result A"),
            _completed_step("s2", "research B", "result B"),
        ]

        plan = await planner.plan("goal", completed_steps=carryover)

        assert [s.id for s in plan.steps] == ["s1", "s2", "s3"]
        assert plan.steps[0].status == "completed"
        assert plan.steps[1].status == "completed"
        assert plan.steps[2].status == "pending"
        # Dependencies on completed ids survive validation (not dangling).
        assert plan.steps[2].dependencies == ["s1", "s2"]

    async def test_colliding_new_ids_renamed_with_deps_rewritten(self) -> None:
        planner = self._fake_planner(
            [
                {"id": "s1", "task": "redo analysis", "dependencies": []},
                {"id": "s2", "task": "report", "dependencies": ["s1"]},
            ],
        )
        carryover = [_completed_step("s1", "research", "result")]

        plan = await planner.plan("goal", completed_steps=carryover)

        ids = [s.id for s in plan.steps]
        assert ids == ["s1", "s1_r2", "s2"]
        # The new step's dependency follows the renamed new step, and the
        # carried step is untouched.
        report = plan.steps[2]
        assert report.dependencies == ["s1_r2"]
        assert plan.steps[0].status == "completed"

    async def test_prompt_lists_completed_steps(self) -> None:
        planner = self._fake_planner([{"id": "s2", "task": "next", "dependencies": []}])
        carryover = [_completed_step("s1", "research topic", "the findings")]

        messages = planner._build_messages(
            "goal", "", None, None, None, completed_steps=carryover,
        )

        user_content = messages[-1].content
        assert isinstance(user_content, str)
        assert "Already-completed steps" in user_content
        assert "[s1] research topic" in user_content
        assert "the findings" in user_content
        assert "Plan ONLY the remaining work" in user_content

    async def test_no_carryover_prompt_unchanged(self) -> None:
        planner = self._fake_planner([{"id": "s1", "task": "t", "dependencies": []}])
        messages = planner._build_messages("goal", "", None, None, None)
        user_content = messages[-1].content
        assert isinstance(user_content, str)
        assert "Already-completed" not in user_content


class TestDAGCheckpoint:
    def _plan(self) -> ExecutionPlan:
        s1 = _completed_step("s1", "research", "findings")
        s2 = PlanStep(id="s2", task="report", dependencies=["s1"])
        s2.status = "failed"
        s3 = PlanStep(id="s3", task="notify", dependencies=["s2"])
        return ExecutionPlan(goal="g", steps=[s1, s2, s3])

    def test_roundtrip_returns_only_completed(self, tmp_path) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        cp.save("conv1", "my goal", self._plan(), round_num=1)

        restored = cp.load_completed_steps("conv1", "my goal")

        assert len(restored) == 1
        step = restored[0]
        assert step.id == "s1"
        assert step.status == "completed"
        assert step.result is not None
        assert step.result.summary == "findings"
        assert step.result.evidence == "evidence for s1"

    def test_goal_mismatch_returns_empty(self, tmp_path) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        cp.save("conv1", "my goal", self._plan(), round_num=1)

        assert cp.load_completed_steps("conv1", "another goal") == []

    def test_stale_checkpoint_ignored(self, tmp_path) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        cp.save("conv1", "my goal", self._plan(), round_num=1)
        # Rewrite the file with an ancient timestamp.
        path = tmp_path / "conv1.json"
        payload = json.loads(path.read_text())
        payload["updated_at"] = time.time() - 90 * 3600
        path.write_text(json.dumps(payload))

        assert cp.load_completed_steps("conv1", "my goal") == []

    def test_clear_removes_checkpoint(self, tmp_path) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        cp.save("conv1", "my goal", self._plan(), round_num=1)
        cp.clear("conv1")

        assert cp.load_completed_steps("conv1", "my goal") == []
        assert not (tmp_path / "conv1.json").exists()

    def test_missing_checkpoint_returns_empty(self, tmp_path) -> None:
        cp = DAGCheckpoint(base_dir=str(tmp_path))
        assert cp.load_completed_steps("nope", "goal") == []
