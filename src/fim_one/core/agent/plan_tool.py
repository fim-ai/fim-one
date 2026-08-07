"""Built-in plan/todo tool for the ReAct loop.

Long multi-step ReAct runs drift: the model forgets sub-goals, repeats
work, or declares victory with steps unfinished.  The fix (borrowed from
Claude Code's TodoWrite) is a *plan board* the model maintains itself:

- :class:`UpdatePlanTool` — a tool that only records state, never acts.
  The model calls it to write down its plan as a todo checklist and to
  update item statuses as it progresses.
- :class:`PlanState` — the per-run mutable state shared between the tool
  and the ReAct loop.
- :class:`PlanReminderTracker` — the per-run reminder state machine both
  ReAct loops (JSON and native) feed with one call per tool round.  It
  decides when to inject which reminder:

  * stale — the plan has open items but no ``update_plan`` call for
    several rounds; re-embeds the full checklist so plan state survives
    micro-compaction of older tool results.
  * repetition — the same tool has been called many rounds in a row
    (typically fruitless searches); tells the model to change approach
    instead of grinding.
  * no-plan — sustained tool activity with an empty plan board; a
    one-shot nudge to write the plan down.

- :func:`make_open_plan_note` — appended to the completion checklist
  when the model tries to finalise with unfinished plan items.

The tool is registered by ``ReActAgent.__init__`` (like the workspace
tools) and must NOT be part of the builtin auto-discovery — it depends on
a live per-agent :class:`PlanState`.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import json
from dataclasses import dataclass
from typing import Any, Sequence

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
            "item in_progress at a time, and batch status changes: mark "
            "the finished step completed AND set the next step in_progress "
            "in the same call. This tool only records state — it performs "
            "no action."
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


def make_repetition_reminder(state: PlanState, tool_name: str, streak: int) -> str:
    """Reminder for a model grinding the same tool round after round.

    Fires on same-tool-different-args streaks (e.g. a dozen search
    queries that all come back empty) — the exact-duplicate case is
    already handled by the loop's cycle detection.
    """
    return (
        "<plan-reminder>\n"
        f"You have called the tool '{tool_name}' {streak} rounds in a row. "
        "If those calls keep failing or returning nothing useful, more of "
        "the same is unlikely to help — step back and try a different "
        "approach (a different tool, a different source, or a direct "
        "route to the goal). Current plan state:\n"
        f"{state.render()}\n"
        "Call update_plan to record the adjusted approach, then continue.\n"
        "</plan-reminder>"
    )


def make_no_plan_nudge() -> str:
    """One-shot nudge for sustained tool activity with no plan recorded."""
    return (
        "<plan-reminder>\n"
        "You have made several tool calls without recording a plan. If "
        "this task has multiple remaining steps, call update_plan now to "
        "write them down as a todo checklist — it keeps long tasks on "
        "track. If only one step remains, skip the plan and finish "
        "directly.\n"
        "</plan-reminder>"
    )


def make_open_plan_note(state: PlanState) -> str:
    """Completion-checklist addendum when finalising with open plan items."""
    return (
        "Additionally, your plan still has unfinished items:\n"
        f"{state.render()}\n"
        "Before finalising, either complete them (call tools), or call "
        "update_plan to mark them completed / record why they are no "
        "longer needed."
    )


@dataclass
class PlanReminder:
    """A reminder the loop should inject after the current tool round."""

    kind: str  # "stale" | "repetition" | "no_plan"
    message: str


class PlanReminderTracker:
    """Per-run reminder state machine shared by both ReAct loops.

    The loop calls :meth:`observe_round` exactly once per completed tool
    round (a round = one JSON-mode tool call, or one native-mode tool
    batch) and injects the returned message, if any.  Keeping the
    counters here rather than as loose loop locals means the JSON and
    native loops cannot drift apart.
    """

    def __init__(
        self,
        state: PlanState,
        *,
        stale_interval: int = 3,
        repeat_threshold: int = 4,
        no_plan_after: int = 5,
    ) -> None:
        self._state = state
        self._stale_interval = max(1, stale_interval)
        self._repeat_threshold = max(2, repeat_threshold)
        self._no_plan_after = max(1, no_plan_after)
        self._rounds_since_plan = 0
        self._repeat_tool: str | None = None
        self._repeat_streak = 0
        self._rounds_without_plan = 0
        self._no_plan_nudged = False

    def observe_round(self, tool_names: Sequence[str]) -> PlanReminder | None:
        """Record one tool round; return the reminder to inject, if any."""
        names = [n for n in tool_names if n]

        if "update_plan" in names:
            # The model just (re)planned — every nudge is moot for now.
            self._rounds_since_plan = 0
            self._repeat_tool = None
            self._repeat_streak = 0
            self._rounds_without_plan = 0
            return None

        # Repetition streak: consecutive rounds whose only tool is the
        # same one.  Mixed-tool rounds break the streak.
        distinct = set(names)
        if len(distinct) == 1:
            tool = next(iter(distinct))
            if tool == self._repeat_tool:
                self._repeat_streak += 1
            else:
                self._repeat_tool = tool
                self._repeat_streak = 1
        else:
            self._repeat_tool = None
            self._repeat_streak = 0

        # Fires at the threshold and every multiple of it, so an ignored
        # reminder comes back rather than going silent forever.
        if (
            self._repeat_tool is not None
            and self._repeat_streak >= self._repeat_threshold
            and self._repeat_streak % self._repeat_threshold == 0
        ):
            self._rounds_since_plan = 0
            return PlanReminder(
                kind="repetition",
                message=make_repetition_reminder(
                    self._state, self._repeat_tool, self._repeat_streak,
                ),
            )

        if self._state.has_open_items:
            self._rounds_since_plan += 1
            if self._rounds_since_plan >= self._stale_interval:
                self._rounds_since_plan = 0
                return PlanReminder(
                    kind="stale",
                    message=make_plan_reminder(self._state),
                )
            return None

        if not self._state.todos and not self._no_plan_nudged:
            self._rounds_without_plan += 1
            if self._rounds_without_plan >= self._no_plan_after:
                self._no_plan_nudged = True
                return PlanReminder(kind="no_plan", message=make_no_plan_nudge())
        return None
