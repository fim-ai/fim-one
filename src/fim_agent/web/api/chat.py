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
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.core.agent import ReActAgent
from fim_agent.core.model import BaseLLM
from fim_agent.core.model.types import ChatMessage
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
from fim_agent.core.utils import extract_json_value, get_language_directive

from ..deps import (
    get_context_budget,
    get_dag_max_replan_rounds,
    get_dag_replan_stop_confidence,
    get_dag_step_max_iterations,
    get_fast_context_budget,
    get_fast_llm,
    get_llm,
    get_llm_from_config,
    get_max_concurrency,
    get_model_registry,
    get_react_max_iterations,
    get_tools,
)
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user_optional
from fim_agent.web.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interrupt queue infrastructure
# ---------------------------------------------------------------------------


@dataclass
class InjectedMessage:
    """A user message injected mid-stream into a running agent execution."""

    id: str  # unique id for recall/edit
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InterruptQueue:
    """Async-safe interrupt queue that supports recall (cancel) of pending messages."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[InjectedMessage] = asyncio.Queue()
        self._recalled: set[str] = set()  # ids of recalled messages

    def put(self, msg: InjectedMessage) -> None:
        self._queue.put_nowait(msg)

    def recall(self, msg_id: str) -> bool:
        """Mark a message as recalled. Returns True if the id was not already recalled."""
        if msg_id in self._recalled:
            return False
        self._recalled.add(msg_id)
        return True

    def drain(self) -> list[InjectedMessage]:
        """Drain all non-recalled messages from the queue."""
        result: list[InjectedMessage] = []
        while not self._queue.empty():
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if msg.id not in self._recalled:
                result.append(msg)
        return result

    def empty(self) -> bool:
        return self._queue.empty()


_interrupt_queues: dict[str, InterruptQueue] = {}


def register_interrupt_queue(conversation_id: str) -> InterruptQueue:
    """Create and register an interrupt queue for a conversation."""
    q = InterruptQueue()
    _interrupt_queues[conversation_id] = q
    return q


def unregister_interrupt_queue(conversation_id: str) -> None:
    """Remove the interrupt queue for a conversation."""
    _interrupt_queues.pop(conversation_id, None)


def get_interrupt_queue(conversation_id: str) -> InterruptQueue | None:
    """Get the interrupt queue for a conversation, or None if not active."""
    return _interrupt_queues.get(conversation_id)

router = APIRouter(prefix="/api")

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
# Suggested follow-ups helper
# ---------------------------------------------------------------------------


async def _generate_suggestions(
    fast_llm: "BaseLLM",
    query: str,
    answer: str,
    *,
    count: int = 3,
    preferred_language: str | None = None,
) -> list[str]:
    """Generate follow-up question suggestions based on query and answer.

    Uses a fast LLM to produce *count* concise follow-up questions that the
    user might ask next.  The result is ephemeral — it is injected into the
    SSE ``done`` payload but **not** persisted to the database.

    On any failure the function silently returns an empty list so that the
    main chat flow is never interrupted.
    """
    try:
        truncated_answer = answer[:1500]

        lang_directive = get_language_directive(preferred_language)
        lang_rule = (
            f"- {lang_directive}\n" if lang_directive
            else "- Match the language of the original query (e.g. Chinese query -> Chinese questions).\n"
        )
        system_prompt = (
            "You generate concise follow-up questions that a user might naturally ask "
            "after receiving an answer. The questions should explore different angles: "
            "deeper detail, related topics, or practical next steps.\n\n"
            "Rules:\n"
            f"- Return EXACTLY {count} questions.\n"
            "- Each question must be a single sentence, under 80 characters.\n"
            f"{lang_rule}"
            "- Return ONLY a JSON array of strings, no other text."
        )
        user_content = (
            f"User query: {query}\n\n"
            f"Assistant answer (truncated): {truncated_answer}"
        )

        result = await fast_llm.chat([
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ])

        raw = (result.message.content or "").strip()

        suggestions = extract_json_value(raw)
        if isinstance(suggestions, list) and all(isinstance(s, str) for s in suggestions):
            return suggestions[:count]

        logger.debug("_generate_suggestions: unexpected JSON structure: %s", type(suggestions))
        return []
    except Exception:
        logger.debug("_generate_suggestions failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Auth & agent resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_user(
    token: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate a JWT query-param token and return (user_id, system_instructions, preferred_language).

    Returns ``(None, None, None)`` when *token* is not provided.
    Raises HTTPException(401) on invalid/expired tokens.
    """
    if not token:
        return None, None, None

    from fim_agent.web.auth import decode_token

    payload = decode_token(token)  # raises 401 on bad token
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    # Fetch user's system_instructions and preferred_language
    system_instructions: str | None = None
    preferred_language: str | None = None
    try:
        from fim_agent.db import create_session
        from fim_agent.web.models import User

        async with create_session() as session:
            result = await session.execute(
                sa_select(User.system_instructions, User.preferred_language).where(
                    User.id == user_id
                )
            )
            row = result.one_or_none()
            if row:
                system_instructions = row[0]
                preferred_language = row[1]
    except Exception:
        logger.warning("Failed to load user system_instructions", exc_info=True)

    return user_id, system_instructions, preferred_language


async def _validate_conversation_ownership(
    conversation_id: str, user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*.  Raises 404 otherwise."""
    from fim_agent.db import create_session
    from fim_agent.web.models import Conversation

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Conversation not found")


async def _resolve_agent_config(
    agent_id: str | None,
    conversation_id: str | None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Load agent configuration from DB.

    Resolution priority: explicit ``agent_id`` > conversation's bound agent.
    When *user_id* is provided, the agent must belong to that user (returns
    ``None`` otherwise) to prevent cross-user agent access.
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
        stmt = sa_select(Agent).where(Agent.id == resolved_id)
        if user_id:
            stmt = stmt.where(Agent.user_id == user_id)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return {
            "instructions": agent.instructions,
            "tool_categories": agent.tool_categories,
            "model_config_json": agent.model_config_json,
            "kb_ids": agent.kb_ids,
            "connector_ids": agent.connector_ids,
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


async def _resolve_tools(
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

    # When the agent is bound to knowledge bases, choose retrieval tool based on
    # RETRIEVAL_MODE: "grounding" → full pipeline, "simple" → basic RAG.
    # Priority: agent grounding_config.retrieval_mode > RETRIEVAL_MODE env > "grounding"
    kb_ids = agent_cfg.get("kb_ids") if agent_cfg else None
    if kb_ids:
        retrieval_mode = _get_retrieval_mode(agent_cfg)
        tools = tools.exclude_by_name("kb_retrieve", "grounded_retrieve")

        if retrieval_mode == "simple":
            from fim_agent.core.tool.builtin.kb_retrieve import KBRetrieveTool

            tools.register(KBRetrieveTool(user_id=user_id, kb_ids=kb_ids))
        else:
            from fim_agent.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

            grounding_config = agent_cfg.get("grounding_config") or {}
            confidence_threshold = grounding_config.get("confidence_threshold")
            tools.register(GroundedRetrieveTool(
                kb_ids=kb_ids,
                user_id=user_id,
                confidence_threshold=confidence_threshold,
            ))
    elif user_id:
        # No bound KBs — keep basic kb_retrieve with user scope
        from fim_agent.core.tool.builtin.kb_retrieve import KBRetrieveTool

        tools = tools.exclude_by_name("kb_retrieve")
        tools.register(KBRetrieveTool(user_id=user_id))

    # Load connector tools when the agent has bound connectors.
    connector_ids = agent_cfg.get("connector_ids") if agent_cfg else None
    if connector_ids:
        from fim_agent.core.tool.connector import ConnectorToolAdapter
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector as ConnectorModel

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        try:
            async with create_session() as session:
                stmt = (
                    select(ConnectorModel)
                    .options(selectinload(ConnectorModel.actions))
                    .where(ConnectorModel.id.in_(connector_ids))
                )
                if user_id:
                    stmt = stmt.where(ConnectorModel.user_id == user_id)
                result = await session.execute(stmt)
                connectors = result.scalars().all()
                for conn in connectors:
                    for action in (conn.actions or []):
                        adapter = ConnectorToolAdapter(
                            connector_name=conn.name,
                            connector_base_url=conn.base_url,
                            connector_auth_type=conn.auth_type,
                            connector_auth_config=conn.auth_config,
                            action_name=action.name,
                            action_description=action.description or "",
                            action_method=action.method,
                            action_path=action.path,
                            action_parameters_schema=action.parameters_schema,
                            action_request_body_template=action.request_body_template,
                            action_response_extract=action.response_extract,
                            action_requires_confirmation=action.requires_confirmation,
                        )
                        tools.register(adapter)
                logger.info(
                    "Loaded %d connector tools from %d connectors",
                    sum(len(c.actions or []) for c in connectors),
                    len(connectors),
                )
        except Exception:
            logger.warning("Failed to load connector tools", exc_info=True)

    return tools


# ---------------------------------------------------------------------------
# Image loading helper
# ---------------------------------------------------------------------------


def _get_retrieval_mode(agent_cfg: dict[str, Any] | None) -> str:
    """Resolve retrieval mode: agent grounding_config > RETRIEVAL_MODE env > 'grounding'."""
    grounding_config = (agent_cfg.get("grounding_config") or {}) if agent_cfg else {}
    return (
        grounding_config.get("retrieval_mode")
        or os.environ.get("RETRIEVAL_MODE", "grounding")
    )


def _kb_system_hint(agent_cfg: dict[str, Any]) -> str:
    """Return the system-prompt hint for KB retrieval based on RETRIEVAL_MODE."""
    retrieval_mode = _get_retrieval_mode(agent_cfg)
    tool_name = "kb_retrieve" if retrieval_mode == "simple" else "grounded_retrieve"

    # Common citation instructions shared by both modes
    hint = (
        f"\n\nYou have access to knowledge bases. When answering questions that "
        f"can be found in the knowledge bases, use the {tool_name} tool. "
        "Place citation markers [N] at the END of the sentence or claim they support, "
        "not at the beginning. Example: '\u6536\u8d2d\u4ef7\u683c\u4e3a\u6bcf\u80a13.70\u7f8e\u5143 [1]\u3002'\n"
        f"If you call {tool_name} multiple times, use the exact [N] numbers "
        "from each result block."
    )

    # Grounding-specific instructions
    if retrieval_mode != "simple":
        hint += (
            "\nIf conflicts are detected between sources, mention them to the user. "
            "The confidence score indicates evidence quality \u2014 mention it for important claims. "
            "Source numbers are cumulative across calls "
            "(e.g., first call [1]-[5], second call [6]-[10])."
        )

    return hint


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
# Inject endpoint — mid-stream message injection
# ---------------------------------------------------------------------------


class InjectMessageRequest(BaseModel):
    """Request body for injecting a message into an active agent execution."""

    conversation_id: str
    content: str


@router.post("/chat/inject")
async def inject_message(
    body: InjectMessageRequest,
    current_user: User | None = Depends(get_current_user_optional),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Inject a user message into a running agent execution.

    The message is queued for the agent to absorb at its next natural
    breakpoint (between iterations).  It is also persisted immediately
    to the conversation history.

    Returns 409 if no active execution exists for the conversation.
    """
    q = get_interrupt_queue(body.conversation_id)
    if q is None:
        raise HTTPException(
            status_code=409,
            detail="No active execution for this conversation",
        )
    msg_id = uuid.uuid4().hex[:12]
    q.put(InjectedMessage(id=msg_id, content=body.content))

    # Persist the injected message to DB immediately.
    try:
        from fim_agent.web.models import Message as MessageModel

        msg = MessageModel(
            conversation_id=body.conversation_id,
            role="user",
            content=body.content,
            message_type="inject",
        )
        db.add(msg)
        await db.commit()
    except Exception:
        logger.warning(
            "Failed to persist injected message for conversation %s",
            body.conversation_id,
            exc_info=True,
        )

    return {"success": True, "id": msg_id}


class RecallInjectRequest(BaseModel):
    """Request body for recalling a queued inject message."""

    conversation_id: str
    inject_id: str


@router.post("/chat/inject/recall")
async def recall_inject(
    body: RecallInjectRequest,
    current_user: User | None = Depends(get_current_user_optional),  # noqa: B008
) -> dict[str, bool]:
    """Recall (cancel) a queued inject message before the agent consumes it."""
    q = get_interrupt_queue(body.conversation_id)
    if q is None:
        raise HTTPException(status_code=409, detail="No active execution")
    recalled = q.recall(body.inject_id)
    return {"success": recalled}


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
    current_user_id, user_system_instructions, preferred_language = await _resolve_user(token)
    if conversation_id and current_user_id:
        await _validate_conversation_ownership(conversation_id, current_user_id)

    agent_cfg = await _resolve_agent_config(agent_id, conversation_id, user_id=current_user_id)
    llm = _resolve_llm(agent_cfg)
    tools = await _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id)
    agent_instructions = agent_cfg["instructions"] if agent_cfg else None

    # Merge user personal instructions + agent-specific instructions
    parts: list[str] = []
    if user_system_instructions:
        parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if agent_instructions:
        parts.append(agent_instructions)
    extra_instructions = "\n\n".join(parts) if parts else None

    # Prepend language directive when user has an explicit language preference
    lang_directive = get_language_directive(preferred_language)
    if lang_directive:
        extra_instructions = f"{lang_directive}\n\n{extra_instructions}" if extra_instructions else lang_directive

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = _kb_system_hint(agent_cfg)
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

        # Register interrupt queue for mid-stream injection.
        interrupt_queue: InterruptQueue | None = None
        if conversation_id:
            interrupt_queue = register_interrupt_queue(conversation_id)

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

            # Handle injected user messages as a special SSE event.
            if action.tool_name == "__inject__":
                inject_payload: dict[str, Any] = {
                    "type": "inject",
                    "content": action.tool_args.get("content", ""),
                }
                if action.tool_args.get("id"):
                    inject_payload["id"] = action.tool_args["id"]
                sse_events.append({"event": "inject", "data": inject_payload})
                progress_queue.put_nowait(_sse("inject", inject_payload))
                return

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
                max_iterations=get_react_max_iterations(),
                memory=memory,
                context_guard=context_guard,
            )

            image_urls = [url for _, _, url in image_data] if image_data else None

            async def _run() -> Any:
                try:
                    return await agent.run(
                        q, on_iteration=on_iteration, image_urls=image_urls,
                        interrupt_queue=interrupt_queue,
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
            # Final drain of any remaining injected messages.
            if interrupt_queue is not None:
                remaining = interrupt_queue.drain()
                if remaining:
                    done_payload["pending_injections"] = [m.content for m in remaining]

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

            # ephemeral: generate AFTER persist, inject BEFORE yield
            suggestions = await _generate_suggestions(
                get_fast_llm(), q, result.answer, preferred_language=preferred_language,
            )
            if suggestions:
                done_payload["suggestions"] = suggestions

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
            if conversation_id:
                unregister_interrupt_queue(conversation_id)
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
    current_user_id, user_system_instructions, preferred_language = await _resolve_user(token)
    if conversation_id and current_user_id:
        await _validate_conversation_ownership(conversation_id, current_user_id)

    agent_cfg = await _resolve_agent_config(agent_id, conversation_id, user_id=current_user_id)
    llm = _resolve_llm(agent_cfg)
    tools = await _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id)
    agent_instructions = agent_cfg["instructions"] if agent_cfg else None

    # Merge user personal instructions + agent-specific instructions
    parts: list[str] = []
    if user_system_instructions:
        parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if agent_instructions:
        parts.append(agent_instructions)
    extra_instructions = "\n\n".join(parts) if parts else None

    # Prepend language directive when user has an explicit language preference
    lang_directive = get_language_directive(preferred_language)
    if lang_directive:
        extra_instructions = f"{lang_directive}\n\n{extra_instructions}" if extra_instructions else lang_directive

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = _kb_system_hint(agent_cfg)
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

        # Register interrupt queue for mid-stream injection.
        dag_interrupt_queue: InterruptQueue | None = None
        if conversation_id:
            dag_interrupt_queue = register_interrupt_queue(conversation_id)

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

            max_replan_rounds = get_dag_max_replan_rounds()
            replan_stop_confidence = get_dag_replan_stop_confidence()
            dag_step_max_iters = get_dag_step_max_iterations()

            round_num = 0
            autonomous_replans = 0
            inject_in_round = False
            while True:
                round_num += 1
                inject_in_round = False
                # -- Build replan context from previous round's results ----
                replan_context = ""
                if plan is not None and analysis is not None:
                    replan_context = _format_replan_context(plan, analysis)

                # Drain any injected messages at phase transition.
                if dag_interrupt_queue is not None:
                    for injected in dag_interrupt_queue.drain():
                        current_phase = "planning" if plan is None else "replanning"
                        inject_payload = {
                            "type": "inject",
                            "content": injected.content,
                            "phase": current_phase,
                        }
                        sse_events.append({"event": "inject", "data": inject_payload})
                        yield _sse("inject", inject_payload)
                        enriched_query += f"\n\n[User follow-up]: {injected.content}"
                        inject_in_round = True

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
                planner = DAGPlanner(llm=llm, language_directive=lang_directive)
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
                    max_iterations=dag_step_max_iters,
                    memory=dag_memory,
                    context_guard=dag_context_guard,
                )
                registry = get_model_registry()
                exec_stop_event = asyncio.Event()
                executor = DAGExecutor(
                    agent=agent,
                    max_concurrency=get_max_concurrency(),
                    model_registry=registry,
                    context_guard=dag_context_guard,
                    original_goal=enriched_query,
                    stop_event=exec_stop_event,
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
                        # Drain inject queue during execution for real-time feedback.
                        if dag_interrupt_queue is not None:
                            for injected in dag_interrupt_queue.drain():
                                inject_payload = {
                                    "type": "inject",
                                    "content": injected.content,
                                    "phase": "executing",
                                }
                                sse_events.append({"event": "inject", "data": inject_payload})
                                yield _sse("inject", inject_payload)
                                enriched_query += f"\n\n[User follow-up]: {injected.content}"
                                inject_in_round = True
                                exec_stop_event.set()
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

                # Drain any injected messages before analysis.
                if dag_interrupt_queue is not None:
                    for injected in dag_interrupt_queue.drain():
                        inject_payload = {
                            "type": "inject",
                            "content": injected.content,
                            "phase": "analyzing",
                        }
                        sse_events.append({"event": "inject", "data": inject_payload})
                        yield _sse("inject", inject_payload)
                        enriched_query += f"\n\n[User follow-up]: {injected.content}"
                        inject_in_round = True

                # Phase 3: Analyze (smart LLM)
                yield _emit(
                    sse_events,
                    "phase",
                    {"name": "analyzing", "status": "start", "round": round_num},
                )
                if await request.is_disconnected():
                    logger.info("Client disconnected before analysis round %d", round_num)
                    return

                analyzer = PlanAnalyzer(llm=llm, language_directive=lang_directive)
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

                if inject_in_round:
                    # User-initiated replan — always allowed, does not
                    # consume autonomous replan budget.
                    logger.info(
                        "DAG round %d: user inject triggered replan",
                        round_num,
                    )
                else:
                    # Autonomous replan — subject to budget and confidence gate.
                    autonomous_replans += 1
                    if autonomous_replans >= max_replan_rounds - 1:
                        logger.info(
                            "DAG round %d: autonomous replan budget exhausted (%d/%d)",
                            round_num,
                            autonomous_replans,
                            max_replan_rounds - 1,
                        )
                        break
                    if analysis.confidence >= replan_stop_confidence:
                        logger.info(
                            "DAG round %d: goal not achieved with high confidence (%.1f), "
                            "stopping re-planning",
                            round_num,
                            analysis.confidence,
                        )
                        break

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
                # Loop continues to next round

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
            # Final drain: emit inject events for any late messages, then record them.
            if dag_interrupt_queue is not None:
                remaining = dag_interrupt_queue.drain()
                for injected in remaining:
                    inject_payload = {
                        "type": "inject",
                        "content": injected.content,
                        "phase": "done",
                    }
                    sse_events.append({"event": "inject", "data": inject_payload})
                    yield _sse("inject", inject_payload)
                if remaining:
                    dag_done_payload["pending_injections"] = [m.content for m in remaining]

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

            # ephemeral: generate AFTER persist, inject BEFORE yield
            dag_suggestions = await _generate_suggestions(
                fast_llm, q, answer, preferred_language=preferred_language,
            )
            if dag_suggestions:
                dag_done_payload["suggestions"] = dag_suggestions

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
            if conversation_id:
                unregister_interrupt_queue(conversation_id)
            if db_session:
                await db_session.close()

    return StreamingResponse(generate(), media_type="text/event-stream")
