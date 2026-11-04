"""Run Workflow tool — invoke a static workflow (DAG) synchronously from an agent.

Workflows are the *static recipe* counterpart to skills: deterministic graphs an
agent reaches for when a sub-task must run the same way every time (with an audit
trail).  This tool is the runtime's symmetric twin of ``read_skill`` — the agent
loop hands it to the LLM, which pulls a workflow by name when it needs one.

Only workflows that are active, visible to the user, and free of human-approval
gates are runnable inline. A run executes as the agent's user (the caller):
their credentials and quota, and the author's env vars only when the caller
is the author.  Re-entrancy and deep nesting are refused so that an
``AGENT`` node inside a workflow can never trigger an unbounded call cycle.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

from fim_one.core.tool.base import BaseTool

# Tracks workflow IDs currently executing in this async context.  ContextVars
# propagate into child tasks the engine spawns, so a workflow that (transitively)
# tries to re-enter itself — or nest too deeply — is refused.
_active_workflows: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "active_workflows", default=frozenset()
)
_MAX_WORKFLOW_DEPTH = 3


class RunWorkflowTool(BaseTool):
    """Run a named workflow synchronously and return its outputs.

    The per-workflow input fields are advertised in the system prompt stub; the
    LLM passes matching values in ``inputs``.
    """

    def __init__(
        self,
        workflow_ids: list[str],
        user_id: str | None = None,
    ) -> None:
        self._workflow_ids = workflow_ids
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "run_workflow"

    @property
    def cacheable(self) -> bool:
        return False

    @property
    def display_name(self) -> str:
        return "Run Workflow"

    @property
    def description(self) -> str:
        return (
            "Run a named workflow (a deterministic multi-step automation) and "
            "return its structured outputs. Use this when a task maps to an "
            "existing workflow that should run the same way every time. Pass "
            "input values in 'inputs' matching the workflow's listed fields."
        )

    @property
    def category(self) -> str:
        return "workflows"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the workflow to run.",
                },
                "inputs": {
                    "type": "object",
                    "description": (
                        "Input values matching the workflow's input schema "
                        "(see the workflow's listed inputs). Omit if it takes none."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["name"],
        }

    async def run(self, **kwargs: Any) -> str:
        name = (kwargs.get("name") or "").strip()
        if not name:
            return "[Error] name is required"
        inputs = kwargs.get("inputs") or {}
        if not isinstance(inputs, dict):
            return "[Error] inputs must be an object"
        if not self._workflow_ids:
            return f"[Error] workflow not found: {name}"

        from sqlalchemy import select

        from fim_one.core.workflow.run_service import (
            RunMode,
            RunRefusedError,
            get_run_service,
        )
        from fim_one.db import create_session
        from fim_one.db.models.workflow import Workflow

        try:
            async with create_session() as session:
                result = await session.execute(
                    select(Workflow).where(
                        Workflow.id.in_(self._workflow_ids),
                        Workflow.name == name,
                        Workflow.is_active == True,  # noqa: E712
                        Workflow.status == "active",
                    )
                )
                wf = result.scalar_one_or_none()
            if not wf:
                return f"[Error] workflow not found or not runnable: {name}"

            # Re-entrancy / depth guard.
            active = _active_workflows.get()
            if wf.id in active:
                return f"[Error] workflow '{name}' is already running (cycle prevented)"
            if len(active) >= _MAX_WORKFLOW_DEPTH:
                return f"[Error] workflow nesting too deep (max {_MAX_WORKFLOW_DEPTH})"

            # The run executes as the agent's user (the caller), with that
            # user's credentials and quota; the author's env vars are not
            # injected unless the caller is the author. Human-approval steps
            # are refused: an agent turn cannot wait for them.
            token = _active_workflows.set(active | {wf.id})
            try:
                outcome = await get_run_service().run_to_completion(
                    wf,
                    caller_id=self._user_id,
                    inputs=inputs,
                    mode=RunMode.TOOL,
                    depth=len(active) + 1,
                )
            except RunRefusedError as exc:
                return _refusal_message(name, exc)
            finally:
                _active_workflows.reset(token)

            if outcome.status != "completed":
                return (
                    f"[Error] workflow '{name}' failed: {outcome.error or 'unknown error'}"
                )

            body = json.dumps(outcome.outputs, ensure_ascii=False, indent=2, default=str)
            return f"Workflow '{name}' completed.\n\n--- Outputs ---\n{body}"
        except Exception as e:
            return f"[Error] failed to run workflow: {e}"


def _refusal_message(name: str, exc: Any) -> str:
    """Tool-facing text for a run the run service refused to start."""
    if exc.error_code == "workflow_has_approval_step":
        return (
            f"[Error] workflow '{name}' contains a human-approval step and "
            "cannot be run inline; trigger it from the Workflows page instead. "
            f"({exc.error_code})"
        )
    reason = {
        "token_quota_exceeded": "token quota exceeded",
        "subscription_required": "a paid subscription is required",
        "workflow_owner_inactive": "the workflow owner's account is disabled",
    }.get(exc.error_code, exc.detail)
    return f"[Error] workflow '{name}' cannot run: {reason} ({exc.error_code})"
