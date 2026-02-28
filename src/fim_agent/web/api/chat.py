"""SSE chat endpoints for ReAct and DAG agent modes.

Both endpoints stream Server-Sent Events with the following event names:

- ``step``           – ReAct iteration progress (tool calls, thinking).
- ``step_progress``  – DAG per-step progress (started / iteration / completed).
- ``phase``          – DAG pipeline phase transitions (planning / executing / analyzing).
- ``compact``        – Context compaction occurred (original_messages, kept_messages).
- ``done``           – Final result payload (always the last event).

A keepalive comment (``": keepalive\\n\\n"``) is emitted every 15 seconds of
inactivity to prevent proxy/browser timeouts during long LLM calls.

When the SSE client disconnects, running agent tasks are cancelled promptly
(checked every 0.5 s) so that LLM and tool work does not continue in the
background.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select as sa_select

from fim_agent.core.agent import ReActAgent
from fim_agent.core.model import BaseLLM
from fim_agent.core.model.usage import UsageSummary
from fim_agent.core.planner import (
    AnalysisResult,
    DAGExecutor,
    DAGPlanner,
    ExecutionPlan,
    PlanAnalyzer,
)
from fim_agent.core.memory.context_guard import ContextGuard
from fim_agent.core.tool import ToolRegistry

from ..deps import (
    get_context_budget,
    get_fast_context_budget,
    get_fast_llm,
    get_llm,
    get_llm_from_config,
    get_max_concurrency,
    get_model_registry,
    get_tools,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# DAG re-planning constants
# ---------------------------------------------------------------------------

MAX_REPLAN_ROUNDS = 3
REPLAN_STOP_CONFIDENCE = 0.8

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _emit(sse_events: list[dict[str, Any]], event: str, data: Any) -> str:
    """Accumulate event for persistence and return SSE frame for streaming."""
    sse_events.append({"event": event, "data": data})
    return _sse(event, data)


# ---------------------------------------------------------------------------
# DAG re-planning helper
# ---------------------------------------------------------------------------


def _format_replan_context(plan: ExecutionPlan, analysis: AnalysisResult) -> str:
    """Format previous round's results as context for re-planning."""
    lines = [f"Previous attempt (round {plan.current_round}) did not fully achieve the goal."]
    lines.append(f"Analyzer reasoning: {analysis.reasoning}")
    lines.append("")
    lines.append("Step results from previous round:")
    for step in plan.steps:
        status_info = f"[{step.id}] status={step.status}"
        if step.result:
            result_preview = step.result[:500] + "..." if len(step.result) > 500 else step.result
            lines.append(f"  {status_info}: {result_preview}")
        else:
            lines.append(f"  {status_info}: (no result)")
    lines.append("")
    lines.append("Please create a revised plan that addresses the gaps identified above.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth & agent resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_user(token: str | None) -> tuple[str | None, str | None]:
    """Validate a JWT query-param token and return (user_id, system_instructions), or (None, None).

    Raises HTTPException(401) on invalid/expired tokens.
    """
    if not token:
        return None, None

    from fim_agent.web.auth import decode_token

    payload = decode_token(token)  # raises 401 on bad token
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Fetch user's system_instructions
    system_instructions: str | None = None
    try:
        from fim_agent.db import create_session
        from fim_agent.web.models import User

        async with create_session() as session:
            result = await session.execute(
                sa_select(User.system_instructions).where(User.id == user_id)
            )
            row = result.scalar_one_or_none()
            if row:
                system_instructions = row
    except Exception:
        logger.warning("Failed to load user system_instructions", exc_info=True)

    return user_id, system_instructions


async def _validate_conversation_ownership(
    conversation_id: str, user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*.  Raises 404 otherwise."""
    from fim_agent.db import create_session
    from fim_agent.web.models import Conversation

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Conversation not found")


async def _resolve_agent_config(
    agent_id: str | None, conversation_id: str | None,
) -> dict[str, Any] | None:
    """Load agent configuration from DB.

    Resolution priority: explicit ``agent_id`` > conversation's bound agent.
    Returns a dict with ``instructions``, ``tool_categories``,
    ``model_config_json``, ``kb_ids``, and ``grounding_config``, or ``None``.
    """
    from fim_agent.db import create_session
    from fim_agent.web.models import Agent, Conversation

    resolved_id = agent_id

    if not resolved_id and conversation_id:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Conversation.agent_id).where(
                    Conversation.id == conversation_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                resolved_id = row

    if not resolved_id:
        return None

    async with create_session() as session:
        result = await session.execute(
            sa_select(Agent).where(Agent.id == resolved_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return {
            "instructions": agent.instructions,
            "tool_categories": agent.tool_categories,
            "model_config_json": agent.model_config_json,
            "kb_ids": agent.kb_ids,
            "grounding_config": agent.grounding_config,
        }


def _resolve_llm(agent_cfg: dict[str, Any] | None) -> BaseLLM:
    """Build an LLM from agent config or fall back to global default."""
    if agent_cfg and agent_cfg.get("model_config_json"):
        llm = get_llm_from_config(agent_cfg["model_config_json"])
        if llm is not None:
            return llm
    return get_llm()


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _conversation_sandbox_root(conversation_id: str | None) -> Path | None:
    """Compute the sandbox root for a conversation.

    Returns ``PROJECT_ROOT/tmp/conversations/{conversation_id}`` when a
    conversation ID is provided, or ``None`` for anonymous sessions (which
    fall back to the global sandbox directories).
    """
    if not conversation_id:
        return None
    return _PROJECT_ROOT / "tmp" / "conversations" / conversation_id


def _resolve_tools(
    agent_cfg: dict[str, Any] | None,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> ToolRegistry:
    """Build tool registry, optionally scoped to a per-conversation sandbox."""
    sandbox_root = _conversation_sandbox_root(conversation_id)
    tools = get_tools(sandbox_root=sandbox_root)
    if agent_cfg:
        cats = agent_cfg.get("tool_categories") or []
        tools = tools.filter_by_category(*cats)

    # Inject user_id into the auto-discovered KBRetrieveTool so that
    # retrieval queries the correct per-user vector store directory.
    if user_id:
        from fim_agent.core.tool.builtin.kb_retrieve import KBRetrieveTool

        tools = tools.exclude_by_name("kb_retrieve")
        tools.register(KBRetrieveTool(user_id=user_id))

    # When the agent is bound to knowledge bases, replace the basic kb_retrieve
    # tool with the grounded version.
    kb_ids = agent_cfg.get("kb_ids") if agent_cfg else None
    if kb_ids:
        from fim_agent.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

        tools = tools.exclude_by_name("kb_retrieve", "grounded_retrieve")
        grounding_config = agent_cfg.get("grounding_config") or {}
        confidence_threshold = grounding_config.get("confidence_threshold")
        tools.register(GroundedRetrieveTool(
            kb_ids=kb_ids,
            user_id=user_id,
            confidence_threshold=confidence_threshold,
        ))

    return tools


# ---------------------------------------------------------------------------
# Image loading helper
# ---------------------------------------------------------------------------


def _load_image_data_urls(
    image_ids: str, user_id: str | None,
) -> list[tuple[str, str, str]]:
    """Load images from disk and return list of ``(file_id, filename, data_url)``.

    Returns an empty list if *user_id* is ``None`` or no valid images are
    found.
    """
    if not user_id or not image_ids:
        return []

    from fim_agent.web.api.files import UPLOAD_ROOT, _is_image, _load_index

    index = _load_index(user_id)
    results: list[tuple[str, str, str]] = []

    for fid in image_ids.split(","):
        fid = fid.strip()
        if not fid:
            continue
        meta = index.get(fid)
        if not meta:
            continue
        suffix = Path(meta["filename"]).suffix.lower()
        if not _is_image(suffix):
            continue
        file_path = UPLOAD_ROOT / f"user_{user_id}" / meta["stored_name"]
        if not file_path.exists():
            continue
        mime = meta.get("mime_type", "image/png")
        raw = file_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        results.append((fid, meta["filename"], data_url))

    return results


# ---------------------------------------------------------------------------
# ReAct endpoint
# ---------------------------------------------------------------------------


@router.get("/react")
async def react_endpoint(
    request: Request,
    q: str,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    token: str | None = None,
    image_ids: str | None = None,
) -> StreamingResponse:
    """Run a ReAct agent query with SSE progress updates.

    Parameters
    ----------
    request : Request
        The incoming HTTP request; used to detect client disconnects.
    q : str
        The user query / task description.
    conversation_id : str | None
        Optional conversation ID for persistence and multi-turn memory.
    agent_id : str | None
        Optional agent ID to load per-agent model, tools, and instructions.
    token : str | None
        JWT access token (query param) for SSE auth.  When provided,
        conversation ownership is validated.
    image_ids : str | None
        Comma-separated file IDs of uploaded images to attach to the query
        for vision model processing.
    """
    # -- Pre-stream resolution (before StreamingResponse) -------------------
    current_user_id, user_system_instructions = await _resolve_user(token)
    if conversation_id and current_user_id:
        await _validate_conversation_ownership(conversation_id, current_user_id)

    agent_cfg = await _resolve_agent_config(agent_id, conversation_id)
    llm = _resolve_llm(agent_cfg)
    tools = _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id)
    agent_instructions = agent_cfg["instructions"] if agent_cfg else None

    # Merge user personal instructions + agent-specific instructions
    parts: list[str] = []
    if user_system_instructions:
        parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if agent_instructions:
        parts.append(agent_instructions)
    extra_instructions = "\n\n".join(parts) if parts else None

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = (
            "\n\nYou have access to knowledge bases. When answering questions that "
            "can be found in the knowledge bases, use the grounded_retrieve tool. "
            "Place citation markers [N] at the END of the sentence or claim they support, "
            "not at the beginning. Example: '\u6536\u8d2d\u4ef7\u683c\u4e3a\u6bcf\u80a13.70\u7f8e\u5143 [1]\u3002'\n"
            "If conflicts are detected between sources, mention them to the user. "
            "The confidence score indicates evidence quality \u2014 mention it for important claims."
        )
        extra_instructions = (extra_instructions or "") + grounding_hint

    # Load attached images
    image_data: list[tuple[str, str, str]] = []
    if image_ids:
        image_data = _load_image_data_urls(image_ids, current_user_id)

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []
        yield _emit(sse_events, "step", {"type": "thinking", "iteration": 0})

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_agent.db import create_session
                from fim_agent.web.models import Message as MessageModel

                db_session = create_session()
                user_metadata = None
                if image_data:
                    user_metadata = {
                        "images": [
                            {
                                "file_id": fid,
                                "filename": fname,
                                "mime_type": durl.split(";")[0].split(":")[1],
                            }
                            for fid, fname, durl in image_data
                        ]
                    }
                user_msg = MessageModel(
                    conversation_id=conversation_id,
                    role="user",
                    content=q,
                    message_type="text",
                    metadata_=user_metadata,
                )
                db_session.add(user_msg)
                await db_session.commit()
            except Exception:
                logger.warning(
                    "Failed to persist user message for conversation %s",
                    conversation_id,
                    exc_info=True,
                )
                db_session = None

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
                sse_events.append({"event": "step", "data": payload})
                progress_queue.put_nowait(_sse("step", payload))

        try:
            memory = None
            if conversation_id:
                from fim_agent.core.memory import DbMemory
                memory = DbMemory(
                    conversation_id=conversation_id,
                    compact_llm=get_fast_llm(),
                    user_id=current_user_id,
                )
            context_guard = ContextGuard(
                compact_llm=get_fast_llm(),
                default_budget=get_context_budget(),
            )

            agent = ReActAgent(
                llm=llm,
                tools=tools,
                extra_instructions=extra_instructions,
                max_iterations=20,
                memory=memory,
                context_guard=context_guard,
            )

            image_urls = [url for _, _, url in image_data] if image_data else None

            async def _run() -> Any:
                try:
                    return await agent.run(
                        q, on_iteration=on_iteration, image_urls=image_urls,
                    )
                finally:
                    done_event.set()

            run_task = asyncio.create_task(_run())

            last_keepalive = time.time()
            while not done_event.is_set():
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        logger.info("Client disconnected — cancelling ReAct task")
                        run_task.cancel()
                        with suppress(asyncio.CancelledError, TimeoutError, Exception):
                            await asyncio.wait_for(run_task, timeout=5.0)
                        return
                    now = time.time()
                    if now - last_keepalive >= 15.0:
                        yield ": keepalive\n\n"
                        last_keepalive = now
                    continue
                last_keepalive = time.time()
                yield item

            while not progress_queue.empty():
                yield progress_queue.get_nowait()

            if run_task.cancelled():
                return

            result = run_task.result()

            # Notify frontend if context was compacted
            if memory and memory.was_compacted:
                compact_payload = {
                    "original_messages": memory._original_count,
                    "kept_messages": memory._compacted_count,
                }
                yield _emit(sse_events, "compact", compact_payload)

            elapsed = round(time.time() - t0, 2)
            last_iter_elapsed = round(time.time() - iter_start, 2)
            done_payload: dict[str, Any] = {
                "answer": result.answer,
                "iterations": result.iterations,
                "elapsed": elapsed,
                "iter_elapsed": last_iter_elapsed,
            }
            if result.usage is not None:
                done_payload["usage"] = {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens,
                }
            sse_events.append({"event": "done", "data": done_payload})

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_agent.web.models import (
                        Conversation,
                        Message as MessageModel,
                    )

                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=result.answer,
                        message_type="done",
                        metadata_={**done_payload, "sse_events": sse_events},
                    )
                    db_session.add(assistant_msg)
                    if "usage" in done_payload:
                        stmt = sa_select(Conversation).where(
                            Conversation.id == conversation_id
                        )
                        conv = (
                            await db_session.execute(stmt)
                        ).scalar_one_or_none()
                        if conv:
                            conv.total_tokens = (
                                conv.total_tokens or 0
                            ) + done_payload["usage"].get("total_tokens", 0)
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for "
                        "conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            yield _sse("done", done_payload)
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
        finally:
            if db_session:
                await db_session.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# DAG endpoint
# ---------------------------------------------------------------------------


@router.get("/dag")
async def dag_endpoint(
    request: Request,
    q: str,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    token: str | None = None,
    image_ids: str | None = None,
) -> StreamingResponse:
    """Run a DAG planner pipeline with SSE progress updates.

    Parameters
    ----------
    request : Request
        The incoming HTTP request; used to detect client disconnects.
    q : str
        The user query / task description.
    conversation_id : str | None
        Optional conversation ID for persistence and multi-turn memory.
    agent_id : str | None
        Optional agent ID to load per-agent model, tools, and instructions.
    token : str | None
        JWT access token (query param) for SSE auth.
    image_ids : str | None
        Comma-separated file IDs of uploaded images to attach to the query
        for vision model processing.
    """
    # -- Pre-stream resolution ----------------------------------------------
    current_user_id, user_system_instructions = await _resolve_user(token)
    if conversation_id and current_user_id:
        await _validate_conversation_ownership(conversation_id, current_user_id)

    agent_cfg = await _resolve_agent_config(agent_id, conversation_id)
    llm = _resolve_llm(agent_cfg)
    tools = _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id)
    agent_instructions = agent_cfg["instructions"] if agent_cfg else None

    # Merge user personal instructions + agent-specific instructions
    parts: list[str] = []
    if user_system_instructions:
        parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if agent_instructions:
        parts.append(agent_instructions)
    extra_instructions = "\n\n".join(parts) if parts else None

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = (
            "\n\nYou have access to knowledge bases. When answering questions that "
            "can be found in the knowledge bases, use the grounded_retrieve tool. "
            "Place citation markers [N] at the END of the sentence or claim they support, "
            "not at the beginning. Example: '\u6536\u8d2d\u4ef7\u683c\u4e3a\u6bcf\u80a13.70\u7f8e\u5143 [1]\u3002'\n"
            "If conflicts are detected between sources, mention them to the user. "
            "The confidence score indicates evidence quality \u2014 mention it for important claims."
        )
        extra_instructions = (extra_instructions or "") + grounding_hint

    # DAG uses a fast LLM for step execution; try agent config first.
    fast_llm = get_fast_llm()

    # Load attached images
    dag_image_data: list[tuple[str, str, str]] = []
    if image_ids:
        dag_image_data = _load_image_data_urls(image_ids, current_user_id)

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_agent.db import create_session
                from fim_agent.web.models import Message as MessageModel

                db_session = create_session()
                dag_user_metadata = None
                if dag_image_data:
                    dag_user_metadata = {
                        "images": [
                            {
                                "file_id": fid,
                                "filename": fname,
                                "mime_type": durl.split(";")[0].split(":")[1],
                            }
                            for fid, fname, durl in dag_image_data
                        ]
                    }
                user_msg = MessageModel(
                    conversation_id=conversation_id,
                    role="user",
                    content=q,
                    message_type="text",
                    metadata_=dag_user_metadata,
                )
                db_session.add(user_msg)
                await db_session.commit()
            except Exception:
                logger.warning(
                    "Failed to persist user message for conversation %s",
                    conversation_id,
                    exc_info=True,
                )
                db_session = None

        # -- Load conversation context for multi-turn DAG planning ----------
        # When images are attached, annotate the query so the text-only
        # planner is aware of them.
        enriched_query = q
        if dag_image_data:
            img_names = ", ".join(fname for _, fname, _ in dag_image_data)
            enriched_query = (
                f"{q}\n\n[Attached images: {img_names}]"
            )
        dag_memory = None
        if conversation_id:
            try:
                from fim_agent.core.memory import DbMemory

                dag_memory = DbMemory(
                    conversation_id=conversation_id,
                    compact_llm=fast_llm,
                    user_id=current_user_id,
                )
                history = await dag_memory.get_messages()
                if history:
                    from fim_agent.core.memory.compact import CompactUtils as _CU
                    context_lines = []
                    for msg in history:
                        prefix = "User" if msg.role == "user" else "Assistant"
                        context_lines.append(f"{prefix}: {_CU.content_as_text(msg.content)}")
                    context_str = "\n".join(context_lines)
                    enriched_query = (
                        f"Previous conversation:\n{context_str}\n\n"
                        f"Current request: {q}"
                    )

                    # Truncate enriched_query if too large for planner.
                    from fim_agent.core.memory.compact import CompactUtils
                    from fim_agent.core.memory.context_guard import _COMPACT_PROMPTS

                    enriched_tokens = CompactUtils.estimate_tokens(enriched_query)
                    if enriched_tokens > 16_000:
                        if fast_llm:
                            from fim_agent.core.model.types import ChatMessage

                            summary_result = await fast_llm.chat([
                                ChatMessage(
                                    role="system",
                                    content=_COMPACT_PROMPTS["planner_input"],
                                ),
                                ChatMessage(role="user", content=context_str),
                            ])
                            summary = (
                                summary_result.message.content or ""
                            ).strip()
                            enriched_query = (
                                f"Previous conversation (summary):\n{summary}\n\n"
                                f"Current request: {q}"
                            )
                        else:
                            enriched_query = (
                                f"Previous conversation:\n"
                                f"{context_str[-20000:]}\n\n"
                                f"Current request: {q}"
                            )
            except Exception:
                logger.warning(
                    "Failed to load conversation history for DAG planning "
                    "(conversation %s)",
                    conversation_id,
                    exc_info=True,
                )

        progress_queue: asyncio.Queue[str] = asyncio.Queue()
        done_event = asyncio.Event()

        def on_step_progress(step_id: str, event: str, data: dict[str, Any]) -> None:
            step_payload = {"step_id": step_id, "event": event, **data}
            sse_events.append({"event": "step_progress", "data": step_payload})
            progress_queue.put_nowait(_sse("step_progress", step_payload))

        try:
            plan: ExecutionPlan | None = None
            analysis: AnalysisResult | None = None
            cumulative_usage: UsageSummary | None = None

            for round_num in range(1, MAX_REPLAN_ROUNDS + 1):
                # -- Build replan context from previous round's results ----
                replan_context = ""
                if plan is not None and analysis is not None:
                    replan_context = _format_replan_context(plan, analysis)

                # Phase 1: Plan (smart LLM)
                yield _emit(
                    sse_events,
                    "phase",
                    {"name": "planning", "status": "start", "round": round_num},
                )
                if await request.is_disconnected():
                    logger.info("Client disconnected before planning round %d", round_num)
                    return

                tool_names = [t.name for t in tools.list_tools()]
                planner = DAGPlanner(llm=llm)
                plan = await planner.plan(
                    enriched_query,
                    context=replan_context,
                    tool_names=tool_names,
                )
                plan.current_round = round_num
                yield _emit(
                    sse_events,
                    "phase",
                    {
                        "name": "planning",
                        "status": "done",
                        "round": round_num,
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

                # Phase 2: Execute — fast LLM (with real-time step progress)
                done_event.clear()
                yield _emit(
                    sse_events,
                    "phase",
                    {"name": "executing", "status": "start", "round": round_num},
                )
                dag_context_guard = ContextGuard(
                    compact_llm=fast_llm,
                    default_budget=get_fast_context_budget(),
                )

                agent = ReActAgent(
                    llm=fast_llm,
                    tools=tools,
                    extra_instructions=extra_instructions,
                    max_iterations=15,
                    memory=dag_memory,
                    context_guard=dag_context_guard,
                )
                registry = get_model_registry()
                executor = DAGExecutor(
                    agent=agent,
                    max_concurrency=get_max_concurrency(),
                    model_registry=registry,
                    context_guard=dag_context_guard,
                    original_goal=enriched_query,
                )

                # Capture plan in closure to avoid late-binding issues
                _current_plan = plan

                async def _exec(_p: ExecutionPlan = _current_plan) -> Any:
                    try:
                        return await executor.execute(_p, on_progress=on_step_progress)
                    finally:
                        done_event.set()

                exec_task = asyncio.create_task(_exec())

                last_keepalive = time.time()
                while not done_event.is_set():
                    try:
                        item = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            logger.info("Client disconnected — cancelling DAG exec task")
                            exec_task.cancel()
                            with suppress(asyncio.CancelledError, TimeoutError, Exception):
                                await asyncio.wait_for(exec_task, timeout=5.0)
                            return
                        now = time.time()
                        if now - last_keepalive >= 15.0:
                            yield ": keepalive\n\n"
                            last_keepalive = now
                        continue
                    last_keepalive = time.time()
                    yield item

                while not progress_queue.empty():
                    yield progress_queue.get_nowait()

                if exec_task.cancelled():
                    return

                plan = exec_task.result()

                yield _emit(
                    sse_events,
                    "phase",
                    {
                        "name": "executing",
                        "status": "done",
                        "round": round_num,
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

                if await request.is_disconnected():
                    logger.info("Client disconnected — skipping DAG analysis phase")
                    return

                # Phase 3: Analyze (smart LLM)
                yield _emit(
                    sse_events,
                    "phase",
                    {"name": "analyzing", "status": "start", "round": round_num},
                )
                if await request.is_disconnected():
                    logger.info("Client disconnected before analysis round %d", round_num)
                    return

                analyzer = PlanAnalyzer(llm=llm)
                analysis = await analyzer.analyze(enriched_query, plan)
                yield _emit(
                    sse_events,
                    "phase",
                    {
                        "name": "analyzing",
                        "status": "done",
                        "round": round_num,
                        "achieved": analysis.achieved,
                        "confidence": analysis.confidence,
                        "reasoning": analysis.reasoning,
                    },
                )

                # -- Accumulate usage from this round ----------------------
                round_usage = plan.total_usage
                if analysis.usage is not None:
                    if round_usage is not None:
                        round_usage = round_usage + analysis.usage
                    else:
                        round_usage = analysis.usage
                if round_usage is not None:
                    if cumulative_usage is not None:
                        cumulative_usage = cumulative_usage + round_usage
                    else:
                        cumulative_usage = round_usage

                # -- Check if goal achieved or confident enough ------------
                if analysis.achieved:
                    break
                # High confidence that goal cannot be achieved — stop wasting tokens
                if not analysis.achieved and analysis.confidence >= REPLAN_STOP_CONFIDENCE:
                    logger.info(
                        "DAG round %d: goal not achieved with high confidence (%.1f), "
                        "stopping re-planning",
                        round_num,
                        analysis.confidence,
                    )
                    break

                if round_num < MAX_REPLAN_ROUNDS:
                    yield _emit(
                        sse_events,
                        "phase",
                        {
                            "name": "replanning",
                            "status": "start",
                            "round": round_num,
                            "reason": analysis.reasoning,
                        },
                    )
                # Otherwise loop continues to next round

            # -- After loop: build answer and persist ----------------------
            # plan and analysis are guaranteed set (at least 1 iteration ran)
            if plan is None or analysis is None:
                raise RuntimeError("DAG loop completed without producing a plan and analysis")

            elapsed = round(time.time() - t0, 2)

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

            # Notify frontend if context was compacted
            if dag_memory and dag_memory.was_compacted:
                compact_payload = {
                    "original_messages": dag_memory._original_count,
                    "kept_messages": dag_memory._compacted_count,
                }
                yield _emit(sse_events, "compact", compact_payload)

            dag_done_payload: dict[str, Any] = {
                "answer": answer,
                "achieved": analysis.achieved,
                "confidence": analysis.confidence,
                "elapsed": elapsed,
                "rounds": plan.current_round,
            }
            if cumulative_usage is not None:
                dag_done_payload["usage"] = {
                    "prompt_tokens": cumulative_usage.prompt_tokens,
                    "completion_tokens": cumulative_usage.completion_tokens,
                    "total_tokens": cumulative_usage.total_tokens,
                }
            sse_events.append({"event": "done", "data": dag_done_payload})

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_agent.web.models import (
                        Conversation as ConvModel,
                        Message as MessageModel,
                    )

                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        message_type="done",
                        metadata_={**dag_done_payload, "sse_events": sse_events},
                    )
                    db_session.add(assistant_msg)
                    if "usage" in dag_done_payload:
                        stmt = sa_select(ConvModel).where(
                            ConvModel.id == conversation_id
                        )
                        conv = (
                            await db_session.execute(stmt)
                        ).scalar_one_or_none()
                        if conv:
                            conv.total_tokens = (
                                conv.total_tokens or 0
                            ) + dag_done_payload["usage"].get(
                                "total_tokens", 0
                            )
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for "
                        "conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            yield _sse("done", dag_done_payload)
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
        finally:
            if db_session:
                await db_session.close()

    return StreamingResponse(generate(), media_type="text/event-stream")
