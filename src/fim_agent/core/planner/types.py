"""Type definitions for the Planner layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fim_agent.core.model.usage import UsageSummary


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
        result: The output produced after execution, if any.
        status: Current lifecycle status of this step.
    """

    id: str
    task: str
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str | None = None
    model_hint: str | None = None
    result: str | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
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
        final_answer: A synthesised answer if the goal was achieved.
        reasoning: The LLM's chain-of-thought reasoning behind the verdict.
    """

    achieved: bool
    confidence: float
    final_answer: str | None = None
    reasoning: str = ""
    usage: UsageSummary | None = None
