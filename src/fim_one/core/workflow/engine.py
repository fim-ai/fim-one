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
from typing import Any

from .nodes import EXECUTOR_REGISTRY, get_executor
from .parser import topological_sort
from .types import (
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
)
from .variable_store import VariableStore

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._max_concurrency = max_concurrency
        self._cancel_event = cancel_event
        self._env_vars = env_vars or {}
        self._run_id = run_id
        self._user_id = user_id
        self._workflow_id = workflow_id

    async def execute_streaming(
        self,
        blueprint: WorkflowBlueprint,
        inputs: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Execute the workflow and yield SSE events as they occur.

        Yields tuples of ``(event_name, event_data)`` for each progress update.

        Events emitted:
        - ``run_started``    - execution begins
        - ``node_started``   - a node begins execution
        - ``node_completed`` - a node finishes successfully
        - ``node_skipped``   - a node was skipped (branch not active)
        - ``node_failed``    - a node failed
        - ``run_completed``  - all nodes done, final result
        - ``run_failed``     - execution failed globally
        """
        start_time = time.time()
        store = VariableStore(env_vars=self._env_vars)

        # Inject inputs into the store under "input." namespace
        if inputs:
            for key, value in inputs.items():
                await store.set(f"input.{key}", value)

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

        # Determine dependencies: for each node, which nodes must complete first
        predecessors: dict[str, set[str]] = defaultdict(set)
        for edge in blueprint.edges:
            predecessors[edge.target].add(edge.source)

        completed: set[str] = set()
        pending: set[str] = set(topo_order)
        running_tasks: dict[asyncio.Task, str] = {}

        # Event queue for streaming
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

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
            """Check if a node should be skipped because all incoming edges are inactive."""
            node_incoming = incoming_edges.get(nid, [])
            if not node_incoming:
                return False
            return all(e.id not in active_edges for e in node_incoming)

        async def _run_node(nid: str) -> None:
            """Execute a single node with semaphore."""
            async with semaphore:
                node = node_index[nid]
                executor = get_executor(node.type)
                ctx = ExecutionContext(
                    run_id=self._run_id,
                    user_id=self._user_id,
                    workflow_id=self._workflow_id,
                    env_vars=self._env_vars,
                )

                await event_queue.put((
                    "node_started",
                    {"node_id": nid, "node_type": node.type.value},
                ))

                try:
                    result = await executor.execute(node, store, ctx)
                    node_results[nid] = result

                    if result.status == NodeStatus.COMPLETED:
                        node_status[nid] = NodeStatus.COMPLETED

                        # Handle conditional branching
                        if result.active_handles is not None:
                            # Deactivate edges that don't match the active handles
                            for edge in outgoing_edges.get(nid, []):
                                if edge.source_handle not in result.active_handles:
                                    active_edges.discard(edge.id)

                        await event_queue.put((
                            "node_completed",
                            {
                                "node_id": nid,
                                "node_type": node.type.value,
                                "status": "completed",
                                "output_preview": _preview(result.output),
                                "duration_ms": result.duration_ms,
                            },
                        ))
                    else:
                        node_status[nid] = NodeStatus.FAILED
                        await event_queue.put((
                            "node_failed",
                            {
                                "node_id": nid,
                                "node_type": node.type.value,
                                "error": result.error,
                                "duration_ms": result.duration_ms,
                            },
                        ))

                except asyncio.CancelledError:
                    node_status[nid] = NodeStatus.FAILED
                    await event_queue.put((
                        "node_failed",
                        {"node_id": nid, "error": "Cancelled"},
                    ))
                    raise
                except Exception as exc:
                    node_status[nid] = NodeStatus.FAILED
                    await event_queue.put((
                        "node_failed",
                        {"node_id": nid, "error": str(exc)},
                    ))

        async def _orchestrator() -> None:
            """Main orchestration loop — runs until all nodes are done."""
            try:
                while pending or running_tasks:
                    # Check for cancellation
                    if self._cancel_event and self._cancel_event.is_set():
                        for nid in sorted(pending):
                            node_status[nid] = NodeStatus.SKIPPED
                            await event_queue.put((
                                "node_skipped",
                                {"node_id": nid, "reason": "Execution cancelled"},
                            ))
                        pending.clear()
                        # Cancel running tasks
                        for task in running_tasks:
                            task.cancel()
                        if running_tasks:
                            await asyncio.gather(
                                *running_tasks, return_exceptions=True
                            )
                        running_tasks.clear()
                        break

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
                        await event_queue.put((
                            "node_skipped",
                            {"node_id": nid, "reason": "Branch not active"},
                        ))

                    # Launch ready nodes
                    for nid in ready:
                        pending.discard(nid)
                        node_status[nid] = NodeStatus.RUNNING
                        task = asyncio.create_task(_run_node(nid))
                        running_tasks[task] = nid

                    if not running_tasks:
                        if pending:
                            # Deadlock — nodes waiting for predecessors that can't finish
                            for nid in sorted(pending):
                                node_status[nid] = NodeStatus.FAILED
                                await event_queue.put((
                                    "node_failed",
                                    {
                                        "node_id": nid,
                                        "error": "Deadlock: dependencies cannot complete",
                                    },
                                ))
                            pending.clear()
                        break

                    # Wait for at least one task to complete
                    done, _ = await asyncio.wait(
                        running_tasks.keys(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for finished_task in done:
                        nid = running_tasks.pop(finished_task)
                        completed.add(nid)

                        exc = finished_task.exception()
                        if exc is not None and not isinstance(
                            exc, asyncio.CancelledError
                        ):
                            logger.exception(
                                "Unexpected error in node %s", nid, exc_info=exc
                            )
                            node_status[nid] = NodeStatus.FAILED

                # Determine final status
                elapsed_ms = int((time.time() - start_time) * 1000)
                failed_nodes = [
                    nid
                    for nid, st in node_status.items()
                    if st == NodeStatus.FAILED
                ]

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
                    await event_queue.put((
                        "run_failed",
                        {
                            "status": "failed",
                            "error": f"Nodes failed: {failed_nodes}",
                            "duration_ms": elapsed_ms,
                        },
                    ))
                else:
                    await event_queue.put((
                        "run_completed",
                        {
                            "status": "completed",
                            "outputs": outputs,
                            "duration_ms": elapsed_ms,
                        },
                    ))

            except asyncio.CancelledError:
                elapsed_ms = int((time.time() - start_time) * 1000)
                await event_queue.put((
                    "run_completed",
                    {
                        "status": "cancelled",
                        "error": "Execution cancelled",
                        "duration_ms": elapsed_ms,
                    },
                ))
            except Exception as exc:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.exception("Workflow orchestrator failed")
                await event_queue.put((
                    "run_failed",
                    {
                        "status": "failed",
                        "error": str(exc),
                        "duration_ms": elapsed_ms,
                    },
                ))
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
