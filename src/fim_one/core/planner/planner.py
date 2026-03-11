"""DAG planner that decomposes a goal into a dependency graph of steps.

The ``DAGPlanner`` prompts an LLM to break a high-level goal into discrete
steps with explicit dependency edges, then validates the resulting structure
is a valid DAG (no cycles, no dangling references).
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fim_one.core.model import BaseLLM, ChatMessage
from fim_one.core.model.structured import structured_llm_call
from fim_one.core.model.usage import UsageSummary

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
- "model_hint": set to "fast" for simple, deterministic steps that require \
minimal reasoning (e.g. data lookup, format conversion, simple calculation, \
straightforward retrieval).  Set to null for steps that require deep reasoning, \
multi-step synthesis, complex analysis, or creative problem-solving.  When in \
doubt, use null — it is always safer to use the more capable model.

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
9. If a list of available tools is provided, the "tool_hint" field MUST only \
reference tools from that list. Do NOT suggest tools that are not available.

Respond with a single JSON object:
{{
  "steps": [
    {{"id": "step_1", "task": "...", "dependencies": [], "tool_hint": null, "model_hint": null}},
    {{"id": "step_2", "task": "...", "dependencies": ["step_1"], "tool_hint": "some_tool", "model_hint": "fast"}}
  ]
}}
"""


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "task": {"type": "string"},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "tool_hint": {"type": ["string", "null"]},
                    "model_hint": {"type": ["string", "null"]},
                },
                "required": ["id", "task"],
            },
        },
    },
    "required": ["steps"],
}


class DAGPlanner:
    """Decomposes a goal into a DAG execution plan using an LLM.

    Args:
        llm: The language model to use for planning.
    """

    def __init__(self, llm: BaseLLM, *, language_directive: str | None = None) -> None:
        self._llm = llm
        self._language_directive = language_directive

    async def plan(
        self,
        goal: str,
        context: str = "",
        tool_names: list[str] | None = None,
    ) -> ExecutionPlan:
        """Generate an execution plan for the given goal.

        Args:
            goal: The high-level objective to decompose.
            context: Optional additional context to inform the planning
                process (e.g. results from a previous round).

        Returns:
            An ``ExecutionPlan`` with validated DAG structure.

        Raises:
            ValueError: If the LLM produces an invalid DAG (cycles or
                dangling dependency references), or unparseable content.
        """
        messages = self._build_messages(goal, context, tool_names)

        call_result = await structured_llm_call(
            self._llm,
            messages,
            schema=_PLAN_SCHEMA,
            function_name="create_plan",
            parse_fn=self._dict_to_steps,
        )

        steps = call_result.value
        self._validate_dag(steps)

        total_usage: UsageSummary | None = None
        if call_result.total_usage:
            total_usage = UsageSummary(
                prompt_tokens=call_result.total_usage.get("prompt_tokens", 0),
                completion_tokens=call_result.total_usage.get("completion_tokens", 0),
                total_tokens=call_result.total_usage.get("total_tokens", 0),
                llm_calls=call_result.llm_calls,
            )

        return ExecutionPlan(goal=goal, steps=steps, total_usage=total_usage)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        goal: str,
        context: str,
        tool_names: list[str] | None = None,
    ) -> list[ChatMessage]:
        """Construct the message list for the planning LLM call.

        Args:
            goal: The high-level objective.
            context: Optional extra context.
            tool_names: Optional list of available tool names to constrain
                the planner's ``tool_hint`` suggestions.

        Returns:
            A list of ``ChatMessage`` objects.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
        system_content = _PLANNING_PROMPT.format(
            current_datetime=now,
            current_year=now_dt.year,
        )
        if self._language_directive:
            system_content += f"\n\n{self._language_directive}"
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_content),
        ]

        user_content = f"Goal: {goal}"
        if tool_names:
            user_content += f"\n\nAvailable tools: {', '.join(tool_names)}"
        if context:
            user_content += f"\n\nAdditional context:\n{context}"

        messages.append(ChatMessage(role="user", content=user_content))
        return messages

    @staticmethod
    def _dict_to_steps(data: dict[str, Any]) -> list[PlanStep]:
        """Transform a raw dict into a list of ``PlanStep`` objects.

        Used as ``parse_fn`` for :func:`structured_llm_call`.

        Args:
            data: Parsed JSON dict from the LLM.

        Returns:
            A list of plan steps.

        Raises:
            ValueError: If the dict does not contain a ``steps`` array.
        """
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            # LLM sometimes returns a single step object instead of {"steps": [...]}
            if "id" in data and "task" in data:
                raw_steps = [data]
            else:
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
        1. All dependency IDs reference existing steps (dangling refs are
           auto-removed with a warning instead of raising).
        2. There are no circular dependencies (via topological sort).

        Args:
            steps: The list of plan steps to validate.

        Raises:
            ValueError: If the graph contains cycles.
        """
        step_ids = {step.id for step in steps}

        # Auto-remove dangling dependency references (LLM may omit steps).
        for step in steps:
            dangling = [d for d in step.dependencies if d not in step_ids]
            if dangling:
                logger.warning(
                    "Step '%s' references unknown deps %s — removing them. "
                    "Known step IDs: %s",
                    step.id,
                    dangling,
                    sorted(step_ids),
                )
                step.dependencies = [
                    d for d in step.dependencies if d in step_ids
                ]

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
