"""DAG executor that runs plan steps concurrently where possible.

The ``DAGExecutor`` respects the dependency edges in an ``ExecutionPlan`` and
launches independent steps in parallel (up to a configurable concurrency
limit) using ``asyncio``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.types import Action
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.model.base import BaseLLM
from fim_one.core.model.registry import ModelRegistry
from fim_one.core.model.usage import UsageSummary
from fim_one.core.tool import ToolRegistry

from .tool_cache import ToolCache, wrap_tools_with_cache
from .types import ExecutionPlan, PlanStep, StepOutput

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
        max_concurrency: int = int(os.getenv("DAG_MAX_CONCURRENCY", "8")),
        model_registry: ModelRegistry | None = None,
        context_guard: ContextGuard | None = None,
        original_goal: str | None = None,
        stop_event: asyncio.Event | None = None,
        step_timeout: float = int(os.getenv("DAG_STEP_TIMEOUT", "600")),
        enable_tool_cache: bool = True,
        verify_llm: BaseLLM | None = None,
        enable_citation_verification: bool | None = None,
    ) -> None:
        self._agent = agent
        self._max_concurrency = max_concurrency
        self._model_registry = model_registry
        self._context_guard = context_guard
        self._original_goal = original_goal
        self._stop_event = stop_event
        self._step_timeout = step_timeout
        self._enable_tool_cache = enable_tool_cache
        self._cached_tool_registry: ToolRegistry | None = None
        self._verify_llm = verify_llm
        self._enable_citation_verification = (
            enable_citation_verification
            if enable_citation_verification is not None
            else os.getenv("DAG_CITATION_VERIFICATION", "true").lower()
            in ("1", "true", "yes")
        )
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

        # Tool cache for this execution — scoped to one execute() call.
        tool_cache: ToolCache | None = None
        if self._enable_tool_cache:
            tool_cache = ToolCache()
            original_tools = self._agent.tools.list_tools()
            cached_tools = wrap_tools_with_cache(original_tools, tool_cache)
            cached_registry = ToolRegistry()
            for tool in cached_tools:
                cached_registry.register(tool)
            self._cached_tool_registry = cached_registry
        else:
            self._cached_tool_registry = None

        semaphore = asyncio.Semaphore(self._max_concurrency)
        step_index = {step.id: step for step in plan.steps}
        pending_ids = {step.id for step in plan.steps}
        completed_ids: set[str] = set()
        failed_ids: set[str] = set()
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

                # Cascade failures: if any dependency of a pending step has
                # failed, mark that step as failed too without running it.
                # Repeat until no more steps can be cascade-failed (a step
                # cascade-failed in this pass may unblock further cascades).
                cascade_changed = True
                while cascade_changed:
                    cascade_changed = False
                    for sid in sorted(pending_ids):
                        step = step_index[sid]
                        failed_deps = [
                            dep for dep in step.dependencies if dep in failed_ids
                        ]
                        if failed_deps:
                            cascade_changed = True
                            pending_ids.discard(sid)
                            step.status = "failed"
                            dep_list = ", ".join(f"'{d}'" for d in failed_deps)
                            step.result = StepOutput(
                                summary=f"Skipped: dependency step {dep_list} failed"
                            )
                            step.completed_at = time.time()
                            if step.started_at is not None:
                                step.duration = round(
                                    step.completed_at - step.started_at, 2,
                                )
                            completed_ids.add(sid)
                            failed_ids.add(sid)
                            self._notify(sid, "completed", {
                                "task": step.task,
                                "status": "failed",
                                "result": step.result.summary,
                            })
                            logger.info(
                                "Step '%s' cascade-failed due to failed "
                                "dependency %s",
                                sid,
                                dep_list,
                            )

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
                            step_index[sid].result = StepOutput(
                                summary="Step could not run: one or more dependencies failed."
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
                        step_index[sid].result = StepOutput(summary=f"Unexpected error: {exc}")

                    completed_ids.add(sid)
                    step = step_index[sid]
                    if step.status == "failed":
                        failed_ids.add(sid)
                    completed_data: dict[str, Any] = {
                        "task": step.task,
                        "status": step.status,
                        "result": step.result.summary if step.result else None,
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

        if tool_cache:
            logger.info(
                "ToolCache stats: %d hits, %d misses",
                tool_cache.hits, tool_cache.misses,
            )

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
                step.result = StepOutput(summary=f"Step execution timed out after {self._step_timeout}s")
                step.completed_at = time.time()
                if step.started_at is not None:
                    step.duration = round(step.completed_at - step.started_at, 2)
                logger.error(
                    "Step '%s' timed out after %ds", step.id, self._step_timeout,
                )

    def _resolve_agent(self, step: PlanStep) -> ReActAgent:
        """Select the appropriate agent for a step based on its model_hint.

        Model selection hierarchy:
        - ``model_hint="fast"``  → fast/lightweight model (simple lookups)
        - ``model_hint="reasoning"`` → strongest model (deep analysis)
        - ``model_hint=None`` (default) → general/capable model via registry,
          falling back to the constructor-provided agent only when no registry
          is available.  This ensures steps get a capable model by default
          rather than the fast model used for DAG infrastructure tasks.

        When the resolved LLM exposes a ``context_size``, a model-aware
        ``ContextGuard`` is created with a budget computed from that model's
        actual context window, instead of using the global budget (which may
        be far too large for small-context models).

        Args:
            step: The plan step to resolve an agent for.

        Returns:
            A ``ReActAgent`` instance to execute the step.
        """
        # Use cached tool registry if available for this execution.
        tools_to_use = (
            self._cached_tool_registry
            if self._cached_tool_registry is not None
            else self._agent.tools
        )

        def _agent_with_llm(llm: BaseLLM) -> ReActAgent:
            """Build a step agent with the given LLM and shared config."""
            context_guard = self._build_model_aware_guard(llm)
            return ReActAgent(
                llm=llm,
                tools=tools_to_use,
                system_prompt=self._agent.system_prompt_override,
                extra_instructions=self._agent.extra_instructions,
                max_iterations=self._agent.max_iterations,
                context_guard=context_guard or self._agent.context_guard,
            )

        # No registry → fall back to the constructor-provided agent.
        if self._model_registry is None:
            if self._cached_tool_registry is not None:
                return _agent_with_llm(self._agent._llm)
            return self._agent

        # Determine the role to look up.  When model_hint is None we use the
        # registry's default (general) model so that steps get a capable LLM
        # instead of the fast model that the executor was constructed with.
        role = step.model_hint  # "fast", "reasoning", or None

        try:
            if role:
                llm = self._model_registry.get_by_role(role)
            else:
                llm = self._model_registry.get_default()
        except KeyError:
            logger.debug(
                "No model registered for role '%s', using constructor agent",
                role,
            )
            if self._cached_tool_registry is not None:
                return _agent_with_llm(self._agent._llm)
            return self._agent

        # Compute a model-aware ContextGuard if the resolved LLM exposes
        # its context_size.  This prevents oversized prompts when the step
        # model has a smaller context window than the default fast model.
        context_guard = self._build_model_aware_guard(llm)

        return ReActAgent(
            llm=llm,
            tools=tools_to_use,
            system_prompt=self._agent.system_prompt_override,
            extra_instructions=self._agent.extra_instructions,
            max_iterations=self._agent.max_iterations,
            context_guard=context_guard,
        )

    def _build_model_aware_guard(self, llm: BaseLLM) -> ContextGuard | None:
        """Build a ContextGuard with a budget derived from *llm*'s context_size.

        If the LLM does not expose ``context_size``, falls back to the
        existing global ``ContextGuard`` passed at construction time.

        The budget formula mirrors ``_compute_input_budget`` from
        ``fim_one.web.deps``:
        ``context_size - max_output_tokens - system_prompt_reserve``.
        """
        if self._context_guard is None:
            return None

        model_ctx = getattr(llm, "context_size", None)
        if not isinstance(model_ctx, int):
            return self._context_guard

        # Derive max_output from the LLM's default_max_tokens if available,
        # otherwise use a sensible default.
        raw_max_output = getattr(llm, "_default_max_tokens", None)
        max_output = raw_max_output if isinstance(raw_max_output, int) else 64000
        system_prompt_reserve = 4000

        budget = model_ctx - max_output - system_prompt_reserve
        budget = max(budget, 4000)

        # If the computed budget matches the existing guard's default, reuse it.
        if budget == self._context_guard._default_budget:
            return self._context_guard

        logger.debug(
            "Model-aware ContextGuard for %s: context_size=%d, "
            "max_output=%d, budget=%d (global budget=%d)",
            getattr(llm, "model_id", "unknown"),
            model_ctx,
            max_output,
            budget,
            self._context_guard._default_budget,
        )

        return ContextGuard(
            compact_llm=self._context_guard._compact_llm,
            default_budget=budget,
            max_message_chars=self._context_guard.max_message_chars,
            usage_tracker=self._context_guard._usage_tracker,
            custom_compact_prompt=self._context_guard._custom_compact_prompt,
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
            step.result = StepOutput(summary=agent_result.answer)
            step.usage = agent_result.usage
            logger.info(
                "Step '%s' completed in %d iterations",
                step.id,
                agent_result.iterations,
            )

            # Post-step verification (opt-in via verify_llm)
            if self._verify_llm and step.status == "completed" and step.result:
                from fim_one.core.planner.step_verifier import verify_step

                verification = await verify_step(
                    task=step.task,
                    result_summary=step.result.summary,
                    llm=self._verify_llm,
                )
                self._notify(step.id, "verification", {
                    "passed": verification.passed,
                    "reason": verification.reason,
                })
                if not verification.passed:
                    logger.warning(
                        "Step %s failed verification: %s -- retrying",
                        step.id,
                        verification.reason,
                    )
                    retry_query = (
                        f"{query}\n\n"
                        f"[VERIFICATION FEEDBACK] Your previous answer was "
                        f"rejected: {verification.reason}\n"
                        f"Please try again and address this feedback."
                    )
                    try:
                        agent_result = await agent.run(
                            retry_query, on_iteration=_on_iteration,
                        )
                        step.result = StepOutput(summary=agent_result.answer)
                        if agent_result.usage and step.usage:
                            step.usage += agent_result.usage
                        elif agent_result.usage:
                            step.usage = agent_result.usage

                        re_verification = await verify_step(
                            task=step.task,
                            result_summary=step.result.summary if step.result else "",
                            llm=self._verify_llm,
                        )
                        self._notify(step.id, "re_verification", {
                            "passed": re_verification.passed,
                            "reason": re_verification.reason,
                        })
                        if not re_verification.passed:
                            step.status = "failed"
                            step.result = StepOutput(
                                summary=(
                                    f"Verification failed after retry: "
                                    f"{re_verification.reason}"
                                ),
                            )
                            logger.warning(
                                "Step '%s' failed re-verification: %s",
                                step.id,
                                re_verification.reason,
                            )
                    except Exception as retry_exc:
                        step.status = "failed"
                        step.result = StepOutput(
                            summary=f"Retry after verification failure also failed: {retry_exc}",
                        )
                        logger.warning(
                            "Retry after verification failure also failed: %s",
                            retry_exc,
                        )
            # Citation verification for high-accuracy domains (opt-in via env).
            if (
                step.status == "completed"
                and step.result
                and self._enable_citation_verification
            ):
                from fim_one.core.planner.citation_verifier import (
                    should_verify_citations,
                    verify_citations,
                )

                if should_verify_citations(step.task, step.result.summary):
                    # Use the registry's default (general) model for verification
                    # so we don't verify with the same weak model that wrote it.
                    verify_llm = self._verify_llm or self._agent._llm
                    if self._model_registry:
                        try:
                            verify_llm = self._model_registry.get_default()
                        except KeyError:
                            pass

                    cv_result = await verify_citations(
                        result_text=step.result.summary,
                        llm=verify_llm,
                    )
                    self._notify(step.id, "citation_verification", {
                        "passed": cv_result.passed,
                        "citations_found": cv_result.citations_found,
                        "issues": cv_result.issues,
                    })
                    if not cv_result.passed and cv_result.issues:
                        logger.warning(
                            "Step '%s' citation issues: %s — retrying",
                            step.id,
                            cv_result.issues,
                        )
                        issue_text = "\n".join(
                            f"- {issue}" for issue in cv_result.issues
                        )
                        retry_query = (
                            f"{query}\n\n"
                            f"[CITATION ACCURACY FEEDBACK] The following "
                            f"citation issues were found in your output:\n"
                            f"{issue_text}\n"
                            f"{cv_result.suggestions}\n\n"
                            f"Please correct these citations and regenerate "
                            f"the affected sections.  Double-check all article "
                            f"numbers and case references against the actual "
                            f"content you found via search."
                        )
                        try:
                            agent_result = await agent.run(
                                retry_query, on_iteration=_on_iteration,
                            )
                            step.result = StepOutput(summary=agent_result.answer)
                            if agent_result.usage and step.usage:
                                step.usage += agent_result.usage
                            elif agent_result.usage:
                                step.usage = agent_result.usage
                        except Exception as retry_exc:
                            logger.warning(
                                "Citation retry failed for step '%s': %s",
                                step.id,
                                retry_exc,
                            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            step.status = "failed"
            step.result = StepOutput(summary=f"{type(exc).__name__}: {exc}")
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

    # Multiplier applied to the normal truncation limit when a step result
    # contains structured content (legal citations, tables, code blocks) that
    # would lose critical information if truncated aggressively.
    _STRUCTURED_CONTENT_MULTIPLIER = float(
        os.getenv("DAG_STRUCTURED_CONTEXT_MULTIPLIER", "3.0")
    )

    @staticmethod
    def _has_structured_content(text: str) -> bool:
        """Detect whether *text* contains structured content that should
        receive a higher truncation budget.

        Checks for:
        - Chinese law citations  (《xxx》第x条)
        - Case references        ((20xx)xxx号)
        - Markdown tables        (|---|)
        - Code blocks            (```)
        - International statutes (Section/Article N)
        """
        _quick_check_len = min(len(text), 4000)
        sample = text[:_quick_check_len]
        # Fast string checks before compiling regexes.
        if "《" in sample and "条" in sample:
            return True
        if "|---" in sample or "| ---" in sample:
            return True
        if "```" in sample:
            return True
        if "Section " in sample or "Article " in sample:
            return True
        # Case reference pattern: （2025）xxx号
        if "号" in sample and ("（" in sample or "(" in sample):
            import re
            if re.search(r"[（(]\d{4}[）)]", sample):
                return True
        return False

    @classmethod
    def _build_step_context(
        cls,
        step: PlanStep,
        step_index: dict[str, PlanStep],
        context_guard: ContextGuard | None = None,
    ) -> str:
        """Gather results from a step's completed dependencies.

        When a dependency result contains structured content (legal citations,
        tables, code blocks), the truncation limit is multiplied by
        ``_STRUCTURED_CONTENT_MULTIPLIER`` to preserve critical details that
        would be lost by aggressive truncation.

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
        has_structured = False
        for dep_id in step.dependencies:
            dep_step = step_index.get(dep_id)
            if dep_step is None:
                continue

            status_label = dep_step.status
            result_text = dep_step.result.summary if dep_step.result else "(no result)"
            if not has_structured and cls._has_structured_content(result_text):
                has_structured = True
            context_parts.append(
                f"[{dep_id}] ({status_label}) {dep_step.task}\n"
                f"Result: {result_text}"
            )

        context_text = "\n\n".join(context_parts)

        # Truncate oversized dependency context.  Structured content gets a
        # higher budget to avoid losing legal citations, table data, etc.
        if context_guard:
            limit = context_guard.max_message_chars
            if has_structured:
                limit = int(limit * cls._STRUCTURED_CONTENT_MULTIPLIER)
                logger.debug(
                    "Structured content detected in deps of step '%s' — "
                    "using extended context limit %d (base %d × %.1f)",
                    step.id,
                    limit,
                    context_guard.max_message_chars,
                    cls._STRUCTURED_CONTENT_MULTIPLIER,
                )
            if len(context_text) > limit:
                context_text = (
                    context_text[:limit]
                    + "\n[Dependency context truncated]"
                )

        return context_text
