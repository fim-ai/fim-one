"""Tests for the ``_format_replan_context`` helper in the DAG chat endpoint."""

from __future__ import annotations

from fim_one.core.planner.types import (
    AnalysisResult,
    ExecutionPlan,
    PlanStep,
    StepOutput,
)
from fim_one.web.api.chat import _format_replan_context, _should_stop_replanning


class TestFormatReplanContext:
    """Unit tests for ``_format_replan_context``."""

    def test_format_replan_context_basic(self):
        """Formats plan steps and analysis reasoning into a readable string."""
        plan = ExecutionPlan(
            goal="Summarise the weather",
            steps=[
                PlanStep(
                    id="step_1",
                    task="Fetch weather data",
                    status="completed",
                    result=StepOutput(summary="Temperature: 22C, sunny."),
                ),
                PlanStep(
                    id="step_2",
                    task="Summarise findings",
                    status="failed",
                    result=None,
                ),
            ],
            current_round=1,
        )
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.3,
            reasoning="Step 2 failed so the summary was not produced.",
        )

        text = _format_replan_context([(plan, analysis)])

        # Should mention the round number.
        assert "Round 1" in text

        # Should include the analyzer reasoning.
        assert "Step 2 failed so the summary was not produced." in text

        # Should include step results / status.
        assert "[step_1] status=completed" in text
        assert "Temperature: 22C, sunny." in text
        assert "[step_2] status=failed" in text
        assert "(no output)" in text

        # Should end with the replanning instruction.
        assert "revised plan" in text.lower()

    def test_format_replan_context_truncates_long_results(self):
        """Step results longer than 500 characters are truncated."""
        long_result = "A" * 600
        plan = ExecutionPlan(
            goal="Test truncation",
            steps=[
                PlanStep(
                    id="step_1",
                    task="Produce long output",
                    status="completed",
                    result=StepOutput(summary=long_result),
                ),
            ],
            current_round=2,
        )
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.4,
            reasoning="Output too verbose.",
        )

        text = _format_replan_context([(plan, analysis)])

        # The full 600-char result must NOT appear.
        assert long_result not in text

        # Instead we should see the first 500 chars followed by truncation marker.
        assert long_result[:500] + "... (truncated)" in text

    def test_format_replan_context_short_result_not_truncated(self):
        """Step results at exactly 500 characters are NOT truncated."""
        exact_result = "B" * 500
        plan = ExecutionPlan(
            goal="Boundary test",
            steps=[
                PlanStep(
                    id="step_1",
                    task="Produce boundary output",
                    status="completed",
                    result=StepOutput(summary=exact_result),
                ),
            ],
            current_round=1,
        )
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.2,
            reasoning="Needs more detail.",
        )

        text = _format_replan_context([(plan, analysis)])

        # The 500-char result should appear in full without trailing truncation.
        assert exact_result in text
        # Make sure there is no spurious truncation marker appended right after it.
        idx = text.index(exact_result)
        after = text[idx + len(exact_result) : idx + len(exact_result) + 15]
        assert "truncated" not in after

    def test_format_replan_context_multiple_steps(self):
        """All steps in the plan are included in the output."""
        steps = [
            PlanStep(
                id=f"step_{i}",
                task=f"Task {i}",
                status="completed",
                result=StepOutput(summary=f"Result {i}"),
            )
            for i in range(1, 6)
        ]
        plan = ExecutionPlan(goal="Multi-step", steps=steps, current_round=3)
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.45,
            reasoning="Not all sub-goals met.",
        )

        text = _format_replan_context([(plan, analysis)])

        for i in range(1, 6):
            assert f"[step_{i}]" in text
            assert f"Result {i}" in text
        assert "Round 3" in text

    def test_format_replan_context_multi_round(self):
        """Multiple rounds are formatted with progressive truncation."""
        plan1 = ExecutionPlan(
            goal="Round 1 goal",
            steps=[
                PlanStep(
                    id="step_1",
                    task="First task",
                    status="completed",
                    result=StepOutput(summary="Round 1 result"),
                ),
            ],
            current_round=1,
        )
        analysis1 = AnalysisResult(
            achieved=False,
            confidence=0.3,
            reasoning="Insufficient.",
        )
        plan2 = ExecutionPlan(
            goal="Round 2 goal",
            steps=[
                PlanStep(
                    id="step_1",
                    task="Retry task",
                    status="completed",
                    result=StepOutput(summary="Round 2 result"),
                ),
            ],
            current_round=2,
        )
        analysis2 = AnalysisResult(
            achieved=False,
            confidence=0.5,
            reasoning="Still not enough.",
        )

        text = _format_replan_context([(plan1, analysis1), (plan2, analysis2)])

        assert "Round 1" in text
        assert "Round 2" in text
        assert "Round 1 result" in text
        assert "Round 2 result" in text

    def test_format_replan_context_empty_history(self):
        """Empty round history returns empty string."""
        text = _format_replan_context([])
        assert text == ""


class TestShouldStopReplanning:
    """The gate that decides whether an unachieved goal gets another round."""

    def test_missing_deliverable_is_retried_however_confident(self) -> None:
        """A confident "the HTML card is missing" must NOT end the run."""
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.8,
            unrecoverable=False,
            reasoning="The scores were computed but the HTML card was never written.",
        )

        assert _should_stop_replanning(analysis, 0.8) is False

    def test_unreachable_goal_stops_at_threshold(self) -> None:
        """A confident "this cannot be done" stops burning rounds."""
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.9,
            unrecoverable=True,
            reasoning="The requested API was retired and has no replacement.",
        )

        assert _should_stop_replanning(analysis, 0.8) is True

    def test_unreachable_but_unsure_still_retries(self) -> None:
        """A shaky "probably impossible" verdict is not enough to give up."""
        analysis = AnalysisResult(
            achieved=False,
            confidence=0.4,
            unrecoverable=True,
            reasoning="The endpoint might be gone, but the errors were ambiguous.",
        )

        assert _should_stop_replanning(analysis, 0.8) is False
