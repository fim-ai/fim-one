"""Run Workflow tool — invoke a static workflow (DAG) synchronously from an agent.

Workflows are the *static recipe* counterpart to skills: deterministic graphs an
agent reaches for when a sub-task must run the same way every time (with an audit
trail).  This tool is the runtime's symmetric twin of ``read_skill`` — the agent
loop hands it to the LLM, which pulls a workflow by name when it needs one.

Only workflows that are active, visible to the user, and free of human-approval
gates are runnable inline.  Re-entrancy and deep nesting are refused so that an
``AGENT`` node inside a workflow can never trigger an unbounded call cycle.
"""

from __future__ import annotations

import contextvars
import json
import time
import uuid
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

        from fim_one.core.workflow.engine import WorkflowEngine
        from fim_one.core.workflow.parser import (
            BlueprintValidationError,
            parse_blueprint,
        )
        from fim_one.core.workflow.types import ExecutionContext, NodeType
        from fim_one.db import create_session
        from fim_one.db.models.workflow import Workflow, WorkflowRun

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

            try:
                parsed = parse_blueprint(wf.blueprint)
            except BlueprintValidationError as exc:
                return f"[Error] invalid workflow blueprint: {exc}"

            # Fail closed on human-approval gates — they block for minutes/hours.
            if any(node.type == NodeType.HUMAN_INTERVENTION for node in parsed.nodes):
                return (
                    f"[Error] workflow '{name}' contains a human-approval step and "
                    "cannot be run inline; trigger it from the Workflows page instead."
                )

            # Re-entrancy / depth guard.
            active = _active_workflows.get()
            if wf.id in active:
                return f"[Error] workflow '{name}' is already running (cycle prevented)"
            if len(active) >= _MAX_WORKFLOW_DEPTH:
                return f"[Error] workflow nesting too deep (max {_MAX_WORKFLOW_DEPTH})"

            # Quota gate: workflow LLM/Agent usage bills to the owner (mirrors the
            # webhook/cron paths). Refuse up-front when the owner is over quota so
            # an agent can't mint free, unmetered LLM usage via run_workflow.
            from fim_one.web.api.chat import _get_quota_status

            _used, _cap = await _get_quota_status(wf.user_id)
            if _cap > 0 and _used >= _cap:
                return (
                    f"[Error] workflow '{name}' cannot run: owner token quota exceeded"
                )

            env_vars: dict[str, str] = {}
            if wf.env_vars_blob:
                try:
                    from fim_one.core.security.encryption import decrypt_credential

                    env_vars = decrypt_credential(wf.env_vars_blob)
                except Exception:
                    pass

            # Run as the workflow owner (mirrors the trigger endpoint): subscribed
            # workflows execute with the publisher's bound credentials, not the caller's.
            run_user_id = wf.user_id
            run_id = str(uuid.uuid4())

            # Best-effort audit record so agent-triggered runs show in run history.
            try:
                async with create_session() as session:
                    session.add(
                        WorkflowRun(
                            id=run_id,
                            workflow_id=wf.id,
                            user_id=run_user_id,
                            blueprint_snapshot=wf.blueprint,
                            inputs=inputs,
                            status="running",
                        )
                    )
                    await session.commit()
            except Exception:
                pass

            outputs: dict[str, Any] = {}
            final_status = "completed"
            error_msg: str | None = None
            run_tokens = 0
            start = time.time()

            token = _active_workflows.set(active | {wf.id})
            try:
                engine = WorkflowEngine(
                    max_concurrency=5,
                    env_vars=env_vars,
                    run_id=run_id,
                    user_id=run_user_id,
                    workflow_id=wf.id,
                )
                exec_context = ExecutionContext(
                    run_id=run_id,
                    user_id=run_user_id,
                    workflow_id=wf.id,
                    env_vars=env_vars,
                    db_session_factory=create_session,
                    depth=len(active) + 1,
                )
                async for event_name, event_data in engine.execute_streaming(
                    parsed, inputs, context=exec_context
                ):
                    if event_name == "run_completed":
                        outputs = event_data.get("outputs", {}) or {}
                        final_status = event_data.get("status", "completed")
                        run_tokens = int(event_data.get("total_tokens", 0) or 0)
                    elif event_name == "run_failed":
                        final_status = "failed"
                        error_msg = event_data.get("error")
                        run_tokens = int(event_data.get("total_tokens", 0) or 0)
            finally:
                _active_workflows.reset(token)

            elapsed_ms = int((time.time() - start) * 1000)

            # Finalize the audit record.
            try:
                from datetime import UTC, datetime

                async with create_session() as session:
                    db_run = await session.get(WorkflowRun, run_id)
                    if db_run is not None:
                        db_run.status = final_status
                        db_run.outputs = outputs
                        db_run.completed_at = datetime.now(UTC)
                        db_run.duration_ms = elapsed_ms
                        db_run.total_tokens = run_tokens
                        await session.commit()
            except Exception:
                pass

            if final_status != "completed":
                return f"[Error] workflow '{name}' failed: {error_msg or 'unknown error'}"

            body = json.dumps(outputs, ensure_ascii=False, indent=2, default=str)
            return f"Workflow '{name}' completed.\n\n--- Outputs ---\n{body}"
        except Exception as e:
            return f"[Error] failed to run workflow: {e}"
