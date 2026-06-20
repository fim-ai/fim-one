"""Tests for PlanAnalyzer step-result formatting (detail preservation)."""

from __future__ import annotations

from fim_one.core.planner.analyzer import PlanAnalyzer
from fim_one.core.planner.types import (
    ExecutionPlan,
    PlanStep,
    StepOutput,
)


def _plan_with_step(reasoning: str | None) -> ExecutionPlan:
    step = PlanStep(id="step_1", task="find the HHAI link")
    step.status = "completed"
    step.result = StepOutput(summary="No HHAI on the homepage.", reasoning=reasoning)
    return ExecutionPlan(goal="does Tao An relate to HHAI?", steps=[step])


class TestFormatStepResultsReasoning:
    """A clue in a step's thinking must reach the analyzer/synthesis prompt."""

    def test_step_reasoning_is_included(self) -> None:
        plan = _plan_with_step(reasoning="Found HHAI 2026 acceptance on /research")
        formatted = PlanAnalyzer._format_step_results(plan)
        assert "Reasoning:" in formatted
        assert "HHAI 2026 acceptance" in formatted

    def test_no_reasoning_no_section(self) -> None:
        plan = _plan_with_step(reasoning=None)
        formatted = PlanAnalyzer._format_step_results(plan)
        assert "Reasoning:" not in formatted
        assert "No HHAI on the homepage." in formatted
