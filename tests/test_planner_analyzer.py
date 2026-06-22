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


def _plan_with_evidence(evidence: str | None) -> ExecutionPlan:
    step = PlanStep(id="step_1", task="list the CVEs fixed in v24.17.0")
    step.status = "completed"
    # Summary under-reports (6) what the evidence actually contained (11).
    step.result = StepOutput(summary="Fixed 6 CVEs.", evidence=evidence)
    return ExecutionPlan(goal="how many CVEs were fixed?", steps=[step])


class TestFormatStepResultsEvidence:
    """Raw tool output must reach the analyzer/synthesis prompt as ground truth.

    Regression: a step's lossy summary was the only thing propagated, so the
    analyzer (which gave 97% confidence) and the synthesis could never catch a
    summary that silently dropped items or mislabelled a severity.
    """

    def test_evidence_is_included(self) -> None:
        plan = _plan_with_evidence(
            evidence="[web_fetch] CVE-2026-48618 High ... CVE-2026-48931 Low (11 total)"
        )
        formatted = PlanAnalyzer._format_step_results(plan)
        assert "Source evidence" in formatted
        assert "CVE-2026-48931 Low" in formatted

    def test_no_evidence_no_section(self) -> None:
        plan = _plan_with_evidence(evidence=None)
        formatted = PlanAnalyzer._format_step_results(plan)
        assert "Source evidence" not in formatted
        assert "Fixed 6 CVEs." in formatted

    def test_evidence_is_truncated(self) -> None:
        plan = _plan_with_evidence(evidence="X" * 50)
        formatted = PlanAnalyzer._format_step_results(plan, max_result_chars=10)
        assert "[Evidence truncated]" in formatted
