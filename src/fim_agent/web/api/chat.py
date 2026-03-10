"""SSE chat endpoints for ReAct and DAG agent modes.

Both endpoints stream Server-Sent Events with the following event names:

- ``step``           – ReAct iteration progress (tool calls, thinking).
- ``step_progress``  – DAG per-step progress (started / iteration / completed).
- ``phase``          – Pipeline phase transitions (selecting_tools / planning / executing / analyzing).
- ``compact``        – Context compaction occurred (original_messages, kept_messages).
- ``answer``         – Streamed answer text (start / delta / done) emitted before ``done``.
- ``done``           – Final result payload (answer complete, emitted immediately).
- ``suggestions``    – Suggested follow-up questions (emitted after ``done``).
- ``title``          – Auto-generated conversation title (emitted after ``done``).
- ``end``            – Stream terminator (always the last event, NOT persisted).

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
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from fim_agent.web.exceptions import AppError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.core.agent import ReActAgent
from fim_agent.core.model import BaseLLM
from fim_agent.core.model.types import ChatMessage
from fim_agent.core.model.usage import UsageSummary, UsageTracker
from fim_agent.core.planner import (
    AnalysisResult,
    DAGExecutor,
    DAGPlanner,
    ExecutionPlan,
    PlanAnalyzer,
)
from fim_agent.core.memory.context_guard import ContextGuard
from fim_agent.core.security import is_stdio_allowed
from fim_agent.core.tool import ToolRegistry
from fim_agent.core.utils import extract_json_value, get_language_directive

from ..deps import (
    get_context_budget,
    get_dag_max_replan_rounds,
    get_dag_replan_stop_confidence,
    get_dag_step_max_iterations,
    get_effective_context_budget,
    get_effective_fast_context_budget,
    get_effective_fast_llm,
    get_effective_llm,
    get_fast_context_budget,
    get_fast_llm,
    get_llm,
    get_llm_by_config_id,
    get_llm_from_config,
    get_max_concurrency,
    get_model_registry,
    get_react_max_iterations,
    get_tools,
)
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user, get_current_user_optional
from fim_agent.web.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensitive word check helper
# ---------------------------------------------------------------------------


async def _check_sensitive_words(text: str, db: AsyncSession) -> list[str]:
    """Return list of matched blocked words. Empty = clean."""
    from fim_agent.web.models import SensitiveWord

    result = await db.execute(
        sa_select(SensitiveWord).where(
            SensitiveWord.is_active == True,  # noqa: E712
        )
    )
    words = result.scalars().all()
    text_lower = text.lower()
    return [w.word for w in words if w.word.lower() in text_lower]


# ---------------------------------------------------------------------------
# Interrupt broker
# ---------------------------------------------------------------------------

from fim_agent.web.interrupt import (
    InjectedMessage,
    InterruptQueue,
    get_broker,
)

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


def _chunk_answer(text: str, target_size: int = 30) -> list[str]:
    """Split answer text into word-boundary chunks for streaming effect.

    Produces chunks of approximately *target_size* characters, breaking
    at word boundaries to avoid splitting words or markdown tokens.
    """
    if not text:
        return []
    words = text.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = current + " " + word if current else word
        if current and len(candidate) > target_size:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


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
    usage_tracker: "UsageTracker | None" = None,
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
        if usage_tracker and result.usage:
            await usage_tracker.record(result.usage)

        suggestions = extract_json_value(raw)
        if isinstance(suggestions, list) and all(isinstance(s, str) for s in suggestions):
            return suggestions[:count]

        logger.debug("_generate_suggestions: unexpected JSON structure: %s", type(suggestions))
        return []
    except Exception:
        logger.debug("_generate_suggestions failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Auto-title helper
# ---------------------------------------------------------------------------


async def _generate_title(
    fast_llm: "BaseLLM",
    query: str,
    answer: str,
    *,
    preferred_language: str | None = None,
    usage_tracker: "UsageTracker | None" = None,
) -> str | None:
    """Generate a concise conversation title from the first Q&A exchange.

    Uses the fast LLM to produce a short descriptive title.  The result is
    persisted to the ``Conversation.title`` column by the caller.

    On any failure the function silently returns ``None`` so that the main
    chat flow is never interrupted.
    """
    try:
        lang_directive = get_language_directive(preferred_language)
        lang_rule = (
            f"- {lang_directive}\n" if lang_directive
            else "- Match the language of the user query.\n"
        )
        system_prompt = (
            "Generate a short, descriptive title for a conversation based on "
            "the user's first message and the assistant's response.\n\n"
            "Rules:\n"
            "- The title MUST be under 50 characters.\n"
            "- Capture the core topic or intent.\n"
            "- Do NOT wrap the title in quotes or add punctuation at the edges.\n"
            f"{lang_rule}"
            "- Return ONLY the title text, nothing else."
        )
        user_content = (
            f"User: {query[:500]}\n\n"
            f"Assistant: {answer[:500]}"
        )

        result = await fast_llm.chat([
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ])

        raw = (result.message.content or "").strip().strip("\"'")
        if usage_tracker and result.usage:
            await usage_tracker.record(result.usage)

        if raw and len(raw) <= 100:
            return raw
        return None
    except Exception:
        logger.debug("_generate_title failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Auth & agent resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_user(
    token: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate a JWT query-param token (or SSE ticket JWT) and return
    ``(user_id, system_instructions, preferred_language)``.

    Returns ``(None, None, None)`` when *token* is not provided.
    Raises HTTPException(401) on invalid/expired tokens.
    """
    if not token:
        return None, None, None

    import jwt as pyjwt
    from fim_agent.web.auth import SECRET_KEY, ALGORITHM

    # Decode the JWT — works for both SSE tickets and normal access tokens
    try:
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise AppError("token_expired", status_code=401)
    except pyjwt.InvalidTokenError:
        raise AppError("invalid_token", status_code=401)

    # SSE ticket or normal access token — both have "sub" as user_id
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise AppError("invalid_token_payload", status_code=401)

    # -- Fetch full user record from DB ------------------------------------
    system_instructions: str | None = None
    preferred_language: str | None = None
    try:
        from fim_agent.db import create_session
        from fim_agent.web.models import User

        async with create_session() as session:
            result = await session.execute(
                sa_select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise AppError("user_not_found", status_code=401)

            # Fix #11a: reject disabled accounts
            if not user.is_active:
                return None, None, None

            # Fix #11b: reject tokens issued before a force-logout event
            if user.tokens_invalidated_at is not None:
                iat = payload.get("iat")
                if iat is None:
                    return None, None, None
                token_issued = (
                    datetime.fromtimestamp(iat, tz=UTC)
                    if isinstance(iat, (int, float))
                    else iat
                )
                if token_issued <= user.tokens_invalidated_at.replace(tzinfo=UTC):
                    return None, None, None

            system_instructions = user.system_instructions
            preferred_language = user.preferred_language
    except HTTPException:
        raise
    except Exception:
        logger.warning("Failed to load user record", exc_info=True)

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
            raise AppError("conversation_not_found", status_code=404)


async def _check_token_quota(user_id: str) -> None:
    """Raise 429 if the user has exceeded their monthly token quota."""
    from fim_agent.db import create_session
    from fim_agent.web.api.admin import get_setting
    from fim_agent.web.models import Conversation, User

    async with create_session() as session:
        result = await session.execute(
            sa_select(User.token_quota).where(User.id == user_id)
        )
        user_quota = result.scalar_one_or_none()

        if user_quota is None:
            default_str = await get_setting(session, "default_token_quota", "0")
            user_quota = int(default_str) if default_str.isdigit() else 0

        if user_quota and user_quota > 0:
            from sqlalchemy import func as _func

            first_of_month = datetime(
                date.today().year, date.today().month, 1, tzinfo=timezone.utc
            )
            monthly_result = await session.execute(
                sa_select(_func.coalesce(_func.sum(Conversation.total_tokens), 0))
                .where(
                    Conversation.user_id == user_id,
                    Conversation.created_at >= first_of_month,
                )
            )
            monthly_tokens = monthly_result.scalar_one()
            if monthly_tokens >= user_quota:
                raise AppError("token_quota_exceeded", status_code=429)


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
        from sqlalchemy import and_ as _and_agent, or_ as _or_agent

        stmt = sa_select(Agent).where(Agent.id == resolved_id)
        if user_id:
            stmt = stmt.where(
                _or_agent(
                    Agent.user_id == user_id,
                    _and_agent(Agent.is_global == True, Agent.status == "published"),  # noqa: E712
                )
            )
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return {
            "agent_id": agent.id,
            "instructions": agent.instructions,
            "tool_categories": agent.tool_categories,
            "model_config_json": agent.model_config_json,
            "kb_ids": agent.kb_ids,
            "connector_ids": agent.connector_ids,
            "grounding_config": agent.grounding_config,
            "sandbox_config": agent.sandbox_config,
        }


async def _resolve_llm(
    agent_cfg: dict[str, Any] | None,
    db: AsyncSession,
) -> BaseLLM:
    """Build an LLM with priority: agent config id > inline config > system default > ENV."""
    if agent_cfg:
        cfg = agent_cfg.get("model_config_json") or {}
        model_config_id = cfg.get("model_config_id") if isinstance(cfg, dict) else None
        if model_config_id:
            llm = await get_llm_by_config_id(db, model_config_id)
            if llm is not None:
                return llm
        # Inline custom parameters (legacy path, kept for compatibility)
        if cfg and isinstance(cfg, dict):
            llm = get_llm_from_config(cfg)
            if llm is not None:
                return llm
    # System default -> ENV fallback
    llm = await get_effective_llm(db)
    if not getattr(llm, "api_key", None):
        raise ValueError(
            "No LLM API key configured. "
            "Go to Admin → Models to add a model provider, "
            "or set LLM_API_KEY in your environment."
        )
    return llm


async def _resolve_fast_llm(
    agent_cfg: dict[str, Any] | None,
    db: AsyncSession,
) -> BaseLLM:
    """Fast LLM: agent fast_model_config_id > DB role='fast' > DB role='general' > ENV."""
    if agent_cfg:
        cfg = agent_cfg.get("model_config_json") or {}
        fast_id = cfg.get("fast_model_config_id") if isinstance(cfg, dict) else None
        if fast_id:
            llm = await get_llm_by_config_id(db, fast_id)
            if llm is not None:
                return llm
    return await get_effective_fast_llm(db)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _conversation_sandbox_root(conversation_id: str | None) -> Path | None:
    """Compute the sandbox root for a conversation.

    Returns ``{data_dir}/sandbox/{conversation_id}`` when a conversation ID
    is provided, or ``None`` for anonymous sessions (which fall back to the
    global sandbox directories).

    The path lives under ``data/`` so that DooD (Docker-outside-of-Docker)
    volume mounts work correctly — ``./data:/app/data`` is already mounted
    in docker-compose.yml.

    Note: ``_PROJECT_ROOT`` is ``src/`` (parents[3] from chat.py), so we go
    one level up to reach the actual project root where ``data/`` lives.
    """
    if not conversation_id:
        return None
    return _PROJECT_ROOT.parent / "data" / "sandbox" / conversation_id


async def _resolve_tools(
    agent_cfg: dict[str, Any] | None,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> ToolRegistry:
    """Build tool registry, optionally scoped to a per-conversation sandbox."""
    sandbox_root = _conversation_sandbox_root(conversation_id)
    sandbox_config = agent_cfg.get("sandbox_config") if agent_cfg else None
    # Per-conversation uploads dir so generated images are isolated and
    # cleaned up when the conversation is deleted.
    uploads_root: Path | None = None
    if conversation_id:
        uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
        uploads_root = uploads_base / "conversations" / conversation_id
    tools = get_tools(
        sandbox_root=sandbox_root,
        sandbox_config=sandbox_config,
        uploads_root=uploads_root,
    )
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
        from fim_agent.web.models.connector_call_log import ConnectorCallLog

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        agent_id_for_log = agent_cfg.get("agent_id") if agent_cfg else None

        async def _log_connector_call(**kwargs: Any) -> None:
            """Persist a connector call log entry in its own DB session."""
            try:
                async with create_session() as log_session:
                    log = ConnectorCallLog(
                        connector_id=kwargs.get("connector_id", ""),
                        connector_name=kwargs.get("connector_name", ""),
                        action_id=kwargs.get("action_id"),
                        action_name=kwargs.get("action_name", ""),
                        conversation_id=conversation_id,
                        user_id=user_id,
                        agent_id=agent_id_for_log,
                        request_method=kwargs.get("request_method", ""),
                        request_url=kwargs.get("request_url", ""),
                        response_status=kwargs.get("response_status"),
                        response_time_ms=kwargs.get("response_time_ms"),
                        success=kwargs.get("success", False),
                        error_message=kwargs.get("error_message"),
                    )
                    log_session.add(log)
                    await log_session.commit()
            except Exception:
                logger.debug("Failed to log connector call", exc_info=True)

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
                            connector_id=conn.id,
                            action_id=action.id,
                            on_call_complete=_log_connector_call,
                        )
                        tools.register(adapter)
                logger.info(
                    "Loaded %d connector tools from %d connectors",
                    sum(len(c.actions or []) for c in connectors),
                    len(connectors),
                )
        except Exception:
            logger.warning("Failed to load connector tools", exc_info=True)

    # Inject Connector Builder tools when this is a Builder Agent.
    if agent_cfg and "builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re
        _instructions = agent_cfg.get("instructions") or ""
        _m = _re.search(r"connector_id=([a-f0-9-]{36})", _instructions)
        if _m:
            _builder_cid = _m.group(1)
            from fim_agent.core.tool.builtin.connector_builder import (
                ConnectorListActionsTool,
                ConnectorCreateActionTool,
                ConnectorUpdateActionTool,
                ConnectorDeleteActionTool,
                ConnectorUpdateSettingsTool,
                ConnectorTestActionTool,
                ConnectorGetSettingsTool,
                ConnectorTestConnectionTool,
                ConnectorImportOpenAPITool,
            )
            for _BCls in [
                ConnectorListActionsTool,
                ConnectorCreateActionTool,
                ConnectorUpdateActionTool,
                ConnectorDeleteActionTool,
                ConnectorUpdateSettingsTool,
                ConnectorTestActionTool,
                ConnectorGetSettingsTool,
                ConnectorTestConnectionTool,
                ConnectorImportOpenAPITool,
            ]:
                tools.register(_BCls(connector_id=_builder_cid, user_id=user_id or ""))
            logger.info(
                "Injected connector builder tools for connector_id=%s", _builder_cid
            )

    # Inject Agent Builder tools when this is an Agent Builder Agent.
    if agent_cfg and "agent_builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re2
        _instructions2 = agent_cfg.get("instructions") or ""
        _m2 = _re2.search(r"target_agent_id=([a-f0-9-]{36})", _instructions2)
        if _m2:
            _builder_aid = _m2.group(1)
            from fim_agent.core.tool.builtin.agent_builder import (
                AgentGetSettingsTool,
                AgentUpdateSettingsTool,
                AgentListConnectorsTool,
                AgentAddConnectorTool,
                AgentRemoveConnectorTool,
                AgentSetModelTool,
            )
            for _BCls2 in [
                AgentGetSettingsTool,
                AgentUpdateSettingsTool,
                AgentListConnectorsTool,
                AgentAddConnectorTool,
                AgentRemoveConnectorTool,
                AgentSetModelTool,
            ]:
                tools.register(_BCls2(agent_id=_builder_aid, user_id=user_id or ""))
            logger.info(
                "Injected agent builder tools for agent_id=%s", _builder_aid
            )

    # Filter out globally disabled built-in tools (admin setting).
    try:
        from fim_agent.db import create_session as _cs_disabled
        from fim_agent.web.api.admin_utils import get_setting as _get_setting

        from fim_agent.web.api.admin import SETTING_DISABLED_BUILTIN_TOOLS as _SDBT
        async with _cs_disabled() as _disabled_db:
            _disabled_raw = await _get_setting(_disabled_db, _SDBT, default="[]")
        import json as _json
        _disabled_names = _json.loads(_disabled_raw)
        if isinstance(_disabled_names, list) and _disabled_names:
            tools = tools.exclude_by_name(*_disabled_names)
    except Exception:
        logger.warning("Failed to load disabled builtin tools setting", exc_info=True)

    # Load user-defined MCP servers — only fetch configs here; actual connection
    # happens inside the SSE generator (same coroutine) to avoid the anyio
    # cancel-scope cross-task RuntimeError on disconnect_all().
    try:
        from fim_agent.db import create_session as _create_session
        from fim_agent.web.models.mcp_server import MCPServer as _MCPServerModel
        from sqlalchemy import or_ as _or_, true as _true

        async with _create_session() as _mcp_db:
            _conditions = [_MCPServerModel.is_global == _true()]
            if user_id:
                _conditions.append(_MCPServerModel.user_id == user_id)
            _stmt = sa_select(_MCPServerModel).where(
                _or_(*_conditions),
                _MCPServerModel.is_active == _true(),
            )
            _result = await _mcp_db.execute(_stmt)
            _user_servers = _result.scalars().all()

        if _user_servers:
            tools._pending_mcp_servers = list(_user_servers)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("Failed to load MCP server configs", exc_info=True)

    return tools


async def _connect_pending_mcp_servers(tools: ToolRegistry) -> Any:
    """Connect to pending MCP servers and register their tools.

    Must be called from inside the SSE generator so that the anyio cancel
    scope created by stdio_client is entered and exited in the same coroutine.
    Returns the MCPClient (caller must call disconnect_all() in finally).
    """
    pending = getattr(tools, "_pending_mcp_servers", None)
    if not pending:
        return None

    from fim_agent.core.mcp import MCPClient as _MCPClient

    _mcp_client = _MCPClient()
    _loaded = 0
    for _srv in pending:
        try:
            if _srv.transport == "stdio" and _srv.command:
                if not is_stdio_allowed():
                    logger.warning(
                        "STDIO MCP disabled by ALLOW_STDIO_MCP=false, skipping %r",
                        _srv.name,
                    )
                    continue
                _mcp_tools = await _mcp_client.connect_stdio(
                    name=_srv.name,
                    command=_srv.command,
                    args=_srv.args or [],
                    env=_srv.env,
                    working_dir=_srv.working_dir,
                )
            elif _srv.transport == "sse" and _srv.url:
                _mcp_tools = await _mcp_client.connect_sse(
                    name=_srv.name,
                    url=_srv.url,
                    headers=_srv.headers,
                )
            elif _srv.transport == "streamable_http" and _srv.url:
                _mcp_tools = await _mcp_client.connect_streamable_http(
                    name=_srv.name,
                    url=_srv.url,
                    headers=_srv.headers,
                )
            else:
                continue
            for _t in _mcp_tools:
                tools.register(_t)
            _loaded += len(_mcp_tools)
        except Exception:
            logger.warning(
                "Failed to connect user MCP server %r",
                _srv.name,
                exc_info=True,
            )
    logger.info(
        "Loaded %d tools from %d user MCP servers",
        _loaded,
        len(pending),
    )
    return _mcp_client


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


async def _load_image_data_urls(
    image_ids: str, user_id: str | None,
) -> list[tuple[str, str, str]]:
    """Load images from disk and return list of ``(file_id, filename, data_url)``.

    File reads are offloaded to a thread so the event loop stays unblocked.

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
        raw = await asyncio.to_thread(file_path.read_bytes)
        b64 = await asyncio.to_thread(base64.b64encode, raw)
        data_url = f"data:{mime};base64,{b64.decode('ascii')}"
        results.append((fid, meta["filename"], data_url))

    return results


# ---------------------------------------------------------------------------
# SSE ticket endpoint — issue a short-lived one-time token for SSE auth
# ---------------------------------------------------------------------------


@router.post("/chat/ticket")
async def issue_sse_ticket(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    """Generate a short-lived JWT ticket for SSE authentication.

    The ticket is valid for 60 seconds and can be passed as the ``token``
    field in the JSON body of ``POST /api/react`` or ``POST /api/dag``.
    """
    from fim_agent.web.auth import create_sse_ticket
    return {"ticket": create_sse_ticket(str(current_user.id))}


# ---------------------------------------------------------------------------
# Inject endpoint — mid-stream message injection
# ---------------------------------------------------------------------------


class ChatStreamRequest(BaseModel):
    """Request body for ReAct/DAG streaming endpoints."""

    q: str
    conversation_id: str | None = None
    agent_id: str | None = None
    token: str | None = None
    image_ids: str | None = None
    user_metadata: str | None = None


class InjectMessageRequest(BaseModel):
    """Request body for injecting a message into an active agent execution."""

    conversation_id: str
    content: str


@router.post("/chat/inject")
async def inject_message(
    body: InjectMessageRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Inject a user message into a running agent execution.

    The message is queued for the agent to absorb at its next natural
    breakpoint (between iterations).  It is also persisted immediately
    to the conversation history.

    Returns 409 if no active execution exists for the conversation.
    """
    # Ownership check: verify the conversation belongs to the authenticated user.
    from fim_agent.web.models import Conversation as _ConvModel

    _conv_result = await db.execute(
        sa_select(_ConvModel.user_id).where(_ConvModel.id == body.conversation_id)
    )
    _conv_owner = _conv_result.scalar_one_or_none()
    if _conv_owner is None or str(_conv_owner) != str(current_user.id):
        raise AppError("forbidden", status_code=403)

    # Sensitive word check — block before persisting or queuing
    matched = await _check_sensitive_words(body.content, db)
    if matched:
        raise AppError(
            "sensitive_word_blocked",
            status_code=400,
            detail_args={"words": ", ".join(matched)},
        )

    msg_id = uuid.uuid4().hex[:12]
    broker = get_broker()
    delivered = await broker.inject(
        body.conversation_id,
        InjectedMessage(id=msg_id, content=body.content),
    )
    if not delivered:
        raise AppError("no_active_execution", status_code=404)

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
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, bool]:
    """Recall (cancel) a queued inject message before the agent consumes it."""
    # Ownership check: verify the conversation belongs to the authenticated user.
    from fim_agent.web.models import Conversation as _ConvModel

    _conv_result = await db.execute(
        sa_select(_ConvModel.user_id).where(_ConvModel.id == body.conversation_id)
    )
    _conv_owner = _conv_result.scalar_one_or_none()
    if _conv_owner is None or str(_conv_owner) != str(current_user.id):
        raise AppError("forbidden", status_code=403)

    recalled = await get_broker().recall(body.conversation_id, body.inject_id)
    return {"success": recalled}


# ---------------------------------------------------------------------------
# ReAct endpoint
# ---------------------------------------------------------------------------


@router.post("/react")
async def react_endpoint(
    request: Request,
    body: ChatStreamRequest,
) -> StreamingResponse:
    """Run a ReAct agent query with SSE progress updates.

    Parameters
    ----------
    request : Request
        The incoming HTTP request; used to detect client disconnects.
    body : ChatStreamRequest
        JSON request body containing the query and optional parameters.
    """
    q = body.q
    conversation_id = body.conversation_id
    agent_id = body.agent_id
    token = body.token
    image_ids = body.image_ids
    user_metadata_str = body.user_metadata

    # Sensitive word check — block before starting the agent
    from fim_agent.db import create_session as _create_sw_session

    async with _create_sw_session() as _sw_db:
        matched = await _check_sensitive_words(q, _sw_db)
    if matched:
        raise AppError(
            "sensitive_word_blocked",
            status_code=400,
            detail_args={"words": ", ".join(matched)},
        )

    # -- Pre-stream resolution (before StreamingResponse) -------------------
    current_user_id, user_system_instructions, preferred_language = await _resolve_user(token)
    if current_user_id is None:
        raise AppError("authentication_required", status_code=401)

    # Run independent validations and agent config resolution in parallel
    _parallel_tasks: list[Any] = [_check_token_quota(current_user_id)]
    if conversation_id:
        _parallel_tasks.append(_validate_conversation_ownership(conversation_id, current_user_id))
    _parallel_tasks.append(_resolve_agent_config(agent_id, conversation_id, user_id=current_user_id))
    _parallel_results = await asyncio.gather(*_parallel_tasks)
    # _resolve_agent_config is always the last task appended
    agent_cfg = _parallel_results[-1]

    from fim_agent.db import create_session as _create_session
    try:
        async with _create_session() as _llm_db:
            llm = await _resolve_llm(agent_cfg, _llm_db)
            fast_llm = await _resolve_fast_llm(agent_cfg, _llm_db)
            _context_budget = await get_effective_context_budget(_llm_db)
    except ValueError as exc:
        raise AppError(
            "agent_config_error",
            status_code=500,
            detail=str(exc),
            detail_args={"reason": str(exc)},
        ) from exc
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

    # Load attached images (async to avoid blocking the event loop)
    image_data: list[tuple[str, str, str]] = []
    if image_ids:
        image_data = await _load_image_data_urls(image_ids, current_user_id)

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []
        yield _emit(sse_events, "step", {"type": "thinking", "status": "start", "iteration": 1})

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        user_mcp_client = await _connect_pending_mcp_servers(tools)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_agent.db import create_session
                from fim_agent.web.models import Message as MessageModel

                db_session = create_session()
                # Build final metadata by merging image info and caller-provided user_metadata
                extra_meta: dict[str, Any] = {}
                if user_metadata_str:
                    try:
                        parsed = json.loads(user_metadata_str)
                        if isinstance(parsed, dict):
                            extra_meta = parsed
                    except json.JSONDecodeError:
                        pass
                final_metadata: dict[str, Any] = {}
                if image_data:
                    final_metadata["images"] = [
                        {
                            "file_id": fid,
                            "filename": fname,
                            "mime_type": durl.split(";")[0].split(":")[1],
                        }
                        for fid, fname, durl in image_data
                    ]
                if extra_meta:
                    final_metadata.update(extra_meta)
                user_msg = MessageModel(
                    conversation_id=conversation_id,
                    role="user",
                    content=q,
                    message_type="text",
                    metadata_=final_metadata if final_metadata else None,
                )
                db_session.add(user_msg)
                await db_session.commit()
                # Release connection back to pool immediately — the session
                # is not needed during the long-running LLM execution phase.
                await db_session.close()
                db_session = None
            except Exception:
                logger.warning(
                    "Failed to persist user message for conversation %s",
                    conversation_id,
                    exc_info=True,
                )
                if db_session:
                    await db_session.close()
                db_session = None

        # Register interrupt queue for mid-stream injection.
        interrupt_queue: InterruptQueue | None = None
        if conversation_id:
            interrupt_queue = await get_broker().register(conversation_id)

        progress_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        done_event = asyncio.Event()
        iter_start = time.time()
        thinking_done_iter = 0  # track which iteration's thinking-done was emitted
        current_iteration = 1  # track the current iteration for _on_answer_token
        answer_started = False
        answer_streaming_started = False  # True when real streaming via on_answer_token

        def _on_answer_token(token: str, reasoning: str = "") -> None:
            """Push real-time answer tokens to the SSE progress queue.

            Called from ReAct native mode when streaming the final answer
            token-by-token.  Emits answer:start once, then answer:delta
            for each token.

            Args:
                token: The answer text delta.
                reasoning: Accumulated reasoning/thinking content from the
                    model (e.g. DeepSeek R1).  Used on the first call to
                    populate the thinking:done event.
            """
            nonlocal answer_streaming_started, thinking_done_iter
            if not answer_streaming_started:
                answer_streaming_started = True

                # Close the thinking phase BEFORE starting the answer stream,
                # so the UI doesn't show a loading spinner on the thinking card
                # while answer tokens are already rendering.
                if thinking_done_iter < current_iteration:
                    _emit_step({
                        "type": "thinking",
                        "status": "done",
                        "iteration": current_iteration,
                        "reasoning": reasoning,
                    })
                    thinking_done_iter = current_iteration

                evt_start = {"status": "start"}
                sse_events.append({"event": "answer", "data": evt_start})
                try:
                    progress_queue.put_nowait(_sse("answer", evt_start))
                except asyncio.QueueFull:
                    pass
            evt_delta = {"status": "delta", "content": token}
            sse_events.append({"event": "answer", "data": evt_delta})
            try:
                progress_queue.put_nowait(_sse("answer", evt_delta))
            except asyncio.QueueFull:
                logger.warning("SSE progress queue full, dropping answer token")

        def _emit_step(payload: dict[str, Any]) -> None:
            """Emit a step SSE event to both persistence list and queue."""
            sse_events.append({"event": "step", "data": payload})
            try:
                progress_queue.put_nowait(_sse("step", payload))
            except asyncio.QueueFull:
                logger.warning("SSE progress queue full, dropping event")

        def on_iteration(
            iteration: int,
            action: Any,
            observation: str | None,
            error: str | None,
            step_result: Any = None,
        ) -> None:
            nonlocal iter_start, thinking_done_iter, current_iteration, answer_started

            # Handle injected user messages as a special SSE event.
            if getattr(action, "tool_name", None) == "__inject__":
                inject_payload: dict[str, Any] = {
                    "type": "inject",
                    "content": action.tool_args.get("content", ""),
                }
                if action.tool_args.get("id"):
                    inject_payload["id"] = action.tool_args["id"]
                sse_events.append({"event": "inject", "data": inject_payload})
                try:
                    progress_queue.put_nowait(_sse("inject", inject_payload))
                except asyncio.QueueFull:
                    logger.warning("SSE progress queue full, dropping event")
                return

            # Handle tool selection phase indicator.
            if getattr(action, "tool_name", None) == "__selecting_tools__":
                phase_payload: dict[str, Any] = {
                    "phase": "selecting_tools",
                    "total_tools": action.tool_args.get("total", 0),
                }
                sse_events.append({"event": "phase", "data": phase_payload})
                try:
                    progress_queue.put_nowait(_sse("phase", phase_payload))
                except asyncio.QueueFull:
                    logger.warning("SSE progress queue full, dropping event")
                return

            # -- Thinking start signal (emitted by agent before LLM call) --
            if action.type == "thinking":
                current_iteration = iteration
                # Iteration 1 already emitted as the initial event above.
                if iteration > 1:
                    _emit_step({
                        "type": "thinking",
                        "status": "start",
                        "iteration": iteration,
                    })
                return

            # -- Tool call lifecycle --
            if action.type == "tool_call":
                is_starting = observation is None and error is None

                if is_starting:
                    # Emit thinking done (once per iteration, before first tool)
                    if thinking_done_iter < iteration:
                        _emit_step({
                            "type": "thinking",
                            "status": "done",
                            "iteration": iteration,
                            "reasoning": action.reasoning,
                        })
                        thinking_done_iter = iteration

                    # Emit iteration start
                    iter_start = time.time()
                    _emit_step({
                        "type": "iteration",
                        "status": "start",
                        "iteration": iteration,
                        "tool_name": action.tool_name,
                        "tool_args": action.tool_args,
                        "reasoning": action.reasoning,
                    })
                else:
                    # Emit iteration done
                    iter_elapsed = round(time.time() - iter_start, 2)
                    payload: dict[str, Any] = {
                        "type": "iteration",
                        "status": "done",
                        "iteration": iteration,
                        "tool_name": action.tool_name,
                        "tool_args": action.tool_args,
                        "reasoning": action.reasoning,
                        "observation": observation,
                        "error": error,
                        "iter_elapsed": iter_elapsed,
                    }
                    if step_result is not None:
                        if getattr(step_result, "content_type", None):
                            payload["content_type"] = step_result.content_type
                        if getattr(step_result, "artifacts", None):
                            payload["artifacts"] = [
                                {
                                    "name": a["name"],
                                    "url": f"/api/conversations/{conversation_id}/artifacts/{a['path'].split('/')[-1].split('_', 1)[0]}",
                                    "mime_type": a["mime_type"],
                                    "size": a["size"],
                                }
                                for a in step_result.artifacts
                            ] if conversation_id else step_result.artifacts
                    _emit_step(payload)
                return

            # -- Final answer --
            if action.type == "final_answer":
                if thinking_done_iter < iteration:
                    _emit_step({
                        "type": "thinking",
                        "status": "done",
                        "iteration": iteration,
                        "reasoning": action.reasoning,
                    })
                    thinking_done_iter = iteration

                # Signal answer generation phase start (skip if real
                # streaming via on_answer_token already emitted these).
                if not answer_streaming_started:
                    iter_start = time.time()
                    _emit_step({"type": "answer", "status": "start"})
                answer_started = True
                return

        try:
            fast_usage_tracker = UsageTracker()
            memory = None
            if conversation_id:
                from fim_agent.core.memory import DbMemory
                memory = DbMemory(
                    conversation_id=conversation_id,
                    max_tokens=_context_budget,
                    compact_llm=fast_llm,
                    user_id=current_user_id,
                    usage_tracker=fast_usage_tracker,
                )
            context_guard = ContextGuard(
                compact_llm=fast_llm,
                default_budget=_context_budget,
                usage_tracker=fast_usage_tracker,
            )

            # Inject fast usage tracker into grounded retrieve tool
            from fim_agent.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool
            for tool in tools._tools.values():
                if isinstance(tool, GroundedRetrieveTool):
                    tool.set_usage_tracker(fast_usage_tracker)

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
                        on_answer_token=_on_answer_token,
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
                        try:
                            with suppress(asyncio.CancelledError, TimeoutError):
                                await asyncio.wait_for(run_task, timeout=5.0)
                        except Exception:
                            logger.exception("Unexpected error while cancelling ReAct task")
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

            # Emit answer start if callback didn't (e.g. max iterations exceeded)
            if not answer_started:
                yield _emit(sse_events, "step", {"type": "answer", "status": "start"})
                iter_start = time.time()

            # Notify frontend if context was compacted
            if memory and memory.was_compacted:
                compact_payload = {
                    "original_messages": memory._original_count,
                    "kept_messages": memory._compacted_count,
                }
                yield _emit(sse_events, "compact", compact_payload)

            # -- Stream answer to client -----------------------------------
            if answer_streaming_started:
                # Real streaming already happened via on_answer_token — just
                # close the answer stream.  Events are already in sse_events.
                yield _emit(sse_events, "answer", {"status": "done"})
            else:
                # JSON mode or non-streaming fallback — fake chunking.
                yield _emit(sse_events, "answer", {"status": "start"})
                for _ans_chunk in _chunk_answer(result.answer):
                    yield _emit(sse_events, "answer", {"status": "delta", "content": _ans_chunk})
                yield _emit(sse_events, "answer", {"status": "done"})

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
                remaining = await interrupt_queue.drain()
                if remaining:
                    done_payload["pending_injections"] = [m.content for m in remaining]

            sse_events.append({"event": "done", "data": done_payload})

            # -- Re-open a fresh DB session for persistence ----------------
            # The original session was closed right after saving the user
            # message to avoid holding a connection during LLM execution.
            if conversation_id:
                from fim_agent.db import create_session
                db_session = create_session()

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
                            if conv.model_name is None and llm.model_id:
                                conv.model_name = llm.model_id
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for "
                        "conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            # -- Yield done IMMEDIATELY (no suggestions/title yet) ------
            yield _sse("done", done_payload)

            # -- Async post-answer metadata (after done, before end) ----
            async def _maybe_generate_title() -> str | None:
                """Generate title if this is the first message."""
                if not (db_session and conversation_id):
                    return None
                try:
                    from sqlalchemy import func as _sa_func
                    from fim_agent.web.models import (
                        Conversation,
                        Message as _MsgModel,
                    )
                    msg_count = (
                        await db_session.execute(
                            sa_select(_sa_func.count())
                            .select_from(_MsgModel)
                            .where(_MsgModel.conversation_id == conversation_id)
                        )
                    ).scalar() or 0
                    if msg_count <= 2:
                        return await _generate_title(
                            fast_llm, q, result.answer,
                            preferred_language=preferred_language,
                            usage_tracker=fast_usage_tracker,
                        )
                except Exception:
                    logger.debug("Auto-title generation failed", exc_info=True)
                return None

            suggestions, gen_title = await asyncio.gather(
                _generate_suggestions(
                    fast_llm, q, result.answer,
                    preferred_language=preferred_language,
                    usage_tracker=fast_usage_tracker,
                ),
                _maybe_generate_title(),
            )
            if suggestions:
                yield _sse("suggestions", {"items": suggestions})
            if gen_title:
                yield _sse("title", {"title": gen_title})
                if db_session and conversation_id:
                    try:
                        from sqlalchemy import update as _sa_update
                        from fim_agent.web.models import Conversation
                        await db_session.execute(
                            _sa_update(Conversation)
                            .where(Conversation.id == conversation_id)
                            .values(title=gen_title)
                        )
                        await db_session.commit()
                    except Exception:
                        logger.debug("Failed to persist title", exc_info=True)

            # Capture fast LLM token usage (after all fast calls including suggestions)
            fast_summary = fast_usage_tracker.get_summary()
            if fast_summary.total_tokens > 0:
                if "usage" in done_payload:
                    done_payload["usage"]["fast_llm_tokens"] = fast_summary.total_tokens
                if db_session and conversation_id:
                    try:
                        from sqlalchemy import update as _sa_update, func as _sa_func
                        from fim_agent.web.models import Conversation
                        await db_session.execute(
                            _sa_update(Conversation)
                            .where(Conversation.id == conversation_id)
                            .values(
                                total_tokens=_sa_func.coalesce(Conversation.total_tokens, 0) + fast_summary.total_tokens,
                                fast_llm_tokens=_sa_func.coalesce(Conversation.fast_llm_tokens, 0) + fast_summary.total_tokens,
                            )
                        )
                        await db_session.commit()
                    except Exception:
                        logger.warning("Failed to persist fast LLM tokens", exc_info=True)

            yield _sse("end", {})
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
            yield _sse("end", {})
        finally:
            if user_mcp_client:
                await user_mcp_client.disconnect_all()
            if conversation_id:
                await get_broker().unregister(conversation_id)
            if db_session:
                await db_session.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
        },
    )


# ---------------------------------------------------------------------------
# DAG endpoint
# ---------------------------------------------------------------------------


@router.post("/dag")
async def dag_endpoint(
    request: Request,
    body: ChatStreamRequest,
) -> StreamingResponse:
    """Run a DAG planner pipeline with SSE progress updates.

    Parameters
    ----------
    request : Request
        The incoming HTTP request; used to detect client disconnects.
    body : ChatStreamRequest
        JSON request body containing the query and optional parameters.
    """
    q = body.q
    conversation_id = body.conversation_id
    agent_id = body.agent_id
    token = body.token
    image_ids = body.image_ids
    dag_user_metadata_str = body.user_metadata

    # -- Pre-stream resolution ----------------------------------------------
    current_user_id, user_system_instructions, preferred_language = await _resolve_user(token)
    if current_user_id is None:
        raise AppError("authentication_required", status_code=401)

    # Run independent validations and agent config resolution in parallel
    _parallel_tasks_dag: list[Any] = [_check_token_quota(current_user_id)]
    if conversation_id:
        _parallel_tasks_dag.append(_validate_conversation_ownership(conversation_id, current_user_id))
    _parallel_tasks_dag.append(_resolve_agent_config(agent_id, conversation_id, user_id=current_user_id))
    _parallel_results_dag = await asyncio.gather(*_parallel_tasks_dag)
    # _resolve_agent_config is always the last task appended
    agent_cfg = _parallel_results_dag[-1]

    from fim_agent.db import create_session as _create_session
    try:
        async with _create_session() as _llm_db:
            llm = await _resolve_llm(agent_cfg, _llm_db)
            _fast_context_budget = await get_effective_fast_context_budget(_llm_db)
            _context_budget = await get_effective_context_budget(_llm_db)
    except ValueError as exc:
        raise AppError(
            "agent_config_error",
            status_code=500,
            detail=str(exc),
            detail_args={"reason": str(exc)},
        ) from exc
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

    # DAG uses a fast LLM for step execution; role='fast' -> role='general' -> ENV fallback.
    async with _create_session() as _fast_llm_db:
        fast_llm = await _resolve_fast_llm(agent_cfg, _fast_llm_db)

    # Load attached images (async to avoid blocking the event loop)
    dag_image_data: list[tuple[str, str, str]] = []
    if image_ids:
        dag_image_data = await _load_image_data_urls(image_ids, current_user_id)

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        dag_user_mcp_client = await _connect_pending_mcp_servers(tools)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_agent.db import create_session
                from fim_agent.web.models import Message as MessageModel

                db_session = create_session()
                # Build final metadata by merging image info and caller-provided user_metadata
                dag_extra_meta: dict[str, Any] = {}
                if dag_user_metadata_str:
                    try:
                        parsed = json.loads(dag_user_metadata_str)
                        if isinstance(parsed, dict):
                            dag_extra_meta = parsed
                    except json.JSONDecodeError:
                        pass
                dag_final_metadata: dict[str, Any] = {}
                if dag_image_data:
                    dag_final_metadata["images"] = [
                        {
                            "file_id": fid,
                            "filename": fname,
                            "mime_type": durl.split(";")[0].split(":")[1],
                        }
                        for fid, fname, durl in dag_image_data
                    ]
                if dag_extra_meta:
                    dag_final_metadata.update(dag_extra_meta)
                user_msg = MessageModel(
                    conversation_id=conversation_id,
                    role="user",
                    content=q,
                    message_type="text",
                    metadata_=dag_final_metadata if dag_final_metadata else None,
                )
                db_session.add(user_msg)
                await db_session.commit()
                # Release connection back to pool immediately — the session
                # is not needed during the long-running LLM/DAG execution.
                await db_session.close()
                db_session = None
            except Exception:
                logger.warning(
                    "Failed to persist user message for conversation %s",
                    conversation_id,
                    exc_info=True,
                )
                if db_session:
                    await db_session.close()
                db_session = None

        # Register interrupt queue for mid-stream injection.
        dag_interrupt_queue: InterruptQueue | None = None
        if conversation_id:
            dag_interrupt_queue = await get_broker().register(conversation_id)

        fast_usage_tracker = UsageTracker()

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
                    max_tokens=_context_budget,
                    compact_llm=fast_llm,
                    user_id=current_user_id,
                    usage_tracker=fast_usage_tracker,
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
                            if summary_result.usage:
                                await fast_usage_tracker.record(summary_result.usage)
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

        progress_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        done_event = asyncio.Event()

        def on_step_progress(step_id: str, event: str, data: dict[str, Any]) -> None:
            # Convert raw artifact dicts (path-based) to download URLs.
            if conversation_id and "artifacts" in data and data["artifacts"]:
                data["artifacts"] = [
                    {
                        "name": a["name"],
                        "url": f"/api/conversations/{conversation_id}/artifacts/{a['path'].split('/')[-1].split('_', 1)[0]}",
                        "mime_type": a["mime_type"],
                        "size": a["size"],
                    }
                    for a in data["artifacts"]
                ]
            step_payload = {"step_id": step_id, "event": event, **data}
            sse_events.append({"event": "step_progress", "data": step_payload})
            try:
                progress_queue.put_nowait(_sse("step_progress", step_payload))
            except asyncio.QueueFull:
                logger.warning("SSE progress queue full, dropping event")

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
                    for injected in await dag_interrupt_queue.drain():
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
                    default_budget=_fast_context_budget,
                    usage_tracker=fast_usage_tracker,
                )

                # Inject fast usage tracker into grounded retrieve tool
                from fim_agent.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool
                for tool in tools._tools.values():
                    if isinstance(tool, GroundedRetrieveTool):
                        tool.set_usage_tracker(fast_usage_tracker)

                agent = ReActAgent(
                    llm=fast_llm,
                    tools=tools,
                    extra_instructions=extra_instructions,
                    max_iterations=dag_step_max_iters,
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
                            try:
                                with suppress(asyncio.CancelledError, TimeoutError):
                                    await asyncio.wait_for(exec_task, timeout=5.0)
                            except Exception:
                                logger.exception("Unexpected error while cancelling DAG exec task")
                            return
                        # Drain inject queue during execution for real-time feedback.
                        if dag_interrupt_queue is not None:
                            for injected in await dag_interrupt_queue.drain():
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
                    for injected in await dag_interrupt_queue.drain():
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

            # Notify frontend if context was compacted
            if dag_memory and dag_memory.was_compacted:
                compact_payload = {
                    "original_messages": dag_memory._original_count,
                    "kept_messages": dag_memory._compacted_count,
                }
                yield _emit(sse_events, "compact", compact_payload)

            # -- Stream answer to client immediately --------------------
            yield _emit(sse_events, "answer", {"status": "start"})

            if analysis.achieved:
                # Real streaming synthesis from LLM
                answer_chunks: list[str] = []
                try:
                    async for _token in analyzer.stream_synthesize(
                        enriched_query, plan, analysis,
                    ):
                        answer_chunks.append(_token)
                        yield _emit(
                            sse_events, "answer",
                            {"status": "delta", "content": _token},
                        )
                    answer = "".join(answer_chunks)
                except Exception:
                    logger.warning(
                        "stream_synthesize failed, falling back to "
                        "analysis.final_answer",
                        exc_info=True,
                    )
                    answer = analysis.final_answer or ""
                    for _ans_chunk in _chunk_answer(answer):
                        yield _emit(
                            sse_events, "answer",
                            {"status": "delta", "content": _ans_chunk},
                        )
            else:
                # Goal not achieved — use fallback answer
                completed = [
                    s for s in plan.steps
                    if s.status == "completed" and s.result
                ]
                if completed:
                    answer = "\n\n---\n\n".join(
                        f"**{s.id}**: {s.result}" for s in completed
                    )
                else:
                    answer = "(goal not achieved)"
                for _ans_chunk in _chunk_answer(answer):
                    yield _emit(
                        sse_events, "answer",
                        {"status": "delta", "content": _ans_chunk},
                    )

            yield _emit(sse_events, "answer", {"status": "done"})

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
                remaining = await dag_interrupt_queue.drain()
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

            # -- Re-open a fresh DB session for persistence ----------------
            # The original session was closed right after saving the user
            # message to avoid holding a connection during DAG execution.
            if conversation_id:
                from fim_agent.db import create_session
                db_session = create_session()

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
                            if conv.model_name is None and llm.model_id:
                                conv.model_name = llm.model_id
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for "
                        "conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            # -- Yield done IMMEDIATELY (no suggestions/title yet) ------
            yield _sse("done", dag_done_payload)

            # -- Async post-answer metadata (after done, before end) ----
            async def _dag_maybe_generate_title() -> str | None:
                """Generate title if this is the first message."""
                if not (db_session and conversation_id):
                    return None
                try:
                    from sqlalchemy import func as _sa_func
                    from fim_agent.web.models import (
                        Conversation as ConvModel,
                        Message as _MsgModel,
                    )
                    msg_count = (
                        await db_session.execute(
                            sa_select(_sa_func.count())
                            .select_from(_MsgModel)
                            .where(_MsgModel.conversation_id == conversation_id)
                        )
                    ).scalar() or 0
                    if msg_count <= 2:
                        return await _generate_title(
                            fast_llm, q, answer,
                            preferred_language=preferred_language,
                            usage_tracker=fast_usage_tracker,
                        )
                except Exception:
                    logger.debug("Auto-title generation failed", exc_info=True)
                return None

            dag_suggestions, dag_gen_title = await asyncio.gather(
                _generate_suggestions(
                    fast_llm, q, answer,
                    preferred_language=preferred_language,
                    usage_tracker=fast_usage_tracker,
                ),
                _dag_maybe_generate_title(),
            )
            if dag_suggestions:
                yield _sse("suggestions", {"items": dag_suggestions})
            if dag_gen_title:
                yield _sse("title", {"title": dag_gen_title})
                if db_session and conversation_id:
                    try:
                        from sqlalchemy import update as _sa_update
                        from fim_agent.web.models import Conversation as ConvModel
                        await db_session.execute(
                            _sa_update(ConvModel)
                            .where(ConvModel.id == conversation_id)
                            .values(title=dag_gen_title)
                        )
                        await db_session.commit()
                    except Exception:
                        logger.debug("Failed to persist title", exc_info=True)

            # Capture fast LLM token usage (after all fast calls including suggestions)
            fast_summary = fast_usage_tracker.get_summary()
            if fast_summary.total_tokens > 0:
                if "usage" in dag_done_payload:
                    dag_done_payload["usage"]["fast_llm_tokens"] = fast_summary.total_tokens
                if db_session and conversation_id:
                    try:
                        from sqlalchemy import update as _sa_update, func as _sa_func
                        from fim_agent.web.models import Conversation as ConvModel
                        await db_session.execute(
                            _sa_update(ConvModel)
                            .where(ConvModel.id == conversation_id)
                            .values(
                                total_tokens=_sa_func.coalesce(ConvModel.total_tokens, 0) + fast_summary.total_tokens,
                                fast_llm_tokens=_sa_func.coalesce(ConvModel.fast_llm_tokens, 0) + fast_summary.total_tokens,
                            )
                        )
                        await db_session.commit()
                    except Exception:
                        logger.warning("Failed to persist fast LLM tokens", exc_info=True)

            yield _sse("end", {})
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
            yield _sse("end", {})
        finally:
            if dag_user_mcp_client:
                await dag_user_mcp_client.disconnect_all()
            if conversation_id:
                await get_broker().unregister(conversation_id)
            if db_session:
                await db_session.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
        },
    )
