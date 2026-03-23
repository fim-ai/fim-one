"""DAG planner that decomposes a goal into a dependency graph of steps.

The ``DAGPlanner`` prompts an LLM to break a high-level goal into discrete
steps with explicit dependency edges, then validates the resulting structure
is a valid DAG (no cycles, no dangling references).
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fim_one.core.model import BaseLLM, ChatMessage
from fim_one.core.model.structured import structured_llm_call
from fim_one.core.model.usage import UsageSummary
from fim_one.core.utils import extract_json_value

from .types import ExecutionPlan, PlanStep

logger = logging.getLogger(__name__)

_PLANNER_DESC_LEN = int(os.getenv("DAG_PLANNER_DESC_LENGTH", "120"))

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
- "model_hint": one of "fast", "reasoning", or null.  Set to "fast" ONLY for \
trivially simple, deterministic steps (e.g. format conversion, simple calculation, \
straightforward data lookup).  Set to "reasoning" for steps that require deep \
analysis, complex multi-step reasoning, mathematical proofs, strategic \
decision-making, or domain-expert knowledge (legal, medical, financial analysis). \
Set to null for normal steps that need a capable model.  When in doubt, use \
null — it uses the general-purpose model which handles most tasks well.  \
IMPORTANT: Never set "fast" for steps involving report writing, synthesis, \
comparison, or any output that requires nuanced judgment.

Rules:
1. Steps MUST form a valid directed acyclic graph (DAG) -- no circular \
dependencies.
2. Minimise the number of sequential dependencies to allow maximum \
parallelism.
3. Each step should be self-contained and produce a clear output.
4. Order the steps list so that dependencies appear before dependents \
when possible.
5. Keep the plan CONCISE -- do NOT create separate steps for trivially \
related sub-tasks that can be done in a single script.  However, split \
genuinely independent research or data-gathering tasks into separate steps \
so they can run IN PARALLEL.  Aim for 2-4 steps for simple goals, 5-8 for \
moderately complex goals, and up to 10 for highly complex goals that \
involve multiple independent research dimensions.
6. Each step will be executed by a tool-using agent that can write and run \
code.  A single step can perform multiple operations (e.g. check four \
character types in one script).  But if two tasks use DIFFERENT tools or \
search DIFFERENT topics, they SHOULD be separate parallel steps.
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
    {{"id": "step_2", "task": "...", "dependencies": ["step_1"], "tool_hint": "some_tool", "model_hint": "fast"}},
    {{"id": "step_3", "task": "...", "dependencies": ["step_2"], "tool_hint": null, "model_hint": "reasoning"}}
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
                    "model_hint": {
                        "type": ["string", "null"],
                        "enum": ["fast", "reasoning", None],
                    },
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
        tools: list[dict[str, str]] | None = None,
        skill_descriptions: list[dict[str, str]] | None = None,
    ) -> ExecutionPlan:
        """Generate an execution plan for the given goal.

        Args:
            goal: The high-level objective to decompose.
            context: Optional additional context to inform the planning
                process (e.g. results from a previous round).
            tool_names: Deprecated — use *tools* instead.  A plain list
                of tool name strings (no descriptions).
            tools: List of ``{"name": ..., "description": ...}`` dicts
                describing available tools.  When provided, *tool_names*
                is ignored.
            skill_descriptions: Optional list of
                ``{"name": ..., "description": ...}`` dicts describing
                available skills (SOPs).  When provided, the planner can
                suggest ``tool_hint="read_skill"`` for steps that match
                a skill's domain.

        Returns:
            An ``ExecutionPlan`` with validated DAG structure.

        Raises:
            ValueError: If the LLM produces an invalid DAG (cycles or
                dangling dependency references), or unparseable content.
        """
        messages = self._build_messages(
            goal, context, tool_names, tools, skill_descriptions,
        )

        call_result = await structured_llm_call(
            self._llm,
            messages,
            schema=_PLAN_SCHEMA,
            function_name="create_plan",
            parse_fn=self._dict_to_steps,
        )

        steps: list[PlanStep] = call_result.value or []
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
        tools: list[dict[str, str]] | None = None,
        skill_descriptions: list[dict[str, str]] | None = None,
    ) -> list[ChatMessage]:
        """Construct the message list for the planning LLM call.

        Args:
            goal: The high-level objective.
            context: Optional extra context.
            tool_names: Deprecated plain list of tool name strings.
            tools: Rich tool descriptors ``{"name": ..., "description": ...}``.
                Takes priority over *tool_names* when both are provided.
            skill_descriptions: Optional skill descriptors for auto-discovery.

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
        if tools:
            # Rich format: one line per tool with name + description
            tool_lines = []
            for t in tools:
                desc = t.get('description', '')
                if len(desc) > _PLANNER_DESC_LEN:
                    desc = desc[:_PLANNER_DESC_LEN - 3] + "..."
                tool_lines.append(f"- {t['name']}: {desc}")
            user_content += "\n\nAvailable tools:\n" + "\n".join(tool_lines)
        elif tool_names:
            # Legacy fallback: names only
            user_content += f"\n\nAvailable tools: {', '.join(tool_names)}"

        # Inject skill catalogue so the planner can route steps to skills.
        if skill_descriptions:
            skill_lines = []
            for s in skill_descriptions:
                desc = s.get("description", "")
                if len(desc) > _PLANNER_DESC_LEN:
                    desc = desc[: _PLANNER_DESC_LEN - 3] + "..."
                skill_lines.append(f"- {s['name']}: {desc}")
            user_content += (
                "\n\nAvailable skills (specialized procedures the agent can follow):\n"
                + "\n".join(skill_lines)
                + "\n\nWhen a step's task clearly matches a skill's domain, set "
                "tool_hint to \"read_skill\" and mention the skill name in the "
                "task description (e.g. \"Read and follow the 'legal-advisor' "
                "skill, then analyse ...\")."
            )

        if context:
            user_content += f"\n\nAdditional context:\n{context}"

        messages.append(ChatMessage(role="user", content=user_content))
        return messages

    @staticmethod
    def _dict_to_steps(data: dict[str, Any]) -> list[PlanStep] | None:
        """Transform a raw dict into a list of ``PlanStep`` objects.

        Used as ``parse_fn`` for :func:`structured_llm_call`.  Returns
        ``None`` when the data is unparseable so the degradation chain
        in ``structured_llm_call`` can continue to the next level.

        Args:
            data: Parsed JSON dict from the LLM.

        Returns:
            A list of plan steps, or ``None`` if the data is malformed.
        """
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            # LLM sometimes returns a single step object instead of {"steps": [...]}
            if isinstance(raw_steps, dict) and ("id" in raw_steps or "task" in raw_steps):
                raw_steps = [raw_steps]
            elif isinstance(raw_steps, str):
                # Double-encoded JSON string — use extract_json_value which
                # handles literal newlines inside JSON strings, invalid escape
                # sequences, and other common LLM serialization quirks.
                parsed = extract_json_value(raw_steps)
                if isinstance(parsed, list):
                    raw_steps = parsed
                elif isinstance(parsed, dict) and ("id" in parsed or "task" in parsed):
                    raw_steps = [parsed]
                elif "id" in data and "task" in data:
                    # Flattened step: LLM put step fields at top level alongside garbage 'steps'
                    logger.warning(
                        "Malformed 'steps' string (len=%d), but found flattened step fields — recovering",
                        len(raw_steps),
                    )
                    raw_steps = [data]
                else:
                    logger.warning(
                        "Failed to parse 'steps' string (len=%d): %.500s — returning None for retry",
                        len(raw_steps),
                        raw_steps,
                    )
                    return None
            elif "id" in data and "task" in data:
                # Entire response is a single step (no "steps" wrapper)
                raw_steps = [data]
            else:
                logger.warning(
                    "LLM 'steps' is not an array (type=%s, keys=%s) — returning None for retry",
                    type(raw_steps).__name__,
                    list(data.keys()),
                )
                return None

        _VALID_MODEL_HINTS = {"fast", "reasoning"}

        steps: list[PlanStep] = []
        for raw in raw_steps:
            raw_hint = raw.get("model_hint")
            if raw_hint is not None and raw_hint not in _VALID_MODEL_HINTS:
                logger.warning(
                    "Step '%s' has unknown model_hint '%s' — normalizing to None",
                    raw.get("id", "?"),
                    raw_hint,
                )
                raw_hint = None

            step = PlanStep(
                id=str(raw.get("id", "")),
                task=str(raw.get("task", "")),
                dependencies=[str(d) for d in raw.get("dependencies", [])],
                tool_hint=raw.get("tool_hint"),
                model_hint=raw_hint,
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
