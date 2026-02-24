"""SSE chat endpoints for ReAct and DAG agent modes.

Both endpoints stream Server-Sent Events with the following event names:

- ``step``           – ReAct iteration progress (tool calls, thinking).
- ``step_progress``  – DAG per-step progress (started / iteration / completed).
- ``phase``          – DAG pipeline phase transitions (planning / executing / analyzing).
- ``done``           – Final result payload (always the last event).

A keepalive comment (``": keepalive\\n\\n"``) is emitted every 30 seconds of
inactivity to prevent proxy/browser timeouts during long LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from fim_agent.core.agent import ReActAgent
from fim_agent.core.planner import DAGExecutor, DAGPlanner, PlanAnalyzer

from ..deps import get_fast_llm, get_llm, get_max_concurrency, get_model_registry, get_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# ReAct endpoint
# ---------------------------------------------------------------------------


@router.get("/react")
async def react_endpoint(q: str, user_id: str = "default") -> StreamingResponse:
    """Run a ReAct agent query with SSE progress updates.

    Parameters
    ----------
    q : str
        The user query / task description.
    user_id : str
        Identifier for the requesting user (reserved for future auth).
    """
    _ = user_id  # reserved for future auth

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        yield _sse("step", {"type": "thinking", "iteration": 0})

        progress_queue: asyncio.Queue[str] = asyncio.Queue()
        done_event = asyncio.Event()
        iter_start = time.time()

        def on_iteration(
            iteration: int,
            action: Any,
            observation: str | None,
            error: str | None,
        ) -> None:
            nonlocal iter_start
            if action.type == "tool_call":
                is_starting = observation is None and error is None
                now = time.time()
                iter_elapsed: float | None = None
                if is_starting:
                    # Reset timer when tools start executing, so parallel
                    # tools all measure from the same baseline.
                    iter_start = now
                else:
                    iter_elapsed = round(now - iter_start, 2)
                payload: dict[str, Any] = {
                    "type": "tool_start" if is_starting else "tool_call",
                    "iteration": iteration,
                    "tool_name": action.tool_name,
                    "tool_args": action.tool_args,
                    "reasoning": action.reasoning,
                    "observation": observation,
                    "error": error,
                }
                if iter_elapsed is not None:
                    payload["iter_elapsed"] = iter_elapsed
                progress_queue.put_nowait(_sse("step", payload))

        try:
            llm = get_llm()
            tools = get_tools()
            agent = ReActAgent(llm=llm, tools=tools, max_iterations=20)

            async def _run() -> Any:
                try:
                    return await agent.run(q, on_iteration=on_iteration)
                finally:
                    done_event.set()

            run_task = asyncio.create_task(_run())

            # Drain the queue until the agent task signals completion.
            while not done_event.is_set():
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield item

            # Flush any remaining items queued between the last get() and done_event.
            while not progress_queue.empty():
                yield progress_queue.get_nowait()

            result = run_task.result()

            elapsed = round(time.time() - t0, 2)
            last_iter_elapsed = round(time.time() - iter_start, 2)
            yield _sse(
                "done",
                {
                    "answer": result.answer,
                    "iterations": result.iterations,
                    "elapsed": elapsed,
                    "iter_elapsed": last_iter_elapsed,
                },
            )
        except Exception as exc:
            logger.exception("ReAct agent failed")
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "done",
                {
                    "answer": f"Agent error: {type(exc).__name__}: {exc}",
                    "iterations": 0,
                    "elapsed": elapsed,
                },
            )

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# DAG endpoint
# ---------------------------------------------------------------------------


@router.get("/dag")
async def dag_endpoint(q: str, user_id: str = "default") -> StreamingResponse:
    """Run a DAG planner pipeline with SSE progress updates.

    Parameters
    ----------
    q : str
        The user query / task description.
    user_id : str
        Identifier for the requesting user (reserved for future auth).
    """
    _ = user_id  # reserved for future auth

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()

        # Queue bridges the executor's synchronous callback into the async SSE stream.
        progress_queue: asyncio.Queue[str] = asyncio.Queue()
        done_event = asyncio.Event()

        def on_step_progress(step_id: str, event: str, data: dict[str, Any]) -> None:
            progress_queue.put_nowait(
                _sse("step_progress", {"step_id": step_id, "event": event, **data})
            )

        try:
            llm = get_llm()           # Sonnet — planning & analysis
            fast_llm = get_fast_llm()  # Haiku — step execution
            tools = get_tools()

            # Phase 1: Plan (Sonnet)
            yield _sse("phase", {"name": "planning", "status": "start"})
            planner = DAGPlanner(llm=llm)
            plan = await planner.plan(q)
            yield _sse(
                "phase",
                {
                    "name": "planning",
                    "status": "done",
                    "steps": [
                        {
                            "id": s.id,
                            "task": s.task,
                            "deps": s.dependencies,
                            "tool_hint": s.tool_hint,
                        }
                        for s in plan.steps
                    ],
                },
            )

            # Phase 2: Execute — Haiku (with real-time step progress)
            yield _sse("phase", {"name": "executing", "status": "start"})
            agent = ReActAgent(llm=fast_llm, tools=tools, max_iterations=15)
            registry = get_model_registry()
            executor = DAGExecutor(
                agent=agent,
                max_concurrency=get_max_concurrency(),
                model_registry=registry,
            )

            async def _exec() -> Any:
                try:
                    return await executor.execute(plan, on_progress=on_step_progress)
                finally:
                    done_event.set()

            exec_task = asyncio.create_task(_exec())

            while not done_event.is_set():
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield item

            # Flush remaining items.
            while not progress_queue.empty():
                yield progress_queue.get_nowait()

            plan = exec_task.result()

            yield _sse(
                "phase",
                {
                    "name": "executing",
                    "status": "done",
                    "results": [
                        {
                            "id": s.id,
                            "task": s.task,
                            "status": s.status,
                            "result": s.result,
                            "started_at": s.started_at,
                            "completed_at": s.completed_at,
                            "duration": s.duration,
                        }
                        for s in plan.steps
                    ],
                },
            )

            # Phase 3: Analyze (Sonnet)
            yield _sse("phase", {"name": "analyzing", "status": "start"})
            analyzer = PlanAnalyzer(llm=llm)
            analysis = await analyzer.analyze(plan.goal, plan)
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "phase",
                {
                    "name": "analyzing",
                    "status": "done",
                    "achieved": analysis.achieved,
                    "confidence": analysis.confidence,
                    "reasoning": analysis.reasoning,
                },
            )

            # Build the answer: prefer analyzer's final_answer, fall back to
            # concatenated step results so users always see something useful.
            answer = analysis.final_answer
            if not answer:
                completed = [
                    s for s in plan.steps if s.status == "completed" and s.result
                ]
                if completed:
                    answer = "\n\n---\n\n".join(
                        f"**{s.id}**: {s.result}" for s in completed
                    )
                else:
                    answer = "(goal not achieved)"

            yield _sse(
                "done",
                {
                    "answer": answer,
                    "achieved": analysis.achieved,
                    "confidence": analysis.confidence,
                    "elapsed": elapsed,
                },
            )
        except Exception as exc:
            logger.exception("DAG pipeline failed")
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "done",
                {
                    "answer": f"Pipeline error: {type(exc).__name__}: {exc}",
                    "achieved": False,
                    "confidence": 0.0,
                    "elapsed": elapsed,
                },
            )

    return StreamingResponse(generate(), media_type="text/event-stream")
