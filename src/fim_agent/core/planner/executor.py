"""DAG executor that runs plan steps concurrently where possible.

The ``DAGExecutor`` respects the dependency edges in an ``ExecutionPlan`` and
launches independent steps in parallel (up to a configurable concurrency
limit) using ``asyncio``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from fim_agent.core.agent import ReActAgent
from fim_agent.core.agent.types import Action
from fim_agent.core.memory.context_guard import ContextGuard
from fim_agent.core.model.registry import ModelRegistry
from fim_agent.core.model.usage import UsageSummary

from .types import ExecutionPlan, PlanStep

# Type alias for the optional progress callback.
# Called with (step_id, event, data) where event is "started", "completed",
# or "iteration".
ProgressCallback = Callable[[str, str, dict[str, Any]], Any]

logger = logging.getLogger(__name__)


class DAGExecutor:
    """Execute an ``ExecutionPlan`` respecting DAG dependencies.

    Steps whose dependencies have all completed are launched concurrently.
    An ``asyncio.Semaphore`` caps the number of steps that may run at the
    same time.

    Args:
        agent: The ``ReActAgent`` used to execute individual steps.
        max_concurrency: Maximum number of steps running in parallel.
        model_registry: Optional registry for per-step model selection
            based on ``PlanStep.model_hint``.
        context_guard: Optional context-window budget manager used to
            truncate oversized dependency contexts.
        step_timeout: Maximum seconds a single step may run before being
            cancelled.  Defaults to 600 (10 minutes).
    """

    def __init__(
        self,
        agent: ReActAgent,
        max_concurrency: int = 5,
        model_registry: ModelRegistry | None = None,
        context_guard: ContextGuard | None = None,
        original_goal: str | None = None,
        stop_event: asyncio.Event | None = None,
        step_timeout: float = 600,
    ) -> None:
        self._agent = agent
        self._max_concurrency = max_concurrency
        self._model_registry = model_registry
        self._context_guard = context_guard
        self._original_goal = original_goal
        self._stop_event = stop_event
        self._step_timeout = step_timeout
        self._usage_lock = asyncio.Lock()

    async def execute(
        self,
        plan: ExecutionPlan,
        on_progress: ProgressCallback | None = None,
    ) -> ExecutionPlan:
        """Execute all steps in *plan*, respecting dependency order.

        The method modifies the plan's steps in-place, updating their
        ``status`` and ``result`` fields.

        Args:
            plan: The execution plan to run.
            on_progress: Optional callback invoked when a step starts or
                completes.  Signature: ``(step_id, event, data)`` where
                *event* is ``"started"`` or ``"completed"``.

        Returns:
            The same ``ExecutionPlan`` instance, with step results and
            statuses updated.
        """
        self._on_progress = on_progress
        semaphore = asyncio.Semaphore(self._max_concurrency)
        step_index = {step.id: step for step in plan.steps}
        pending_ids = {step.id for step in plan.steps}
        completed_ids: set[str] = set()
        running_tasks: dict[asyncio.Task[None], str] = {}

        try:
            while pending_ids or running_tasks:
                # If stop was requested (user inject), skip all remaining pending steps.
                if self._stop_event is not None and self._stop_event.is_set() and pending_ids:
                    for sid in sorted(pending_ids):
                        step_index[sid].status = "skipped"
                        self._notify(sid, "completed", {
                            "task": step_index[sid].task,
                            "status": "skipped",
                            "result": "Skipped — user changed requirements",
                        })
                    pending_ids.clear()

                # Identify steps that are ready to launch (sorted for deterministic order).
                ready_ids: list[str] = sorted(
                    (sid for sid in pending_ids
                     if all(dep in completed_ids for dep in step_index[sid].dependencies)),
                    key=lambda sid: step_index[sid].id,
                )

                # Launch ready steps.
                for sid in ready_ids:
                    pending_ids.discard(sid)
                    step = step_index[sid]
                    step.status = "running"
                    step.started_at = time.time()
                    self._notify(sid, "started", {"task": step.task, "started_at": step.started_at})

                    context = self._build_step_context(step, step_index, self._context_guard)
                    task = asyncio.create_task(
                        self._run_with_semaphore(semaphore, step, context),
                    )
                    running_tasks[task] = sid
                    logger.debug("Launched step '%s': %s", sid, step.task)

                if not running_tasks:
                    # No tasks running and nothing can be launched -- this
                    # would indicate a bug (e.g. failed dependency blocking).
                    if pending_ids:
                        logger.error(
                            "Deadlock: pending steps %s cannot proceed "
                            "(dependencies never completed)",
                            sorted(pending_ids),
                        )
                        for sid in pending_ids:
                            step_index[sid].status = "failed"
                            step_index[sid].result = (
                                "Step could not run: one or more dependencies failed."
                            )
                        pending_ids.clear()
                    break

                # Wait for at least one task to finish.
                done, _ = await asyncio.wait(
                    running_tasks.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for finished_task in done:
                    sid = running_tasks.pop(finished_task)

                    # Re-raise unexpected exceptions (step-level errors are
                    # already caught inside ``_execute_step``).
                    exc = finished_task.exception()
                    if exc is not None:
                        logger.exception(
                            "Unexpected error in step '%s'", sid, exc_info=exc,
                        )
                        step_index[sid].status = "failed"
                        step_index[sid].result = f"Unexpected error: {exc}"

                    completed_ids.add(sid)
                    step = step_index[sid]
                    completed_data: dict[str, Any] = {
                        "task": step.task,
                        "status": step.status,
                        "result": step.result,
                        "started_at": step.started_at,
                        "completed_at": step.completed_at,
                        "duration": step.duration,
                    }
                    if step.usage is not None:
                        completed_data["usage"] = {
                            "prompt_tokens": step.usage.prompt_tokens,
                            "completion_tokens": step.usage.completion_tokens,
                            "total_tokens": step.usage.total_tokens,
                        }
                    self._notify(sid, "completed", completed_data)
        except asyncio.CancelledError:
            # Cancel all in-flight step tasks so they don't keep running
            # (consuming LLM tokens / tool resources) after user hits Stop.
            if running_tasks:
                logger.info(
                    "DAG executor cancelled, cancelling %d running step(s): %s",
                    len(running_tasks),
                    list(running_tasks.values()),
                )
                for task in running_tasks:
                    task.cancel()
                # Give tasks a moment to handle cancellation gracefully.
                await asyncio.gather(*running_tasks, return_exceptions=True)
            raise

        # Aggregate step-level usage into the plan's total_usage.
        # Lock protects against concurrent execute() calls on the same plan
        # (e.g. during re-planning), since UsageSummary.__iadd__ is not atomic.
        async with self._usage_lock:
            step_usage = UsageSummary()
            for step in plan.steps:
                if step.usage is not None:
                    step_usage += step.usage
            if step_usage.llm_calls > 0:
                if plan.total_usage is not None:
                    plan.total_usage += step_usage
                else:
                    plan.total_usage = step_usage

        return plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify(self, step_id: str, event: str, data: dict[str, Any]) -> None:
        """Fire the progress callback if one was provided."""
        if self._on_progress is not None:
            self._on_progress(step_id, event, data)

    async def _run_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        step: PlanStep,
        context: str,
    ) -> None:
        """Acquire the semaphore and execute a single step.

        Args:
            semaphore: Concurrency-limiting semaphore.
            step: The plan step to execute.
            context: Contextual information from dependency results.
        """
        async with semaphore:
            try:
                await asyncio.wait_for(
                    self._execute_step(step, context),
                    timeout=self._step_timeout,
                )
            except asyncio.TimeoutError:
                step.status = "failed"
                step.result = f"Step execution timed out after {self._step_timeout}s"
                step.completed_at = time.time()
                if step.started_at is not None:
                    step.duration = round(step.completed_at - step.started_at, 2)
                logger.error(
                    "Step '%s' timed out after %ds", step.id, self._step_timeout,
                )

    def _resolve_agent(self, step: PlanStep) -> ReActAgent:
        """Select the appropriate agent for a step based on its model_hint.

        If a ``ModelRegistry`` is configured and the step has a ``model_hint``
        that matches a registered role, a temporary ``ReActAgent`` is created
        with the corresponding LLM.  Otherwise the default agent is returned.

        Args:
            step: The plan step to resolve an agent for.

        Returns:
            A ``ReActAgent`` instance to execute the step.
        """
        if self._model_registry is None or not step.model_hint:
            return self._agent

        try:
            llm = self._model_registry.get_by_role(step.model_hint)
        except KeyError:
            logger.debug(
                "No model registered for role '%s', using default agent",
                step.model_hint,
            )
            return self._agent

        return ReActAgent(
            llm=llm,
            tools=self._agent.tools,
            system_prompt=self._agent.system_prompt_override,
            extra_instructions=self._agent.extra_instructions,
            max_iterations=self._agent.max_iterations,
            context_guard=self._agent.context_guard,
        )

    async def _execute_step(self, step: PlanStep, context: str) -> None:
        """Execute a single plan step via the ReAct agent.

        On success the step's status is set to ``"completed"`` and its
        ``result`` is populated.  On failure the status becomes ``"failed"``
        and the error message is stored in ``result``.

        Args:
            step: The plan step to execute.
            context: Contextual information from completed dependency steps.
        """
        query = self._build_step_query(step, context)

        iter_start = 0.0

        def _on_iteration(
            iteration: int,
            action: Action,
            observation: str | None,
            error: str | None,
            step_result: Any = None,
        ) -> None:
            nonlocal iter_start
            # Skip non-tool events — final_answer is sent via "completed",
            # thinking shims are empty, __selecting_tools__ is a notification.
            if action.type == "final_answer":
                return
            if action.type == "thinking":
                return
            if action.tool_name == "__selecting_tools__":
                return
            is_starting = observation is None and error is None
            now = time.time()
            iter_elapsed: float | None = None
            if is_starting:
                iter_start = now
            else:
                iter_elapsed = round(now - iter_start, 2)
            status = "start" if is_starting else "done"
            payload: dict[str, Any] = {
                "iteration": iteration,
                "type": action.type,
                "status": status,
                "reasoning": action.reasoning,
                "tool_name": action.tool_name,
                "tool_args": action.tool_args,
                "observation": observation,
                "error": error,
            }
            if iter_elapsed is not None:
                payload["iter_elapsed"] = iter_elapsed
            # Attach artifact metadata for completed tool calls
            if not is_starting and step_result is not None:
                if getattr(step_result, "content_type", None):
                    payload["content_type"] = step_result.content_type
                if getattr(step_result, "artifacts", None):
                    payload["artifacts"] = step_result.artifacts
            self._notify(step.id, "iteration", payload)

        agent = self._resolve_agent(step)

        try:
            agent_result = await agent.run(
                query, on_iteration=_on_iteration,
            )
            step.status = "completed"
            step.result = agent_result.answer
            step.usage = agent_result.usage
            logger.info(
                "Step '%s' completed in %d iterations",
                step.id,
                agent_result.iterations,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            step.status = "failed"
            step.result = f"{type(exc).__name__}: {exc}"
            logger.exception("Step '%s' failed", step.id)
        finally:
            step.completed_at = time.time()
            if step.started_at is not None:
                step.duration = round(step.completed_at - step.started_at, 2)

    def _build_step_query(self, step: PlanStep, context: str) -> str:
        """Build the query string to send to the ReAct agent.

        Args:
            step: The plan step to execute.
            context: Pre-formatted context from dependency results.

        Returns:
            A query string incorporating the task description, any tool hint,
            and dependency context.
        """
        parts: list[str] = []
        if self._original_goal:
            parts.append(f"Original goal: {self._original_goal}")
        parts.append(f"Task: {step.task}")

        if step.tool_hint:
            parts.append(f"Suggested tool: {step.tool_hint}")

        if context:
            parts.append(f"Context from previous steps:\n{context}")

        return "\n\n".join(parts)

    @staticmethod
    def _build_step_context(
        step: PlanStep,
        step_index: dict[str, PlanStep],
        context_guard: ContextGuard | None = None,
    ) -> str:
        """Gather results from a step's completed dependencies.

        Args:
            step: The step whose dependency context is needed.
            step_index: Mapping of step ID to ``PlanStep`` for lookup.
            context_guard: Optional guard used to truncate oversized context.

        Returns:
            A formatted string with each dependency's result, or an empty
            string if there are no dependencies.
        """
        if not step.dependencies:
            return ""

        context_parts: list[str] = []
        for dep_id in step.dependencies:
            dep_step = step_index.get(dep_id)
            if dep_step is None:
                continue

            status_label = dep_step.status
            result_text = dep_step.result or "(no result)"
            context_parts.append(
                f"[{dep_id}] ({status_label}) {dep_step.task}\n"
                f"Result: {result_text}"
            )

        context_text = "\n\n".join(context_parts)

        # Truncate oversized dependency context.
        if context_guard and len(context_text) > context_guard.max_message_chars:
            context_text = (
                context_text[:context_guard.max_message_chars]
                + "\n[Dependency context truncated]"
            )

        return context_text
