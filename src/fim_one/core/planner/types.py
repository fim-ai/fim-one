"""Type definitions for the Planner layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from fim_one.core.model.usage import UsageSummary
from fim_one.core.tool.base import Artifact


@dataclass
class StepOutput:
    """Structured output from a completed DAG step."""

    summary: str
    # Typed execution metadata for downstream consumers (analyzer, synthesis,
    # UI):  ``step_type``, and for react steps ``iterations`` and
    # ``tools_used``.  Lets consumers reason about HOW a result was produced
    # (single direct call vs. multi-tool agent run) without parsing prose.
    data: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    # Extended-thinking the step's sub-agent produced while reaching the
    # summary.  Preserved so the analyzer / synthesis stages can use a clue
    # the step only surfaced in its reasoning instead of discarding it.
    reasoning: str | None = None
    # Raw tool observations gathered while the step ran (web fetches, search
    # results, file reads, …).  Unlike ``summary`` — which is the sub-agent's
    # own, necessarily lossy distillation — this preserves the source material
    # so downstream synthesis and the analyzer can verify the answer's factual
    # claims (totals like "6 CVEs", enumerations, attributes like severity)
    # against ground truth instead of trusting a summary that may have silently
    # dropped or mislabelled items.
    evidence: str | None = None

    def __str__(self) -> str:
        return self.summary  # backward compat for string formatting

    def __bool__(self) -> bool:
        return bool(self.summary)  # if step.result: still works


@dataclass
class PlanStep:
    """A single step in an execution plan.

    Each step represents a discrete unit of work in the DAG.  Dependencies
    are expressed as a list of step IDs that must complete before this step
    can begin.

    Attributes:
        id: Unique identifier for this step (e.g. ``"step_1"``).
        task: Human-readable description of what this step should accomplish.
        dependencies: IDs of steps that must complete first.
        tool_hint: Optional hint suggesting which tool to use.
        step_type: How the step is executed.  ``"react"`` (default) runs a
            full multi-iteration tool-using agent; ``"llm_direct"`` runs a
            single tool-less LLM call — for pure transformation/synthesis
            steps that only work on their dependencies' outputs.
        result: The output produced after execution, if any.
        status: Current lifecycle status of this step.
    """

    id: str
    task: str
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str | None = None
    model_hint: str | None = None
    step_type: Literal["react", "llm_direct"] = "react"
    result: StepOutput | None = None
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    duration: float | None = None
    usage: UsageSummary | None = None


@dataclass
class ExecutionPlan:
    """A DAG-structured execution plan.

    The plan is a directed acyclic graph where each ``PlanStep`` may depend
    on zero or more other steps.  The ``DAGExecutor`` uses this structure to
    determine which steps can run concurrently.

    Attributes:
        goal: The original high-level objective.
        steps: Ordered list of plan steps forming the DAG.
        current_round: Tracks how many planning/execution rounds have occurred
            (useful for iterative re-planning).
    """

    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    current_round: int = 1
    total_usage: UsageSummary | None = None


@dataclass
class AnalysisResult:
    """Result of plan reflection/analysis.

    Produced by ``PlanAnalyzer`` after evaluating whether the execution plan
    has achieved the original goal.

    Attributes:
        achieved: Whether the goal was fully accomplished.
        confidence: A score between 0.0 and 1.0 indicating confidence in
            the assessment.
        unrecoverable: Whether another round of planning could still reach
            the goal.  ``True`` means no — the request is impossible, a
            required capability is missing, or a resource is permanently
            unavailable.  A merely missing or half-finished deliverable is
            recoverable and leaves this ``False``.
        final_answer: A synthesised answer if the goal was achieved.
        reasoning: The LLM's chain-of-thought reasoning behind the verdict.
    """

    achieved: bool
    confidence: float
    unrecoverable: bool = False
    final_answer: str | None = None
    reasoning: str = ""
    usage: UsageSummary | None = None
