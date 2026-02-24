"""Type definitions for the Agent layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from fim_agent.core.model.usage import UsageSummary


@dataclass
class Action:
    """An action decided by the agent.

    Represents either a tool invocation or a final answer produced by the
    ReAct reasoning loop.

    Attributes:
        type: Discriminator indicating whether this is a tool call or a
            terminal answer.
        reasoning: The chain-of-thought reasoning that led to this action.
        tool_name: Name of the tool to invoke (only for ``tool_call``).
        tool_args: Keyword arguments to pass to the tool (only for
            ``tool_call``).
        answer: The final textual answer (only for ``final_answer``).
    """

    type: Literal["tool_call", "final_answer"]
    reasoning: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    answer: str | None = None


@dataclass
class StepResult:
    """Result of a single ReAct step.

    Captures the action taken, the observation returned by the tool, and
    any error that occurred during execution.

    Attributes:
        action: The action that was executed in this step.
        observation: The string result returned by the tool, if successful.
        error: An error message if the tool call failed.
    """

    action: Action
    observation: str | None = None
    error: str | None = None


@dataclass
class AgentResult:
    """Final result from an agent run.

    Attributes:
        answer: The agent's final textual answer.
        steps: The full trace of intermediate steps taken.
        iterations: Total number of reasoning iterations consumed.
    """

    answer: str
    steps: list[StepResult] = field(default_factory=list)
    iterations: int = 0
    usage: UsageSummary | None = None
