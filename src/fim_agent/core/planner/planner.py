"""DAG planner that decomposes a goal into a dependency graph of steps.

The ``DAGPlanner`` prompts an LLM to break a high-level goal into discrete
steps with explicit dependency edges, then validates the resulting structure
is a valid DAG (no cycles, no dangling references).
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fim_agent.core.model import BaseLLM, ChatMessage
from fim_agent.core.model.usage import UsageSummary
from fim_agent.core.utils import extract_json

from .types import ExecutionPlan, PlanStep

logger = logging.getLogger(__name__)

_PLANNING_PROMPT = """\
You are a task-planning assistant.  Given a high-level goal (and optional \
context), decompose it into a set of concrete steps that can be executed \
by a tool-using agent.

Current date and time: {current_datetime} (the current year is {current_year}). \
When planning steps that involve searching for up-to-date information, \
use the current year ({current_year}) in the task description, NOT a previous year.

Each step must have:
- "id": a unique string identifier (e.g. "step_1", "step_2", ...).
- "task": a clear, actionable description of what to do.
- "dependencies": a list of step IDs that must complete before this step \
can start.  Use an empty list for steps that have no prerequisites.
- "tool_hint": (optional) the name of a tool that would be useful for this \
step, or null if no specific tool is needed.
- "model_hint": (optional) a role name indicating which model should handle \
this step (e.g. "fast", "vision", "general"), or null for the default model.

Rules:
1. Steps MUST form a valid directed acyclic graph (DAG) -- no circular \
dependencies.
2. Minimise the number of sequential dependencies to allow maximum \
parallelism.
3. Each step should be self-contained and produce a clear output.
4. Order the steps list so that dependencies appear before dependents \
when possible.
5. Keep the plan CONCISE -- prefer fewer, meatier steps over many trivial \
ones.  If several checks or computations can be done in a single script, \
combine them into ONE step rather than splitting each into its own step.  \
Aim for 2-4 steps for simple goals, up to 5-6 for complex ones.
6. Each step will be executed by a tool-using agent that can write and run \
code.  A single step can perform multiple operations (e.g. check four \
character types in one script), so do NOT create separate steps for \
trivially related sub-tasks.
7. LANGUAGE: Write the "task" descriptions in the same language as the goal. \
If the goal is in Chinese, write tasks in Chinese.
8. IMPORTANT: Keep "task" descriptions CONCISE (1-3 sentences). Do NOT copy \
or echo large blocks of text, conversation history, or report content into \
the task field. Instead, reference it briefly (e.g. "Translate the report \
from the previous conversation into English, preserving Markdown formatting").

Respond with a single JSON object:
{{
  "steps": [
    {{"id": "step_1", "task": "...", "dependencies": [], "tool_hint": null, "model_hint": null}},
    {{"id": "step_2", "task": "...", "dependencies": ["step_1"], "tool_hint": "some_tool", "model_hint": "fast"}}
  ]
}}
"""


class DAGPlanner:
    """Decomposes a goal into a DAG execution plan using an LLM.

    Args:
        llm: The language model to use for planning.
    """

    def __init__(self, llm: BaseLLM) -> None:
        self._llm = llm

    async def plan(self, goal: str, context: str = "") -> ExecutionPlan:
        """Generate an execution plan for the given goal.

        Args:
            goal: The high-level objective to decompose.
            context: Optional additional context to inform the planning
                process (e.g. results from a previous round).

        Returns:
            An ``ExecutionPlan`` with validated DAG structure.

        Raises:
            ValueError: If the LLM produces an invalid DAG (cycles or
                dangling dependency references).
        """
        messages = self._build_messages(goal, context)
        response_format = self._json_response_format()

        result = await self._llm.chat(
            messages,
            response_format=response_format,
        )

        content = result.message.content or ""
        steps = self._parse_steps(content)
        self._validate_dag(steps)

        total_usage: UsageSummary | None = None
        if result.usage:
            total_usage = UsageSummary(
                prompt_tokens=result.usage.get("prompt_tokens", 0),
                completion_tokens=result.usage.get("completion_tokens", 0),
                total_tokens=result.usage.get("total_tokens", 0),
                llm_calls=1,
            )

        return ExecutionPlan(goal=goal, steps=steps, total_usage=total_usage)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(self, goal: str, context: str) -> list[ChatMessage]:
        """Construct the message list for the planning LLM call.

        Args:
            goal: The high-level objective.
            context: Optional extra context.

        Returns:
            A list of ``ChatMessage`` objects.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
        messages: list[ChatMessage] = [
            ChatMessage(
                role="system",
                content=_PLANNING_PROMPT.format(
                    current_datetime=now,
                    current_year=now_dt.year,
                ),
            ),
        ]

        user_content = f"Goal: {goal}"
        if context:
            user_content += f"\n\nAdditional context:\n{context}"

        messages.append(ChatMessage(role="user", content=user_content))
        return messages

    def _json_response_format(self) -> dict[str, Any] | None:
        """Return a JSON-mode response format if supported by the LLM.

        Returns:
            ``{{"type": "json_object"}}`` or ``None``.
        """
        if self._llm.abilities.get("json_mode", False):
            return {"type": "json_object"}
        return None

    def _parse_steps(self, content: str) -> list[PlanStep]:
        """Parse the LLM response into a list of ``PlanStep`` objects.

        Args:
            content: Raw JSON string from the LLM.

        Returns:
            A list of parsed plan steps.

        Raises:
            ValueError: If the content is not valid JSON or does not
                contain a ``steps`` array.
        """
        data = extract_json(content)
        if data is None:
            logger.warning(
                "Failed to parse plan JSON (%d chars): %.500s",
                len(content),
                content,
            )
            raise ValueError(
                f"LLM returned unparseable content for plan: {content[:200]}"
            )

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError(
                "LLM response missing 'steps' array. "
                f"Got keys: {list(data.keys())}"
            )

        steps: list[PlanStep] = []
        for raw in raw_steps:
            step = PlanStep(
                id=str(raw.get("id", "")),
                task=str(raw.get("task", "")),
                dependencies=[str(d) for d in raw.get("dependencies", [])],
                tool_hint=raw.get("tool_hint"),
                model_hint=raw.get("model_hint"),
            )
            steps.append(step)

        return steps

    def _validate_dag(self, steps: list[PlanStep]) -> None:
        """Validate that the steps form a valid DAG.

        Checks two properties:
        1. All dependency IDs reference existing steps.
        2. There are no circular dependencies (via topological sort).

        Args:
            steps: The list of plan steps to validate.

        Raises:
            ValueError: If the graph contains dangling references or cycles.
        """
        step_ids = {step.id for step in steps}

        # Check for dangling dependency references.
        for step in steps:
            for dep_id in step.dependencies:
                if dep_id not in step_ids:
                    raise ValueError(
                        f"Step '{step.id}' depends on unknown step '{dep_id}'. "
                        f"Known step IDs: {sorted(step_ids)}"
                    )

        # Topological sort via Kahn's algorithm to detect cycles.
        in_degree: dict[str, int] = {s.id: 0 for s in steps}
        adjacency: dict[str, list[str]] = {s.id: [] for s in steps}

        for step in steps:
            for dep_id in step.dependencies:
                adjacency[dep_id].append(step.id)
                in_degree[step.id] += 1

        queue: deque[str] = deque(
            sid for sid, degree in in_degree.items() if degree == 0
        )
        visited_count = 0

        while queue:
            current = queue.popleft()
            visited_count += 1
            for neighbour in adjacency[current]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if visited_count != len(steps):
            remaining = {
                sid for sid, degree in in_degree.items() if degree > 0
            }
            raise ValueError(
                f"Circular dependency detected among steps: {sorted(remaining)}"
            )

        logger.debug(
            "DAG validation passed: %d steps, no cycles", len(steps),
        )
