"""Tests for the Planner layer (DAGPlanner, DAGExecutor, PlanAnalyzer)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.planner import (
    AnalysisResult,
    DAGExecutor,
    DAGPlanner,
    ExecutionPlan,
    PlanAnalyzer,
    PlanStep,
    StepOutput,
)

from .conftest import FakeLLM


# ======================================================================
# PlanStep / ExecutionPlan / AnalysisResult dataclass creation
# ======================================================================


class TestPlannerTypes:
    """Verify planner-layer dataclass creation and defaults."""

    def test_plan_step_defaults(self) -> None:
        step = PlanStep(id="s1", task="do something")
        assert step.dependencies == []
        assert step.tool_hint is None
        assert step.result is None
        assert step.status == "pending"

    def test_plan_step_with_dependencies(self) -> None:
        step = PlanStep(
            id="s2",
            task="combine results",
            dependencies=["s1"],
            tool_hint="python_exec",
        )
        assert step.dependencies == ["s1"]
        assert step.tool_hint == "python_exec"

    def test_execution_plan_defaults(self) -> None:
        plan = ExecutionPlan(goal="test goal")
        assert plan.steps == []
        assert plan.current_round == 1

    def test_execution_plan_with_steps(self) -> None:
        steps = [
            PlanStep(id="s1", task="first"),
            PlanStep(id="s2", task="second", dependencies=["s1"]),
        ]
        plan = ExecutionPlan(goal="goal", steps=steps, current_round=2)
        assert len(plan.steps) == 2
        assert plan.current_round == 2

    def test_analysis_result_defaults(self) -> None:
        result = AnalysisResult(achieved=False, confidence=0.0)
        assert result.final_answer is None
        assert result.reasoning == ""

    def test_analysis_result_achieved(self) -> None:
        result = AnalysisResult(
            achieved=True,
            confidence=0.95,
            final_answer="The answer is 42.",
            reasoning="All steps completed successfully.",
        )
        assert result.achieved is True
        assert result.confidence == 0.95
        assert result.final_answer == "The answer is 42."

    def test_plan_step_status_transitions(self) -> None:
        step = PlanStep(id="s1", task="work")
        assert step.status == "pending"
        step.status = "running"
        assert step.status == "running"
        step.status = "completed"
        step.result = StepOutput(summary="done")
        assert step.status == "completed"
        assert step.result.summary == "done"


# ======================================================================
# StepOutput
# ======================================================================


class TestStepOutput:
    """Verify StepOutput dataclass behaviour."""

    def test_str_returns_summary(self) -> None:
        out = StepOutput(summary="The answer is 42.")
        assert str(out) == "The answer is 42."

    def test_bool_empty_summary_is_false(self) -> None:
        out = StepOutput(summary="")
        assert bool(out) is False

    def test_bool_nonempty_summary_is_true(self) -> None:
        out = StepOutput(summary="hello")
        assert bool(out) is True

    def test_data_and_artifacts(self) -> None:
        from fim_one.core.tool.base import Artifact

        artifact = Artifact(
            name="report.pdf",
            path="/uploads/report.pdf",
            mime_type="application/pdf",
            size=1024,
        )
        out = StepOutput(
            summary="Generated report",
            data={"pages": 5},
            artifacts=[artifact],
        )
        assert out.data == {"pages": 5}
        assert len(out.artifacts) == 1
        assert out.artifacts[0].name == "report.pdf"

    def test_defaults(self) -> None:
        out = StepOutput(summary="test")
        assert out.data is None
        assert out.artifacts == []


# ======================================================================
# DAGPlanner._validate_dag
# ======================================================================


class TestDAGPlannerValidation:
    """Tests for DAG validation logic (cycles and dangling references)."""

    def _make_planner(self) -> DAGPlanner:
        """Create a DAGPlanner with a dummy LLM (validation is sync)."""
        llm = FakeLLM(responses=[])
        return DAGPlanner(llm=llm)

    def test_valid_linear_dag(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="first"),
            PlanStep(id="s2", task="second", dependencies=["s1"]),
            PlanStep(id="s3", task="third", dependencies=["s2"]),
        ]
        # Should not raise
        planner._validate_dag(steps)

    def test_valid_parallel_dag(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="a"),
            PlanStep(id="s2", task="b"),
            PlanStep(id="s3", task="merge", dependencies=["s1", "s2"]),
        ]
        # Should not raise
        planner._validate_dag(steps)

    def test_valid_diamond_dag(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="root"),
            PlanStep(id="s2", task="left", dependencies=["s1"]),
            PlanStep(id="s3", task="right", dependencies=["s1"]),
            PlanStep(id="s4", task="join", dependencies=["s2", "s3"]),
        ]
        # Should not raise
        planner._validate_dag(steps)

    def test_valid_no_dependencies(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="independent a"),
            PlanStep(id="s2", task="independent b"),
        ]
        # Should not raise
        planner._validate_dag(steps)

    def test_valid_empty_steps(self) -> None:
        planner = self._make_planner()
        # Should not raise
        planner._validate_dag([])

    def test_cycle_two_nodes(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="a", dependencies=["s2"]),
            PlanStep(id="s2", task="b", dependencies=["s1"]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            planner._validate_dag(steps)

    def test_cycle_three_nodes(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="a", dependencies=["s3"]),
            PlanStep(id="s2", task="b", dependencies=["s1"]),
            PlanStep(id="s3", task="c", dependencies=["s2"]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            planner._validate_dag(steps)

    def test_self_dependency_cycle(self) -> None:
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="self-ref", dependencies=["s1"]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            planner._validate_dag(steps)

    def test_dangling_dependency(self, caplog: pytest.LogCaptureFixture) -> None:
        """Dangling dependency is auto-removed with a warning (not raised)."""
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="a"),
            PlanStep(id="s2", task="b", dependencies=["s999"]),
        ]
        with caplog.at_level(logging.WARNING, logger="fim_one.core.planner.planner"):
            planner._validate_dag(steps)  # should NOT raise
        # Dangling dep removed
        assert steps[1].dependencies == []
        # Warning logged
        assert any("s999" in record.message for record in caplog.records)

    def test_dangling_among_valid(self, caplog: pytest.LogCaptureFixture) -> None:
        """Dangling reference mixed with valid dependencies is auto-removed."""
        planner = self._make_planner()
        steps = [
            PlanStep(id="s1", task="a"),
            PlanStep(id="s2", task="b", dependencies=["s1", "ghost"]),
        ]
        with caplog.at_level(logging.WARNING, logger="fim_one.core.planner.planner"):
            planner._validate_dag(steps)  # should NOT raise
        # Only the valid dep remains
        assert steps[1].dependencies == ["s1"]
        # Warning logged about the dangling ref
        assert any("ghost" in record.message for record in caplog.records)


# ======================================================================
# DAGPlanner.plan() -- end-to-end async
# ======================================================================


class TestDAGPlannerPlan:
    """Test the full ``plan()`` method with a fake LLM."""

    async def test_plan_parses_llm_response(self) -> None:
        plan_json = json.dumps(
            {
                "steps": [
                    {
                        "id": "s1",
                        "task": "research",
                        "dependencies": [],
                        "tool_hint": None,
                    },
                    {
                        "id": "s2",
                        "task": "summarise",
                        "dependencies": ["s1"],
                        "tool_hint": "python_exec",
                    },
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=plan_json),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("Do research and summarise")

        assert plan.goal == "Do research and summarise"
        assert len(plan.steps) == 2
        assert plan.steps[0].id == "s1"
        assert plan.steps[1].dependencies == ["s1"]
        assert plan.steps[1].tool_hint == "python_exec"

    async def test_plan_rejects_invalid_json(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="not json"),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        with pytest.raises(ValueError, match="extraction levels failed"):
            await planner.plan("goal")

    async def test_plan_rejects_missing_steps_key(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content='{"data": []}'),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        with pytest.raises(ValueError, match="extraction levels failed"):
            await planner.plan("goal")

    async def test_plan_rejects_cyclic_plan(self) -> None:
        plan_json = json.dumps(
            {
                "steps": [
                    {"id": "s1", "task": "a", "dependencies": ["s2"]},
                    {"id": "s2", "task": "b", "dependencies": ["s1"]},
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=plan_json),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        with pytest.raises(ValueError, match="Circular dependency"):
            await planner.plan("goal")


# ======================================================================
# DAGExecutor._build_step_context
# ======================================================================


class TestDAGExecutorBuildStepContext:
    """Tests for the static ``_build_step_context`` helper."""

    def test_no_dependencies(self) -> None:
        step = PlanStep(id="s1", task="root task")
        step_index = {"s1": step}
        ctx = DAGExecutor._build_step_context(step, step_index)
        assert ctx == ""

    def test_single_dependency(self) -> None:
        dep = PlanStep(
            id="s1", task="fetch data", status="completed", result=StepOutput(summary="data here")
        )
        step = PlanStep(id="s2", task="process", dependencies=["s1"])
        step_index = {"s1": dep, "s2": step}

        ctx = DAGExecutor._build_step_context(step, step_index)
        assert "s1" in ctx
        assert "completed" in ctx
        assert "data here" in ctx
        assert "fetch data" in ctx

    def test_multiple_dependencies(self) -> None:
        dep1 = PlanStep(id="s1", task="task A", status="completed", result=StepOutput(summary="res A"))
        dep2 = PlanStep(id="s2", task="task B", status="completed", result=StepOutput(summary="res B"))
        step = PlanStep(id="s3", task="combine", dependencies=["s1", "s2"])
        step_index = {"s1": dep1, "s2": dep2, "s3": step}

        ctx = DAGExecutor._build_step_context(step, step_index)
        assert "res A" in ctx
        assert "res B" in ctx

    def test_dependency_with_no_result(self) -> None:
        dep = PlanStep(id="s1", task="failed task", status="failed")
        step = PlanStep(id="s2", task="next", dependencies=["s1"])
        step_index = {"s1": dep, "s2": step}

        ctx = DAGExecutor._build_step_context(step, step_index)
        assert "(no result)" in ctx
        assert "failed" in ctx

    def test_missing_dependency_in_index(self) -> None:
        """A dependency not found in step_index is silently skipped."""
        step = PlanStep(id="s2", task="orphan", dependencies=["s_missing"])
        step_index = {"s2": step}

        ctx = DAGExecutor._build_step_context(step, step_index)
        # Should not crash, and since the dep is missing, nothing is added
        assert ctx == ""


# ======================================================================
# DAGExecutor._build_step_query
# ======================================================================


class TestDAGExecutorBuildStepQuery:
    """Tests for the ``_build_step_query`` instance method."""

    def _make_executor(self, **kwargs: Any) -> DAGExecutor:
        from unittest.mock import MagicMock

        return DAGExecutor(agent=MagicMock(), **kwargs)

    def test_basic_query(self) -> None:
        step = PlanStep(id="s1", task="compute sum")
        query = self._make_executor()._build_step_query(step, context="")
        assert "compute sum" in query

    def test_query_with_tool_hint(self) -> None:
        step = PlanStep(id="s1", task="run code", tool_hint="python_exec")
        query = self._make_executor()._build_step_query(step, context="")
        assert "python_exec" in query
        assert "Suggested tool" in query

    def test_query_with_context(self) -> None:
        step = PlanStep(id="s1", task="summarise")
        ctx = "[s0] (completed) fetch data\nResult: some data"
        query = self._make_executor()._build_step_query(step, context=ctx)
        assert "some data" in query
        assert "Context from previous steps" in query

    def test_query_no_tool_hint_no_context(self) -> None:
        step = PlanStep(id="s1", task="simple task")
        query = self._make_executor()._build_step_query(step, context="")
        assert "Suggested tool" not in query
        assert "Context" not in query


# ======================================================================
# PlanAnalyzer
# ======================================================================


class TestPlanAnalyzer:
    """Tests for ``PlanAnalyzer.analyze()`` with a fake LLM."""

    async def test_analyze_achieved(self) -> None:
        analysis_json = json.dumps(
            {
                "achieved": True,
                "confidence": 0.95,
                "final_answer": "The capital of France is Paris.",
                "reasoning": "Step results confirm the answer.",
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=analysis_json),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)

        steps = [
            PlanStep(
                id="s1",
                task="Look up capital of France",
                status="completed",
                result=StepOutput(summary="Paris"),
            ),
        ]
        plan = ExecutionPlan(goal="What is the capital of France?", steps=steps)

        result = await analyzer.analyze("What is the capital of France?", plan)

        assert result.achieved is True
        assert result.confidence == 0.95
        assert result.final_answer == "The capital of France is Paris."
        assert "confirm" in result.reasoning.lower()

    async def test_analyze_not_achieved(self) -> None:
        analysis_json = json.dumps(
            {
                "achieved": False,
                "confidence": 0.3,
                "final_answer": None,
                "reasoning": "The step failed to produce useful results.",
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=analysis_json),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)

        steps = [
            PlanStep(
                id="s1", task="research", status="failed", result=StepOutput(summary="Error occurred")
            ),
        ]
        plan = ExecutionPlan(goal="goal", steps=steps)

        result = await analyzer.analyze("goal", plan)

        assert result.achieved is False
        assert result.confidence == 0.3
        assert result.final_answer is None

    async def test_analyze_malformed_json(self) -> None:
        """Non-JSON response should be handled gracefully."""
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="not json"),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(goal="goal", steps=[])

        result = await analyzer.analyze("goal", plan)

        assert result.achieved is False
        assert result.confidence == 0.0
        assert "Could not parse" in result.reasoning

    async def test_analyze_confidence_clamped(self) -> None:
        """Confidence values outside [0, 1] should be clamped."""
        analysis_json = json.dumps(
            {
                "achieved": True,
                "confidence": 5.0,
                "final_answer": "ok",
                "reasoning": "very confident",
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=analysis_json),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(goal="goal", steps=[])

        result = await analyzer.analyze("goal", plan)

        assert result.confidence == 1.0

    async def test_analyze_negative_confidence_clamped(self) -> None:
        analysis_json = json.dumps(
            {
                "achieved": False,
                "confidence": -0.5,
                "final_answer": None,
                "reasoning": "unsure",
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=analysis_json),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(goal="goal", steps=[])

        result = await analyzer.analyze("goal", plan)

        assert result.confidence == 0.0

    async def test_analyze_empty_plan(self) -> None:
        """Analyzing a plan with no steps should still work."""
        analysis_json = json.dumps(
            {
                "achieved": False,
                "confidence": 0.1,
                "final_answer": None,
                "reasoning": "No steps were executed.",
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=analysis_json),
                )
            ]
        )
        analyzer = PlanAnalyzer(llm=llm)
        plan = ExecutionPlan(goal="goal", steps=[])

        result = await analyzer.analyze("goal", plan)

        assert result.achieved is False


# ======================================================================
# _regex_extract_analysis — fallback regex extraction
# ======================================================================


class TestRegexExtractAnalysis:
    """Tests for the regex fallback used when JSON parsing fails."""

    def test_reasoning_with_unescaped_quotes(self) -> None:
        """Reasoning containing unescaped quotes should not be truncated."""
        from fim_one.core.planner.analyzer import _regex_extract_analysis

        content = (
            '{"achieved": false, "confidence": 0.35, '
            '"final_answer": null, '
            '"reasoning": "Facebook data shows \\"React\\" has 226k stars but '
            'the API returned incorrect results like \\"fbnic_qemu\\" which '
            'indicates a sorting issue"}'
        )
        data = _regex_extract_analysis(content)
        assert data is not None
        assert "sorting issue" in data["reasoning"]

    def test_reasoning_not_truncated_at_inner_quotes(self) -> None:
        """Reasoning with Chinese-style quoting should be fully extracted."""
        from fim_one.core.planner.analyzer import _regex_extract_analysis

        # Simulate the exact pattern from the user's screenshot:
        # reasoning ends with 得出"Google 远超..."的结论 — old regex would
        # stop at the quote before "Google".
        content = (
            '{"achieved": false, "confidence": 0.35, '
            '"reasoning": "step_compare 采用了错误的数据，'
            '得出\\"Google 远超其他两家\\"的错误结论。需要修正排序参数"}'
        )
        data = _regex_extract_analysis(content)
        assert data is not None
        assert "需要修正排序参数" in data["reasoning"]

    def test_reasoning_last_field(self) -> None:
        """Reasoning as the last field in JSON should be fully extracted."""
        from fim_one.core.planner.analyzer import _regex_extract_analysis

        content = '{"achieved": true, "confidence": 0.9, "reasoning": "All steps completed successfully"}'
        data = _regex_extract_analysis(content)
        assert data is not None
        assert data["reasoning"] == "All steps completed successfully"

    def test_reasoning_before_final_answer(self) -> None:
        """Reasoning followed by final_answer should extract correctly."""
        from fim_one.core.planner.analyzer import _regex_extract_analysis

        content = (
            '{"achieved": true, "confidence": 0.8, '
            '"reasoning": "The plan worked well", '
            '"final_answer": "Here is the result"}'
        )
        data = _regex_extract_analysis(content)
        assert data is not None
        assert data["reasoning"] == "The plan worked well"
        assert data["final_answer"] == "Here is the result"


# ======================================================================
# PlanAnalyzer._format_step_results
# ======================================================================


class TestPlanAnalyzerFormatStepResults:
    """Tests for the static ``_format_step_results`` helper."""

    def test_empty_plan(self) -> None:
        plan = ExecutionPlan(goal="g", steps=[])
        result = PlanAnalyzer._format_step_results(plan)
        assert result == "(no steps in plan)"

    def test_single_completed_step(self) -> None:
        step = PlanStep(id="s1", task="do X", status="completed", result=StepOutput(summary="done X"))
        plan = ExecutionPlan(goal="g", steps=[step])
        result = PlanAnalyzer._format_step_results(plan)
        assert "s1" in result
        assert "completed" in result
        assert "done X" in result
        assert "do X" in result

    def test_step_with_dependencies(self) -> None:
        step = PlanStep(
            id="s2",
            task="combine",
            dependencies=["s1"],
            status="pending",
        )
        plan = ExecutionPlan(goal="g", steps=[step])
        result = PlanAnalyzer._format_step_results(plan)
        assert "s1" in result  # dependency listed
        assert "pending" in result

    def test_step_with_no_result(self) -> None:
        step = PlanStep(id="s1", task="pending task", status="pending")
        plan = ExecutionPlan(goal="g", steps=[step])
        result = PlanAnalyzer._format_step_results(plan)
        assert "(no result)" in result


# ======================================================================
# model_hint parsing and validation
# ======================================================================


class TestModelHintParsing:
    """Tests for model_hint parsing in _dict_to_steps and schema validation."""

    async def test_parse_reasoning_model_hint(self) -> None:
        """A step with model_hint='reasoning' should be parsed correctly."""
        plan_json = json.dumps(
            {
                "steps": [
                    {
                        "id": "s1",
                        "task": "deep analysis",
                        "dependencies": [],
                        "tool_hint": None,
                        "model_hint": "reasoning",
                    },
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=plan_json),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("Analyze something deeply")

        assert len(plan.steps) == 1
        assert plan.steps[0].model_hint == "reasoning"

    async def test_parse_fast_model_hint(self) -> None:
        """A step with model_hint='fast' should be parsed correctly."""
        plan_json = json.dumps(
            {
                "steps": [
                    {
                        "id": "s1",
                        "task": "quick lookup",
                        "dependencies": [],
                        "model_hint": "fast",
                    },
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=plan_json),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("Quick task")

        assert plan.steps[0].model_hint == "fast"

    async def test_parse_null_model_hint(self) -> None:
        """A step with model_hint=null should be parsed as None."""
        plan_json = json.dumps(
            {
                "steps": [
                    {
                        "id": "s1",
                        "task": "normal task",
                        "dependencies": [],
                        "model_hint": None,
                    },
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=plan_json),
                )
            ]
        )
        planner = DAGPlanner(llm=llm)
        plan = await planner.plan("Normal task")

        assert plan.steps[0].model_hint is None

    def test_invalid_model_hint_normalized(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unknown model_hint values should be normalized to None with a warning."""
        data = {
            "steps": [
                {
                    "id": "s1",
                    "task": "task with bad hint",
                    "dependencies": [],
                    "model_hint": "turbo_ultra",
                },
            ]
        }
        with caplog.at_level(logging.WARNING, logger="fim_one.core.planner.planner"):
            steps = DAGPlanner._dict_to_steps(data)

        assert len(steps) == 1
        assert steps[0].model_hint is None
        assert any("turbo_ultra" in record.message for record in caplog.records)

    def test_valid_model_hints_not_normalized(self) -> None:
        """'fast' and 'reasoning' should pass through without normalization."""
        data = {
            "steps": [
                {"id": "s1", "task": "quick", "model_hint": "fast"},
                {"id": "s2", "task": "deep", "model_hint": "reasoning"},
                {"id": "s3", "task": "normal", "model_hint": None},
            ]
        }
        steps = DAGPlanner._dict_to_steps(data)
        assert steps[0].model_hint == "fast"
        assert steps[1].model_hint == "reasoning"
        assert steps[2].model_hint is None

    def test_schema_enum_constraint(self) -> None:
        """The _PLAN_SCHEMA should include model_hint with enum constraint."""
        from fim_one.core.planner.planner import _PLAN_SCHEMA

        model_hint_schema = _PLAN_SCHEMA["properties"]["steps"]["items"][
            "properties"
        ]["model_hint"]
        assert "enum" in model_hint_schema
        assert set(model_hint_schema["enum"]) == {"fast", "reasoning", None}
