"""Built-in plan/todo tool for the ReAct loop.

Long multi-step ReAct runs drift: the model forgets sub-goals, repeats
work, or declares victory with steps unfinished.  The fix (borrowed from
Claude Code's TodoWrite) is a *plan board* the model maintains itself:

- :class:`UpdatePlanTool` — a tool that only records state, never acts.
  The model calls it to write down its plan as a todo checklist and to
  update item statuses as it progresses.
- :class:`PlanState` — the per-run mutable state shared between the tool
  and the ReAct loop.
- :func:`make_plan_reminder` — a reminder message the loop injects when
  the plan has gone stale (several tool rounds without an update).  The
  reminder embeds the full current checklist, so plan state survives
  micro-compaction of older tool results.

The tool is registered by ``ReActAgent.__init__`` (like the workspace
tools) and must NOT be part of the builtin auto-discovery — it depends on
a live per-agent :class:`PlanState`.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import json
from typing import Any

from fim_one.core.tool.base import BaseTool

VALID_STATUSES = ("pending", "in_progress", "completed")

_STATUS_MARKS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}


class PlanState:
    """Mutable plan board for a single ReAct run.

    The ReAct loop resets this at the start of every ``run()`` so plans
    never leak across runs of a reused agent instance.
    """

    def __init__(self) -> None:
        self.todos: list[dict[str, str]] = []

    def reset(self) -> None:
        self.todos = []

    def replace(self, todos: list[dict[str, str]]) -> None:
        """Full-replace the todo list (TodoWrite semantics)."""
        self.todos = todos

    @property
    def has_open_items(self) -> bool:
        """True when a plan exists and at least one item is unfinished."""
        return bool(self.todos) and any(
            t["status"] != "completed" for t in self.todos
        )

    def render(self) -> str:
        """Render the checklist as markdown, one item per line."""
        if not self.todos:
            return "(no plan recorded)"
        lines = [
            f"{_STATUS_MARKS[t['status']]} {t['content']}" for t in self.todos
        ]
        return "\n".join(lines)


def normalize_todos(raw: Any) -> list[dict[str, str]]:
    """Validate and normalise the ``todos`` argument from the LLM.

    Accepts a list of ``{content, status}`` objects or the same as a
    JSON-encoded string (models occasionally double-encode arguments).

    Raises:
        ValueError: With a model-readable message when the structure is
            invalid.  The caller returns it as the tool result so the
            model can self-correct on the next iteration.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"todos is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("todos must be an array of {content, status} objects.")
    if not raw:
        raise ValueError("todos must not be empty.")

    normalized: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"todos[{i}] must be an object, got {type(item).__name__}.")
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip().lower()
        if not content:
            raise ValueError(f"todos[{i}].content must be a non-empty string.")
        if status not in VALID_STATUSES:
            raise ValueError(
                f"todos[{i}].status must be one of {list(VALID_STATUSES)}, got {status!r}."
            )
        normalized.append({"content": content, "status": status})
    return normalized


class UpdatePlanTool(BaseTool):
    """Record or update the plan for the current task.

    Pure state tool: it never performs any action.  Full-replace
    semantics — every call must pass the complete todo list.
    """

    def __init__(self, state: PlanState) -> None:
        self._state = state

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def category(self) -> str:
        return "planning"

    @property
    def display_name(self) -> str:
        return "Update Plan"

    @property
    def description(self) -> str:
        return (
            "Record or update your plan for the current task as a todo "
            "checklist. Use this at the START of any multi-step task (3+ "
            "distinct steps) to write down the steps, then call it again "
            "whenever you complete a step to update statuses. Full-replace "
            "semantics: always pass the complete list. Keep exactly one "
            "item in_progress at a time. This tool only records state — "
            "it performs no action."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The complete todo list (full replace).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Short imperative description of the step.",
                            },
                            "status": {
                                "type": "string",
                                "enum": list(VALID_STATUSES),
                                "description": "Current status of this step.",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        }

    async def run(self, **kwargs: Any) -> str:
        try:
            todos = normalize_todos(kwargs.get("todos"))
        except ValueError as exc:
            return f"[Error] {exc}"
        self._state.replace(todos)
        remaining = sum(1 for t in todos if t["status"] != "completed")
        return (
            f"Plan updated ({len(todos)} items, {remaining} open):\n"
            + self._state.render()
        )


def make_plan_reminder(state: PlanState) -> str:
    """Build the stale-plan reminder injected by the ReAct loop.

    Embeds the full checklist so the plan re-enters the context even
    after older ``update_plan`` tool results were micro-compacted away.
    """
    return (
        "<plan-reminder>\n"
        "Your plan has not been updated for several tool calls. "
        "Current plan state:\n"
        f"{state.render()}\n"
        "If you completed any of these steps, call update_plan now to mark "
        "them completed (keep exactly one item in_progress). If the plan no "
        "longer matches reality, rewrite it. Then continue with the most "
        "direct next step.\n"
        "</plan-reminder>"
    )
