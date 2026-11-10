"""Workflow execution engine — orchestrates node execution with concurrency.

Reuses the ``DAGExecutor`` pattern: topologically sorted nodes are launched
concurrently (bounded by a semaphore), with condition/classifier nodes
controlling which downstream branches are activated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Literal

from .nodes import EXECUTOR_REGISTRY, get_executor
from .parser import topological_sort
from .types import (
    ErrorStrategy,
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
    WorkflowNodeDef,
)
from .variable_store import VariableStore

logger = logging.getLogger(__name__)

# Asking whether the run is still this process's: tries, and the pause between.
_OWNERSHIP_TRIES = 3
_OWNERSHIP_RETRY_SECONDS = 0.2
# Steps that act outside the run or spend tokens. When ownership cannot be
# established they are not run; the others (Start, End, branches) are.
_SIDE_EFFECT_STEPS = frozenset(
    {
        NodeType.CONNECTOR,
        NodeType.MCP,
        NodeType.AGENT,
        NodeType.LLM,
        NodeType.KNOWLEDGE_RETRIEVAL,
        NodeType.HUMAN_INTERVENTION,
    }
)

# Version of the state a suspended run is continued from (``run_suspended``).
SUSPEND_STATE_VERSION = 1

# Maximum size for trace detail values (e.g. HTTP response body)
_TRACE_MAX_BYTES = 10 * 1024  # 10 KB


def _truncate(value: str, max_len: int = _TRACE_MAX_BYTES) -> str:
    """Truncate a string to *max_len* characters."""
    if len(value) <= max_len:
        return value
    return value[:max_len] + f"... (truncated, {len(value)} total chars)"


class WorkflowEngine:
    """Execute a parsed workflow blueprint with concurrent node execution.

    Parameters
    ----------
    max_concurrency:
        Maximum number of nodes executing in parallel.
    cancel_event:
        Async event that, when set, signals the engine to stop.
    env_vars:
        Encrypted env vars to inject into the variable store.
    """

    def __init__(
        self,
        max_concurrency: int = 5,
        cancel_event: asyncio.Event | None = None,
        env_vars: dict[str, str] | None = None,
        run_id: str = "",
        user_id: str = "",
        workflow_id: str = "",
        workflow_timeout_ms: int = 600_000,  # default 10 min
        trace_level: Literal["normal", "debug"] = "normal",
    ) -> None:
        self._max_concurrency = max_concurrency
        self._cancel_event = cancel_event
        self._env_vars = env_vars or {}
        self._run_id = run_id
        self._user_id = user_id
        self._workflow_id = workflow_id
        self._workflow_timeout_ms = workflow_timeout_ms  # 0 = no limit
        self._trace_level = trace_level
        from fim_one.core.model.usage import UsageTracker

        # One tracker shared by every node of this run so LLM/Agent token usage
        # accumulates into a single run total (billed to the workflow owner).
        self._usage_tracker = UsageTracker()

    async def execute_streaming(
        self,
        blueprint: WorkflowBlueprint,
        inputs: dict[str, Any] | None = None,
        context: ExecutionContext | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Execute the workflow and yield SSE events as they occur.

        Yields tuples of ``(event_name, event_data)`` for each progress update.

        Events emitted:
        - ``run_started``    - execution begins
        - ``run_resumed``    - execution continues from *resume_state*
        - ``node_started``   - a node begins execution
        - ``node_completed`` - a node finishes successfully
        - ``node_skipped``   - a node was skipped (branch not active)
        - ``node_failed``    - a node failed
        - ``node_waiting``   - a node asked a human for a decision
        - ``run_completed``  - all nodes done, final result
        - ``run_failed``     - execution failed globally
        - ``run_suspended``  - nothing more can run until a decision is made

        A step that needs a human's decision returns ``NodeStatus.WAITING``
        with what it waits on. Other branches keep running; once nothing
        else can, the last event is ``run_suspended``, whose ``state``
        holds everything needed to go on: pass it back as *resume_state*
        (with the same blueprint) and the waiting steps are run again to
        pick up their decisions. Nothing waits in this process.
        """
        start_time = time.time()
        store = VariableStore(env_vars=self._env_vars)
        resumed: dict[str, Any] = resume_state or {}
        # Active time and tokens of the part of the run before it waited.
        base_active_ms = int(resumed.get("active_ms") or 0)
        base_tokens = int(resumed.get("total_tokens") or 0)

        if resume_state is not None:
            await store.set_many(dict(resumed.get("variables") or {}))
            yield "run_resumed", {
                "run_id": self._run_id,
                "workflow_id": self._workflow_id,
                "node_count": len(blueprint.nodes),
            }
        else:
            # Inject inputs into the store under "input." namespace
            if inputs:
                for key, value in inputs.items():
                    await store.set(f"input.{key}", value)

            # Emit run_started event
            yield "run_started", {
                "run_id": self._run_id,
                "workflow_id": self._workflow_id,
                "node_count": len(blueprint.nodes),
            }

        # Build graph structures
        node_index = {n.id: n for n in blueprint.nodes}
        topo_order = topological_sort(blueprint)

        # Build adjacency and reverse adjacency
        outgoing_edges: dict[str, list[WorkflowEdgeDef]] = defaultdict(list)
        incoming_edges: dict[str, list[WorkflowEdgeDef]] = defaultdict(list)
        for edge in blueprint.edges:
            outgoing_edges[edge.source].append(edge)
            incoming_edges[edge.target].append(edge)

        # Track which edges are "active" (all edges start active)
        active_edges: set[str] = {e.id for e in blueprint.edges}

        # Track node statuses
        node_status: dict[str, NodeStatus] = {
            n.id: NodeStatus.PENDING for n in blueprint.nodes
        }
        node_results: dict[str, NodeResult] = {}

        # Semaphore for concurrency control
        semaphore = asyncio.Semaphore(self._max_concurrency)

        def _active_elapsed_ms() -> int:
            """Time the run has spent executing. The time a suspended run
            spends waiting for a decision is not in it: an approval may wait
            for hours, the whole-run limit is minutes."""
            return base_active_ms + int((time.time() - start_time) * 1000)

        def _total_tokens() -> int:
            return base_tokens + int(self._usage_tracker.get_summary().total_tokens or 0)

        # Determine dependencies: for each node, which nodes must complete first
        predecessors: dict[str, set[str]] = defaultdict(set)
        for edge in blueprint.edges:
            predecessors[edge.target].add(edge.source)

        completed: set[str] = set()
        pending: set[str] = set(topo_order)
        running_tasks: dict[asyncio.Task[None], str] = {}
        # Steps waiting for a decision, by node id: what each waits on.
        waiting: dict[str, dict[str, Any]] = {}
        # Steps that were waiting when the run was suspended; run again first.
        resumed_waiting: dict[str, dict[str, Any]] = {}

        # One context for every step of the run.
        ctx = context or ExecutionContext(
            run_id=self._run_id,
            user_id=self._user_id,
            workflow_id=self._workflow_id,
            env_vars=self._env_vars,
            cancel_event=self._cancel_event,
        )
        # Share this run's usage tracker with whatever context is used
        # (an externally-supplied one, e.g. the webhook path, included) so
        # token accounting accumulates into a single run total.
        if ctx.usage_tracker is None:
            ctx.usage_tracker = self._usage_tracker

        if resume_state is not None:
            known = set(node_status)
            for nid, value in (resumed.get("node_status") or {}).items():
                if nid in known:
                    node_status[nid] = NodeStatus(value)
            for nid, saved in (resumed.get("node_results") or {}).items():
                if nid in known:
                    node_results[nid] = _result_from_state(nid, saved)
            active_edges = set(resumed.get("active_edges") or []) & active_edges
            completed = set(resumed.get("completed") or []) & known
            resumed_waiting = {
                nid: dict(record)
                for nid, record in (resumed.get("waiting") or {}).items()
                if nid in known
            }
            pending = set(topo_order) - completed - set(resumed_waiting)
            ctx.suspensions = {nid: dict(record) for nid, record in resumed_waiting.items()}

        # Event queue for streaming
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def _emit(event_type: str, data: dict[str, Any]) -> None:
            """Put an event with an auto-injected wall-clock timestamp (ms)."""
            data.setdefault("ts", int(time.time() * 1000))
            await event_queue.put((event_type, data))

        def _can_execute(nid: str) -> bool:
            """Check if a node is ready to execute."""
            # All predecessor nodes must be done (completed, failed, or skipped)
            for pred in predecessors.get(nid, set()):
                if pred not in completed:
                    return False

            # Check if at least one incoming edge is active
            node_incoming = incoming_edges.get(nid, [])
            if not node_incoming:
                # No incoming edges (e.g. Start node) — always ready
                return True

            return any(e.id in active_edges for e in node_incoming)

        def _should_skip(nid: str) -> bool:
            """Check if a node should be skipped."""
            node_incoming = incoming_edges.get(nid, [])
            if not node_incoming:
                return False
            return all(e.id not in active_edges for e in node_incoming)

        def _has_stop_workflow_failure() -> bool:
            """Return True if any FAILED node used STOP_WORKFLOW strategy."""
            for nid, st in node_status.items():
                if st == NodeStatus.FAILED:
                    n = node_index[nid]
                    if n.error_strategy == ErrorStrategy.STOP_WORKFLOW:
                        return True
            return False

        # Set once the run's record turned out to be closed by someone else.
        abandoned = {"run": False}

        # Failed nodes whose error strategy (CONTINUE / FAIL_BRANCH) says the
        # run should carry on.  They don't fail the run by themselves.
        tolerated_failures: set[str] = set(resumed.get("tolerated_failures") or [])

        def _suspend_state() -> dict[str, Any]:
            """Everything a later ``execute_streaming`` needs to go on.

            The author's env vars are left out: they are secrets, and the
            run service supplies them again.
            """
            return {
                "version": SUSPEND_STATE_VERSION,
                "variables": {
                    k: v for k, v in store.snapshot_sync().items() if not k.startswith("env.")
                },
                "node_status": {nid: st.value for nid, st in node_status.items()},
                "node_results": {
                    # A step's output is in the variables already; kept here
                    # only for End steps, whose outputs are the run's.
                    nid: _result_to_state(
                        result, keep_output=node_index[nid].type == NodeType.END
                    )
                    for nid, result in node_results.items()
                },
                "active_edges": sorted(active_edges),
                "completed": sorted(completed),
                "tolerated_failures": sorted(tolerated_failures),
                "waiting": {nid: dict(record) for nid, record in waiting.items()},
                "active_ms": _active_elapsed_ms(),
                "total_tokens": _total_tokens(),
            }

        async def _drop_waiting(reason: str) -> None:
            """The run is ending: steps still waiting for a decision are skipped."""
            for nid in sorted(waiting):
                if node_status.get(nid) == NodeStatus.WAITING:
                    node_status[nid] = NodeStatus.SKIPPED
                    await _emit("node_skipped", {"node_id": nid, "reason": reason})
            waiting.clear()

        def _branch_cut_off_by(failed_nid: str) -> set[str]:
            """Nodes that lose every active incoming path once *failed_nid*'s
            outgoing edges are deactivated.  A node that is still reachable
            through another branch (e.g. a join after a parallel fan-out) is
            not in the set and keeps running."""
            dead: set[str] = {failed_nid}
            for tnid in topo_order:
                if tnid in dead or node_status.get(tnid) != NodeStatus.PENDING:
                    continue
                node_incoming = incoming_edges.get(tnid, [])
                if node_incoming and all(
                    e.id not in active_edges or e.source in dead
                    for e in node_incoming
                ):
                    dead.add(tnid)
            dead.discard(failed_nid)
            return dead

        async def _handle_node_failure(
            nid: str, result: NodeResult
        ) -> None:
            """Apply the error strategy for a failed node."""
            node = node_index[nid]
            strategy = node.error_strategy

            if strategy == ErrorStrategy.CONTINUE:
                # Treat as "completed" for dependency resolution — downstream
                # nodes will still run (they can inspect the failure via the
                # variable store if they choose to).
                node_status[nid] = NodeStatus.FAILED
                tolerated_failures.add(nid)
                await _emit(
                    "node_failed",
                    {
                        "node_id": nid,
                        "node_type": node.type.value,
                        "error": result.error,
                        "input_preview": result.input_preview,
                        "duration_ms": result.duration_ms,
                        "error_strategy": "continue",
                    },
                )

            elif strategy == ErrorStrategy.FAIL_BRANCH:
                # Cut this node's branch: deactivate its outgoing edges so the
                # normal skip cascade drops every node reachable *only*
                # through it.  Nodes that still have another active incoming
                # path (a join after parallel branches, the shared End node)
                # keep running.  Nothing downstream can be running yet, since
                # a node only starts once all its predecessors are done.
                node_status[nid] = NodeStatus.FAILED
                tolerated_failures.add(nid)
                downstream = _branch_cut_off_by(nid)
                for edge in outgoing_edges.get(nid, []):
                    active_edges.discard(edge.id)

                await _emit(
                    "node_failed",
                    {
                        "node_id": nid,
                        "node_type": node.type.value,
                        "error": result.error,
                        "input_preview": result.input_preview,
                        "duration_ms": result.duration_ms,
                        "error_strategy": "fail_branch",
                        "skipped_downstream": sorted(downstream),
                    },
                )

            else:
                # STOP_WORKFLOW (default) — just mark failed; the orchestrator
                # will detect it and skip all remaining pending nodes.
                node_status[nid] = NodeStatus.FAILED
                await _emit(
                    "node_failed",
                    {
                        "node_id": nid,
                        "node_type": node.type.value,
                        "error": result.error,
                        "input_preview": result.input_preview,
                        "duration_ms": result.duration_ms,
                        "error_strategy": "stop_workflow",
                    },
                )

        async def _execute_once(
            node: WorkflowNodeDef,
            executor: Any,
            ctx: ExecutionContext,
        ) -> NodeResult:
            """Run an executor once, enforcing the per-node timeout.

            A step that waits for a human does not wait here: it asks,
            returns ``WAITING`` and is run again once the answer is in.
            """
            timeout_sec = node.timeout_ms / 1000
            try:
                return await asyncio.wait_for(
                    executor.execute(node, store, ctx),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Node execution timed out after {timeout_sec:.0f}s",
                )

        async def _run_node(nid: str) -> None:
            """Execute a single node with retry, semaphore, and per-node timeout."""
            async with semaphore:
                await _run_node_inner(nid)

        async def _still_ours() -> bool | None:
            """Whether the run's record is still this process's to write.

            Asked before each step and each retry, so a run closed by
            someone else stops before its next side effect, not at the
            next heartbeat. ``None`` when the question could not be
            answered (the database did not respond, three tries a short
            pause apart): the caller decides by what the step would do.
            """
            if ctx.still_owned is None or abandoned["run"]:
                return not abandoned["run"]
            for attempt in range(_OWNERSHIP_TRIES):
                try:
                    owned = bool(await ctx.still_owned())
                except Exception:
                    if attempt + 1 < _OWNERSHIP_TRIES:
                        await asyncio.sleep(_OWNERSHIP_RETRY_SECONDS)
                        continue
                    logger.exception("Could not check whether run %s is still ours", self._run_id)
                    return None
                if not owned:
                    abandoned["run"] = True
                return owned
            return None

        async def _run_node_inner(nid: str) -> None:
            """Inner node execution logic (retry, timeout, event emission)."""
            node = node_index[nid]
            executor = get_executor(node.type)

            ours = await _still_ours()
            if ours is False:
                node_status[nid] = NodeStatus.SKIPPED
                await _emit(
                    "node_skipped",
                    {"node_id": nid, "reason": "The run was closed while it was in progress"},
                )
                return
            if ours is None and node.type in _SIDE_EFFECT_STEPS:
                # Not knowing whether the run is still ours, a step that
                # acts on the outside world (or spends tokens) is not run:
                # it fails, like any step that could not do its work.
                unknown = NodeResult(
                    node_id=nid,
                    status=NodeStatus.FAILED,
                    error=(
                        "The step was not run: the server could not confirm that this run "
                        "is still its own (the database did not answer)."
                    ),
                )
                node_results[nid] = unknown
                await _handle_node_failure(nid, unknown)
                return

            # Retry config from node data (default: no retry)
            retry_count = max(int(node.data.get("retry_count", 0)), 0)
            retry_delay_ms = max(int(node.data.get("retry_delay_ms", 1000)), 0)

            # Capture inputs from predecessor nodes for debugging
            pred_ids = predecessors.get(nid, set())
            input_preview = await _capture_input_preview(store, pred_ids)

            await _emit(
                # A step run again after its decision came in has started before.
                "node_resumed" if nid in resumed_waiting else "node_started",
                {
                    "node_id": nid,
                    "node_type": node.type.value,
                    "input_preview": input_preview,
                },
            )

            try:
                result = await _execute_once(node, executor, ctx)

                # Retry loop: attempt up to retry_count additional times
                attempt = 0
                while (
                    result.status == NodeStatus.FAILED
                    and attempt < retry_count
                    and not result.no_retry
                ):
                    attempt += 1
                    # Exponential backoff: delay * 2^(attempt-1)
                    delay_sec = (retry_delay_ms / 1000) * (2 ** (attempt - 1))
                    # Cap at 60 seconds
                    delay_sec = min(delay_sec, 60.0)

                    await _emit(
                        "node_retrying",
                        {
                            "node_id": nid,
                            "node_type": node.type.value,
                            "attempt": attempt,
                            "max_retries": retry_count,
                            "delay_ms": int(delay_sec * 1000),
                            "previous_error": result.error,
                        },
                    )

                    # Check for cancellation before waiting
                    if self._cancel_event and self._cancel_event.is_set():
                        break

                    await asyncio.sleep(delay_sec)

                    if self._cancel_event and self._cancel_event.is_set():
                        break

                    if await _still_ours() is not True:
                        break

                    # A new attempt asks again; it does not reuse a
                    # decision made on the attempt that failed.
                    ctx.suspensions.pop(nid, None)
                    result = await _execute_once(node, executor, ctx)

                # Attach the input snapshot to the result for persistence
                result.input_preview = input_preview

                if result.status == NodeStatus.WAITING:
                    record = dict(result.suspension or {})
                    node_status[nid] = NodeStatus.WAITING
                    waiting[nid] = record
                    ctx.suspensions[nid] = record
                    await _emit(
                        "node_waiting",
                        {
                            "node_id": nid,
                            "node_type": node.type.value,
                            "status": "waiting",
                            "input_preview": input_preview,
                            "waiting_for": record,
                        },
                    )
                    return

                waiting.pop(nid, None)
                ctx.suspensions.pop(nid, None)
                node_results[nid] = result

                # Build debug trace data if in debug mode
                if self._trace_level == "debug":
                    trace: dict[str, Any] = {}
                    try:
                        trace["variable_snapshot"] = store.to_dict()
                    except Exception:
                        trace["variable_snapshot"] = None
                    if hasattr(result, "_trace_details"):
                        trace.update(result._trace_details)
                    result._trace = trace  # type: ignore[attr-defined]

                if result.status == NodeStatus.COMPLETED:
                    node_status[nid] = NodeStatus.COMPLETED

                    # Handle conditional branching
                    if result.active_handles is not None:
                        # Deactivate edges that don't match the active handles
                        for edge in outgoing_edges.get(nid, []):
                            if edge.source_handle not in result.active_handles:
                                active_edges.discard(edge.id)

                    await _emit(
                        "node_completed",
                        {
                            "node_id": nid,
                            "node_type": node.type.value,
                            "status": "completed",
                            "input_preview": input_preview,
                            "output_preview": _preview(result.output),
                            "duration_ms": result.duration_ms,
                            "retries_used": attempt if retry_count > 0 else 0,
                        },
                    )
                else:
                    await _handle_node_failure(nid, result)

            except asyncio.CancelledError:
                node_status[nid] = NodeStatus.FAILED
                await _emit(
                    "node_failed",
                    {
                        "node_id": nid,
                        "error": "Cancelled",
                        "input_preview": input_preview,
                    },
                )
                raise
            except Exception as exc:
                node_status[nid] = NodeStatus.FAILED
                result = NodeResult(
                    node_id=nid,
                    status=NodeStatus.FAILED,
                    error=str(exc),
                    input_preview=input_preview,
                )
                node_results[nid] = result
                await _handle_node_failure(nid, result)

        async def _orchestrator() -> None:
            """Main orchestration loop — runs until all nodes are done."""
            _workflow_timed_out = False
            _cancelled = False
            _suspended = False
            try:
                # Continuing a suspended run: the steps that were waiting
                # go first, each to pick up its decision.
                for nid in sorted(resumed_waiting):
                    node_status[nid] = NodeStatus.RUNNING
                    running_tasks[asyncio.create_task(_run_node(nid))] = nid

                while pending or running_tasks or waiting:
                    # Check for workflow-level timeout
                    if self._workflow_timeout_ms > 0:
                        elapsed_ms = _active_elapsed_ms()
                        if elapsed_ms >= self._workflow_timeout_ms:
                            logger.warning(
                                "Workflow %s timed out after %dms",
                                self._workflow_id, elapsed_ms,
                            )
                            for nid in sorted(pending):
                                node_status[nid] = NodeStatus.SKIPPED
                                await _emit(
                                    "node_skipped",
                                    {"node_id": nid, "reason": "Workflow timeout exceeded"},
                                )
                            pending.clear()
                            for task in running_tasks:
                                task.cancel()
                            if running_tasks:
                                await asyncio.gather(
                                    *running_tasks, return_exceptions=True
                                )
                            running_tasks.clear()
                            await _drop_waiting("Workflow timeout exceeded")
                            await _emit(
                                "run_failed",
                                {
                                    "status": "failed",
                                    "error": f"Workflow execution timed out after {self._workflow_timeout_ms}ms",
                                    "error_code": "workflow_run_timed_out",
                                    "duration_ms": elapsed_ms,
                                    "total_tokens": _total_tokens(),
                                },
                            )
                            _workflow_timed_out = True
                            break

                    # Check for cancellation (asked for here, or the run's
                    # record was closed from elsewhere)
                    if abandoned["run"] or (self._cancel_event and self._cancel_event.is_set()):
                        for nid in sorted(pending):
                            node_status[nid] = NodeStatus.SKIPPED
                            await _emit(
                                "node_skipped",
                                {"node_id": nid, "reason": "Execution cancelled"},
                            )
                        pending.clear()
                        # Cancel running tasks
                        for task in running_tasks:
                            task.cancel()
                        if running_tasks:
                            await asyncio.gather(
                                *running_tasks, return_exceptions=True
                            )
                        running_tasks.clear()
                        await _drop_waiting("Execution cancelled")
                        _cancelled = True
                        break

                    # M4: Check STOP_WORKFLOW BEFORE launching new nodes to
                    # prevent a race where new tasks start in the same
                    # iteration before the failure is detected.
                    if _has_stop_workflow_failure() and pending:
                        for remaining_nid in sorted(pending):
                            node_status[remaining_nid] = NodeStatus.SKIPPED
                            completed.add(remaining_nid)
                            await _emit(
                                "node_skipped",
                                {
                                    "node_id": remaining_nid,
                                    "reason": "Stopped by upstream node failure (stop_workflow)",
                                },
                            )
                        pending.clear()
                        continue

                    # Find ready nodes
                    ready: list[str] = []
                    skip: list[str] = []
                    for nid in sorted(pending):
                        if _should_skip(nid):
                            skip.append(nid)
                        elif _can_execute(nid):
                            ready.append(nid)

                    # Skip nodes on inactive branches
                    for nid in skip:
                        pending.discard(nid)
                        completed.add(nid)
                        node_status[nid] = NodeStatus.SKIPPED

                        # Deactivate all outgoing edges from skipped nodes so
                        # downstream nodes cascade-skip too.  Previously this
                        # only applied to branching node types, but any skipped
                        # node must propagate: if an LLM on a dead branch is
                        # skipped, its downstream End node must also be skipped.
                        for edge in outgoing_edges.get(nid, []):
                            active_edges.discard(edge.id)

                        await _emit(
                            "node_skipped",
                            {"node_id": nid, "reason": "Branch not active"},
                        )

                    # Launch ready nodes
                    for nid in ready:
                        pending.discard(nid)
                        node_status[nid] = NodeStatus.RUNNING
                        task = asyncio.create_task(_run_node(nid))
                        running_tasks[task] = nid

                    # Skipping nodes changes the completed set, which may
                    # unlock additional nodes.  Re-evaluate before assuming
                    # deadlock.
                    if skip and not ready and not running_tasks and pending:
                        continue

                    if not running_tasks:
                        if waiting and not _has_stop_workflow_failure():
                            # Everything that can run has; the rest waits
                            # for a human's decision.
                            _suspended = True
                            break
                        if pending:
                            # Deadlock — nodes waiting for predecessors that can't finish
                            for nid in sorted(pending):
                                node_status[nid] = NodeStatus.FAILED
                                await _emit(
                                    "node_failed",
                                    {
                                        "node_id": nid,
                                        "error": "Deadlock: dependencies cannot complete",
                                    },
                                )
                            pending.clear()
                        break

                    # Wait for at least one task to complete.
                    # If a workflow timeout is set, cap the wait so the loop
                    # can re-check the timeout at the top of the next iteration.
                    wait_timeout: float | None = None
                    if self._workflow_timeout_ms > 0:
                        remaining_ms = self._workflow_timeout_ms - _active_elapsed_ms()
                        wait_timeout = max(remaining_ms / 1000.0, 0.01)

                    done, _ = await asyncio.wait(
                        running_tasks.keys(),
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=wait_timeout,
                    )

                    # If wait timed out (no tasks finished), loop back to
                    # check the workflow timeout at the top of the loop.
                    if not done:
                        continue

                    for finished_task in done:
                        nid = running_tasks.pop(finished_task)
                        if node_status.get(nid) != NodeStatus.WAITING:
                            # A waiting step is not done: what follows it
                            # stays pending.
                            completed.add(nid)

                        exc = finished_task.exception()
                        if exc is not None and not isinstance(
                            exc, asyncio.CancelledError
                        ):
                            logger.exception(
                                "Unexpected error in node %s", nid, exc_info=exc
                            )
                            node_status[nid] = NodeStatus.FAILED
                            continue

                    # After processing completed tasks, check if a
                    # STOP_WORKFLOW failure has occurred.  If so, skip all
                    # remaining pending nodes immediately.
                    if _has_stop_workflow_failure() and pending:
                        for remaining_nid in sorted(pending):
                            node_status[remaining_nid] = NodeStatus.SKIPPED
                            completed.add(remaining_nid)
                            await _emit(
                                "node_skipped",
                                {
                                    "node_id": remaining_nid,
                                    "reason": "Stopped by upstream node failure (stop_workflow)",
                                },
                            )
                        pending.clear()

                # Determine final status (skip if already handled by timeout)
                if _workflow_timed_out:
                    return

                if _suspended:
                    await _emit(
                        "run_suspended",
                        {
                            "status": "waiting",
                            "waiting": [
                                {"node_id": nid, **record}
                                for nid, record in sorted(waiting.items())
                            ],
                            "state": _suspend_state(),
                            "duration_ms": _active_elapsed_ms(),
                            "total_tokens": _total_tokens(),
                        },
                    )
                    return

                elapsed_ms = _active_elapsed_ms()
                if _cancelled:
                    await _emit(
                        "run_completed",
                        {
                            "status": "cancelled",
                            "error": "Execution cancelled",
                            "error_code": "workflow_run_cancelled",
                            "duration_ms": elapsed_ms,
                            "total_tokens": _total_tokens(),
                        },
                    )
                    return

                # The run ends here (a step failed with stop_workflow while
                # another was waiting): nobody is asked any more.
                await _drop_waiting("Stopped by upstream node failure (stop_workflow)")
                failed_nodes = [
                    nid
                    for nid, st in node_status.items()
                    if st == NodeStatus.FAILED and nid not in tolerated_failures
                ]
                handled_failures = sorted(
                    nid
                    for nid in tolerated_failures
                    if node_status.get(nid) == NodeStatus.FAILED
                )
                end_reached = any(
                    node.type == NodeType.END
                    and node_status.get(node.id) == NodeStatus.COMPLETED
                    for node in blueprint.nodes
                )

                # Collect outputs from End nodes
                outputs: dict[str, Any] = {}
                for node in blueprint.nodes:
                    if node.type == NodeType.END and node.id in node_results:
                        nr = node_results[node.id]
                        if nr.output:
                            if isinstance(nr.output, dict):
                                outputs.update(nr.output)
                            else:
                                outputs[node.id] = nr.output

                if failed_nodes:
                    await _emit(
                        "run_failed",
                        {
                            "status": "failed",
                            "error": f"Nodes failed: {failed_nodes}",
                            "error_code": "workflow_step_failed",
                            "duration_ms": elapsed_ms,
                            "total_tokens": _total_tokens(),
                        },
                    )
                elif handled_failures and not end_reached:
                    # Every failure was tolerated, but together they cut off
                    # every path to an End node, so the run produced nothing.
                    await _emit(
                        "run_failed",
                        {
                            "status": "failed",
                            "error": (
                                "No End node was reached; nodes failed: "
                                f"{handled_failures}"
                            ),
                            "error_code": "workflow_step_failed",
                            "duration_ms": elapsed_ms,
                            "total_tokens": _total_tokens(),
                        },
                    )
                else:
                    completed_payload: dict[str, Any] = {
                        "status": "completed",
                        "outputs": outputs,
                        "duration_ms": elapsed_ms,
                        "total_tokens": _total_tokens(),
                    }
                    if handled_failures:
                        completed_payload["failed_nodes"] = handled_failures
                    await _emit("run_completed", completed_payload)

            except asyncio.CancelledError:
                elapsed_ms = _active_elapsed_ms()
                await _emit(
                    "run_completed",
                    {
                        "status": "cancelled",
                        "error": "Execution cancelled",
                        "error_code": "workflow_run_cancelled",
                        "duration_ms": elapsed_ms,
                        "total_tokens": _total_tokens(),
                    },
                )
            except Exception as exc:
                elapsed_ms = _active_elapsed_ms()
                logger.exception("Workflow orchestrator failed")
                await _emit(
                    "run_failed",
                    {
                        "status": "failed",
                        "error": str(exc),
                        "error_code": "workflow_run_failed",
                        "duration_ms": elapsed_ms,
                        "total_tokens": _total_tokens(),
                    },
                )
            finally:
                # Signal end of events
                await event_queue.put(("__done__", {}))

        # Start orchestrator as a background task
        orchestrator_task = asyncio.create_task(_orchestrator())

        try:
            while True:
                event_name, event_data = await event_queue.get()
                if event_name == "__done__":
                    break
                yield event_name, event_data
        finally:
            if not orchestrator_task.done():
                orchestrator_task.cancel()
                try:
                    await orchestrator_task
                except asyncio.CancelledError:
                    pass


def _result_to_state(result: NodeResult, *, keep_output: bool) -> dict[str, Any]:
    """A step's result as stored in a suspended run's state."""
    return {
        "status": result.status.value,
        "output": result.output if keep_output else None,
        "error": result.error,
        "active_handles": result.active_handles,
        "duration_ms": result.duration_ms,
        "input_preview": result.input_preview,
    }


def _result_from_state(node_id: str, saved: dict[str, Any]) -> NodeResult:
    return NodeResult(
        node_id=node_id,
        status=NodeStatus(saved.get("status", NodeStatus.COMPLETED.value)),
        output=saved.get("output"),
        error=saved.get("error"),
        active_handles=saved.get("active_handles"),
        duration_ms=int(saved.get("duration_ms") or 0),
        input_preview=saved.get("input_preview"),
    )


def _preview(value: Any, max_len: int = 200) -> str:
    """Create a short preview string from an output value."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_len]
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
        return s[:max_len]
    except Exception:
        return str(value)[:max_len]


async def _capture_input_preview(
    store: VariableStore,
    predecessor_ids: set[str],
    max_len: int = 500,
) -> str:
    """Snapshot the predecessor outputs from the variable store for debugging.

    Collects all variables under each predecessor's namespace (``{node_id}.*``)
    and returns a truncated JSON string.  Environment variables are excluded.
    """
    if not predecessor_ids:
        return "{}"
    inputs: dict[str, Any] = {}
    for pred_id in sorted(predecessor_ids):
        node_outputs = await store.get_node_outputs(pred_id)
        if node_outputs:
            inputs[pred_id] = node_outputs
    # Also include workflow-level inputs (input.* namespace)
    snapshot = await store.snapshot()
    input_vars = {k: v for k, v in snapshot.items() if k.startswith("input.")}
    if input_vars:
        inputs["__input__"] = input_vars
    try:
        s = json.dumps(inputs, ensure_ascii=False, default=str)
        return s[:max_len]
    except Exception:
        return str(inputs)[:max_len]
