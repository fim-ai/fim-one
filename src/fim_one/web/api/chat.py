"""SSE chat endpoints for ReAct and DAG agent modes.

Both endpoints stream Server-Sent Events with the following event names:

- ``step``           – ReAct iteration progress (tool calls, thinking).
- ``step_progress``  – DAG per-step progress (started / iteration / completed).
- ``phase``          – Pipeline phase transitions (selecting_tools / planning / executing / analyzing).
- ``compact``        – Context compaction occurred (original_messages, kept_messages).
- ``answer``         – Streamed answer text (start / delta / done) emitted before ``done``.
- ``done``           – Final result payload (answer complete, emitted immediately).
- ``end``            – Stream terminator (emitted right after ``done``, NOT persisted).
  Suggestions and title generation run as background tasks and are persisted
  directly to the database (message metadata and conversation title).

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

from fim_one.web.exceptions import AppError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.agent import ReActAgent
from fim_one.core.model import BaseLLM
from fim_one.core.model.fallback import FallbackLLM
from fim_one.core.model.structured import StructuredOutputError
from fim_one.core.model.types import ChatMessage
from fim_one.core.model.usage import UsageSummary, UsageTracker
from fim_one.core.planner import (
    AnalysisResult,
    DAGExecutor,
    DAGPlanner,
    ExecutionPlan,
    PlanAnalyzer,
)
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.security import is_stdio_allowed
from fim_one.core.tool import ToolRegistry
from fim_one.core.utils import extract_json_value, get_language_directive

from ..deps import (
    get_context_budget,
    get_dag_max_replan_rounds,
    get_dag_replan_stop_confidence,
    get_dag_step_max_iterations,
    get_dag_step_verification,
    get_dag_tool_cache_enabled,
    get_effective_context_budget,
    get_effective_fast_context_budget,
    get_fast_context_budget,
    get_fast_llm,
    get_llm,
    get_llm_by_config_id,
    get_llm_from_config,
    get_max_concurrency,
    get_model_registry_with_group,
    get_react_max_iterations,
    get_react_max_turn_tokens,
    get_tools,
)
from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_current_user_optional
from fim_one.web.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensitive word check helper
# ---------------------------------------------------------------------------


async def _check_sensitive_words(text: str, db: AsyncSession) -> list[str]:
    """Return list of matched blocked words. Empty = clean."""
    from fim_one.web.models import SensitiveWord

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

from fim_one.web.interrupt import (
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

# Chars per step result in the most recent replan round.
_REPLAN_RECENT_TRUNCATION = int(os.getenv("DAG_REPLAN_RECENT_TRUNCATION", "500"))
# Chars per step result in older replan rounds.
_REPLAN_OLDER_TRUNCATION = int(os.getenv("DAG_REPLAN_OLDER_TRUNCATION", "200"))
_SKILL_STUB_DESC_LEN = int(os.getenv("SKILL_STUB_DESC_LENGTH", "120"))


def _format_replan_context(
    round_history: list[tuple[ExecutionPlan, AnalysisResult]],
) -> str:
    """Format ALL previous rounds' results as context for re-planning.

    Older rounds are truncated more aggressively to keep prompt size
    manageable while still giving the planner visibility into the full
    trajectory:
    - Most recent round (N-1): ``DAG_REPLAN_RECENT_TRUNCATION`` chars per step
    - Older rounds (N-2 and earlier): ``DAG_REPLAN_OLDER_TRUNCATION`` chars per step
    """
    if not round_history:
        return ""

    lines: list[str] = []
    total_rounds = len(round_history)

    for idx, (plan, analysis) in enumerate(round_history):
        is_latest = idx == total_rounds - 1
        # More generous truncation for the latest round
        truncation_limit = _REPLAN_RECENT_TRUNCATION if is_latest else _REPLAN_OLDER_TRUNCATION

        lines.append(f"--- Round {plan.current_round} ---")
        lines.append(f"Analyzer reasoning: {analysis.reasoning}")
        lines.append(f"Achieved: {analysis.achieved}, Confidence: {analysis.confidence}")
        lines.append("Step results:")
        for step in plan.steps:
            status_info = f"[{step.id}] status={step.status}"
            if step.result:
                result = step.result.summary if step.result else "(no output)"
                if len(result) > truncation_limit:
                    result_preview = result[:truncation_limit] + "... (truncated)"
                else:
                    result_preview = result
                lines.append(f"  {status_info}: {result_preview}")
            else:
                lines.append(f"  {status_info}: (no output)")
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

        raw = str(result.message.content or "").strip()
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

        raw = str(result.message.content or "").strip().strip("\"'")
        if usage_tracker and result.usage:
            await usage_tracker.record(result.usage)

        if raw and len(raw) <= 100:
            return raw
        return None
    except Exception:
        logger.debug("_generate_title failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Deliverable classification helper
# ---------------------------------------------------------------------------


async def _classify_deliverables(
    fast_llm: "BaseLLM",
    answer: str,
    artifacts: list[dict[str, Any]],
    *,
    usage_tracker: "UsageTracker | None" = None,
) -> list[dict[str, Any]]:
    """Classify which artifacts are final deliverables vs intermediate outputs.

    Uses a fast LLM to decide which artifacts from an agent execution are the
    final outputs the user actually wants.  When there is only one artifact it
    is returned directly without an LLM call.

    On any failure the function returns **all** artifacts so that nothing is
    accidentally hidden from the user (graceful degradation).
    """
    if not artifacts:
        return []
    if len(artifacts) == 1:
        return artifacts

    try:
        system_prompt = (
            "You classify which artifacts from an AI agent execution are final "
            "deliverables (outputs the user actually wants) vs intermediate outputs "
            "(search results, intermediate computations, drafts that were superseded).\n\n"
            "Return ONLY a JSON array of the artifact indices that are deliverables.\n"
            "Example: [0, 3] means artifacts 0 and 3 are deliverables.\n"
            "If ALL artifacts are deliverables, return all indices.\n"
            "If NONE are clearly deliverables, return all indices."
        )

        artifact_lines: list[str] = []
        for i, a in enumerate(artifacts):
            name = a.get("name", "untitled")
            mime = a.get("mime_type", "unknown")
            tool = a.get("tool_name", "")
            artifact_lines.append(f"[{i}] {name} ({mime}, from {tool})")

        truncated_answer = answer[:2000]
        user_content = (
            f"Agent answer (truncated):\n{truncated_answer}\n\n"
            f"Artifacts:\n" + "\n".join(artifact_lines)
        )

        result = await fast_llm.chat([
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ])

        raw = str(result.message.content or "").strip()
        if usage_tracker and result.usage:
            await usage_tracker.record(result.usage)

        indices = extract_json_value(raw)
        if isinstance(indices, list) and all(isinstance(i, int) for i in indices):
            valid = [idx for idx in indices if 0 <= idx < len(artifacts)]
            if valid:
                return [artifacts[idx] for idx in valid]

        logger.debug(
            "_classify_deliverables: unexpected JSON structure: %s",
            type(indices),
        )
        return artifacts
    except Exception:
        logger.debug("_classify_deliverables failed", exc_info=True)
        return artifacts


# ---------------------------------------------------------------------------
# Auth & agent resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_user(
    token: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Validate a JWT/API-key token and return
    ``(user_id, system_instructions, preferred_language, timezone)``.

    Returns ``(None, None, None, None)`` when *token* is not provided.
    Raises HTTPException(401) on invalid/expired tokens.
    """
    if not token:
        return None, None, None, None

    # -- API key authentication (fim_-prefixed tokens) ----------------------
    if token.startswith("fim_"):
        from fim_one.db import create_session
        from fim_one.web.auth import _authenticate_api_key

        async with create_session() as session:
            user = await _authenticate_api_key(token, session)
            return (
                user.id,
                user.system_instructions,
                user.preferred_language,
                getattr(user, "timezone", None),
            )

    # -- JWT authentication -------------------------------------------------
    import jwt as pyjwt
    from fim_one.web.auth import SECRET_KEY, ALGORITHM

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
    user_timezone: str | None = None
    try:
        from fim_one.db import create_session
        from fim_one.web.models import User

        async with create_session() as session:
            result = await session.execute(
                sa_select(User).where(User.id == user_id)
            )
            user_maybe = result.scalar_one_or_none()
            if user_maybe is None:
                raise AppError("user_not_found", status_code=401)
            user = user_maybe

            # Fix #11a: reject disabled accounts
            if not user.is_active:
                return None, None, None, None

            # Fix #11b: reject tokens issued before a force-logout event
            if user.tokens_invalidated_at is not None:
                iat = payload.get("iat")
                if iat is None:
                    return None, None, None, None
                token_issued = (
                    datetime.fromtimestamp(iat, tz=UTC)
                    if isinstance(iat, (int, float))
                    else iat
                )
                if token_issued <= user.tokens_invalidated_at.replace(tzinfo=UTC):
                    return None, None, None, None

            system_instructions = user.system_instructions
            preferred_language = user.preferred_language
            user_timezone = getattr(user, "timezone", None)
    except HTTPException:
        raise
    except Exception:
        logger.warning("Failed to load user record", exc_info=True)

    return user_id, system_instructions, preferred_language, user_timezone


async def _validate_conversation_ownership(
    conversation_id: str, user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*.  Raises 404 otherwise."""
    from fim_one.db import create_session
    from fim_one.web.models import Conversation

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
    from fim_one.db import create_session
    from fim_one.web.api.admin_utils import get_setting
    from fim_one.web.models import Conversation, User

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
    from fim_one.db import create_session
    from fim_one.web.models import Agent, Conversation

    resolved_id = agent_id

    if not resolved_id and conversation_id:
        async with create_session() as session:
            conv_result = await session.execute(
                sa_select(Conversation.agent_id).where(
                    Conversation.id == conversation_id,
                )
            )
            row = conv_result.scalar_one_or_none()
            if row:
                resolved_id = row

    if not resolved_id:
        return None

    async with create_session() as session:
        stmt = sa_select(Agent).where(Agent.id == resolved_id)
        if user_id:
            from fim_one.web.visibility import resolve_visibility as _agent_vis
            _vis_filter, _, _ = await _agent_vis(Agent, user_id, "agent", session)
            stmt = stmt.where(_vis_filter)
        agent_result = await session.execute(stmt)
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None
        return {
            "agent_id": agent.id,
            "instructions": agent.instructions,
            "tool_categories": agent.tool_categories,
            "model_config_json": agent.model_config_json,
            "kb_ids": agent.kb_ids,
            "connector_ids": agent.connector_ids,
            "mcp_server_ids": agent.mcp_server_ids,
            "grounding_config": agent.grounding_config,
            "sandbox_config": agent.sandbox_config,
            "owner_user_id": agent.user_id,
            "compact_instructions": agent.compact_instructions,
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
    # Model Group -> ENV fallback (Group-aware registry)
    registry = await get_model_registry_with_group(db)
    default_llm = registry.get_default()
    if not getattr(default_llm, "api_key", None):
        raise ValueError(
            "No LLM API key configured. "
            "Go to Admin → Models to add a model provider, "
            "or set LLM_API_KEY in your environment."
        )
    return default_llm


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
    # Model Group -> ENV fallback (Group-aware registry)
    registry = await get_model_registry_with_group(db)
    try:
        return registry.get_by_role("fast")
    except KeyError:
        return registry.get_default()


async def _resolve_model_supports_vision(
    agent_cfg: dict[str, Any] | None,
    db: AsyncSession,
) -> bool:
    """Check whether the resolved model config has vision support enabled.

    Mirrors :func:`_resolve_llm` resolution priority: agent config id >
    DB default model group > ``False`` fallback.
    """
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    if agent_cfg:
        cfg = agent_cfg.get("model_config_json") or {}
        model_config_id = cfg.get("model_config_id") if isinstance(cfg, dict) else None
        if model_config_id:
            stmt = sa_select(ModelConfigORM.supports_vision).where(
                ModelConfigORM.id == model_config_id,
                ModelConfigORM.is_active == True,  # noqa: E712
            )
            result = await db.execute(stmt)
            val = result.scalar_one_or_none()
            if val is not None:
                return bool(val)

    # Model Group fallback: check the active group's general model
    from fim_one.web.models.model_provider import ModelGroup

    group_stmt = sa_select(ModelGroup).where(
        ModelGroup.is_active == True  # noqa: E712
    ).limit(1)
    group_result = await db.execute(group_stmt)
    group = group_result.scalar_one_or_none()
    if group is not None and group.general_model:
        return bool(group.general_model.supports_vision)

    # System default model: check is_default=True system config
    default_stmt = sa_select(ModelConfigORM.supports_vision).where(
        ModelConfigORM.user_id == None,  # noqa: E711
        ModelConfigORM.category == "llm",
        ModelConfigORM.is_default == True,  # noqa: E712
        ModelConfigORM.is_active == True,  # noqa: E712
    ).limit(1)
    default_result = await db.execute(default_stmt)
    val = default_result.scalar_one_or_none()
    if val is not None:
        return bool(val)

    return False


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

        # Resolve per-KB owner for vector store path lookup.
        # Each KB's data lives under user_{owner}/kb_{id}/, so we need
        # the actual KB owner, not the agent owner or current user.
        kb_owner_map: dict[str, str] = {}
        try:
            from fim_one.db import create_session as _kb_cs
            from fim_one.web.models.knowledge_base import KnowledgeBase as _KBModel
            async with _kb_cs() as _kb_db:
                _kb_result = await _kb_db.execute(
                    sa_select(_KBModel.id, _KBModel.user_id).where(
                        _KBModel.id.in_(kb_ids)
                    )
                )
                for row in _kb_result.all():
                    kb_owner_map[row[0]] = row[1]
        except Exception:
            logger.warning("Failed to resolve KB owners", exc_info=True)

        if retrieval_mode == "simple":
            from fim_one.core.tool.builtin.kb_retrieve import KBRetrieveTool

            tools.register(KBRetrieveTool(
                user_id=user_id, kb_ids=kb_ids, kb_owner_map=kb_owner_map,
            ))
        else:
            from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

            grounding_config: dict[str, Any] = (agent_cfg or {}).get("grounding_config") or {}
            confidence_threshold = grounding_config.get("confidence_threshold")
            tools.register(GroundedRetrieveTool(
                kb_ids=kb_ids,
                user_id=user_id,
                kb_owner_map=kb_owner_map,
                confidence_threshold=confidence_threshold,
            ))
    elif user_id:
        # No bound KBs — keep basic kb_retrieve with user scope
        from fim_one.core.tool.builtin.kb_retrieve import KBRetrieveTool

        tools = tools.exclude_by_name("kb_retrieve")
        tools.register(KBRetrieveTool(user_id=user_id))

    # Load connector tools when the agent has bound connectors.
    connector_ids = agent_cfg.get("connector_ids") if agent_cfg else None
    if connector_ids:
        from fim_one.core.tool.connector import (
            ConnectorToolAdapter,
            build_connector_meta_tool,
            get_connector_tool_mode,
        )
        from fim_one.core.tool.connector.database.meta_tool import (
            build_database_meta_tool,
            get_database_tool_mode,
        )
        from fim_one.db import create_session
        from fim_one.web.models.connector import Connector as ConnectorModel
        from fim_one.web.models.connector_call_log import ConnectorCallLog

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        agent_id_for_log = agent_cfg.get("agent_id") if agent_cfg else None
        _connector_tool_mode = get_connector_tool_mode(agent_cfg)
        _database_tool_mode = get_database_tool_mode(agent_cfg)

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
                from fim_one.web.models.database_schema import (
                    DatabaseSchema as DatabaseSchemaModel,
                )

                # Use visibility filter so org-shared and Market-installed
                # connectors are accessible, not just owner-owned ones.
                from fim_one.web.visibility import resolve_visibility as _conn_resolve

                _conn_user_id = (agent_cfg.get("owner_user_id") or user_id) if agent_cfg else user_id
                if _conn_user_id:
                    _conn_vis, _, _ = await _conn_resolve(ConnectorModel, _conn_user_id, "connector", session)
                else:
                    _conn_vis = ConnectorModel.user_id == user_id

                stmt = (
                    select(ConnectorModel)
                    .options(
                        selectinload(ConnectorModel.actions),
                        selectinload(ConnectorModel.database_schemas).selectinload(
                            DatabaseSchemaModel.columns
                        ),
                    )
                    .where(ConnectorModel.id.in_(connector_ids), _conn_vis)
                )
                result = await session.execute(stmt)
                connectors = result.scalars().all()

                api_tool_count = 0
                db_tool_count = 0

                # Collect API connectors for potential progressive mode
                api_connectors = []
                # Collect DB connectors for potential progressive mode
                db_connectors_collected: list[tuple[Any, dict[str, Any], list[Any]]] = []

                for conn in connectors:
                    if conn.type == "database" and conn.db_config:
                        # Database connector — decrypt config and build schema
                        from fim_one.core.security.encryption import decrypt_db_config

                        config = decrypt_db_config(conn.db_config)
                        # Build schema_tables list from ORM
                        schema_tables = []
                        for schema_obj in (conn.database_schemas or []):
                            if not schema_obj.is_visible:
                                continue
                            cols = []
                            for col in (schema_obj.columns or []):
                                if not col.is_visible:
                                    continue
                                cols.append({
                                    "column_name": col.column_name,
                                    "data_type": col.data_type,
                                    "is_nullable": col.is_nullable,
                                    "is_primary_key": col.is_primary_key,
                                    "display_name": col.display_name,
                                    "description": col.description,
                                })
                            schema_tables.append({
                                "table_name": schema_obj.table_name,
                                "display_name": schema_obj.display_name,
                                "description": schema_obj.description,
                                "column_count": len(cols),
                                "columns": cols,
                            })

                        if _database_tool_mode == "progressive":
                            # Collect for batch meta-tool creation
                            db_connectors_collected.append(
                                (conn, config, schema_tables)
                            )
                        else:
                            # Legacy mode — three tools per DB
                            from fim_one.core.tool.connector.database.adapter import (
                                DatabaseToolAdapter,
                            )

                            db_tools = DatabaseToolAdapter.create_tools(
                                connector_name=conn.name,
                                connector_id=conn.id,
                                db_config=config,
                                schema_tables=schema_tables,
                                on_call_complete=_log_connector_call,
                            )
                            for t in db_tools:
                                tools.register(t)
                            db_tool_count += len(db_tools)
                    else:
                        # API connector — resolve per-user credentials
                        from fim_one.web.models.connector_credential import (
                            ConnectorCredential as ConnectorCredentialModel,
                        )
                        from fim_one.core.security.encryption import decrypt_credential

                        resolved_creds: dict[str, Any] = {}
                        _calling_user_id = user_id
                        _allow_fallback = getattr(conn, "allow_fallback", True)

                        if _calling_user_id:
                            # Try user-specific credential first
                            _user_cred_result = await session.execute(
                                select(ConnectorCredentialModel).where(
                                    ConnectorCredentialModel.connector_id == conn.id,
                                    ConnectorCredentialModel.user_id == _calling_user_id,
                                )
                            )
                            _user_cred_row = _user_cred_result.scalar_one_or_none()
                            if _user_cred_row:
                                resolved_creds = decrypt_credential(_user_cred_row.credentials_blob)

                        if not resolved_creds and _allow_fallback:
                            # Fall back to default (owner) credential
                            _default_cred_result = await session.execute(
                                select(ConnectorCredentialModel).where(
                                    ConnectorCredentialModel.connector_id == conn.id,
                                    ConnectorCredentialModel.user_id.is_(None),
                                )
                            )
                            _default_cred_row = _default_cred_result.scalar_one_or_none()
                            if _default_cred_row:
                                resolved_creds = decrypt_credential(_default_cred_row.credentials_blob)

                        if _connector_tool_mode == "progressive":
                            # Collect for batch meta-tool creation
                            api_connectors.append((conn, resolved_creds))
                        else:
                            # Legacy mode — one tool per action
                            for action in (conn.actions or []):
                                adapter = ConnectorToolAdapter(
                                    connector_name=conn.name,
                                    connector_base_url=conn.base_url or "",
                                    connector_auth_type=conn.auth_type,
                                    connector_auth_config=conn.auth_config,
                                    auth_credentials=resolved_creds or None,
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
                                api_tool_count += 1

                # Progressive mode — build a single ConnectorMetaTool
                if _connector_tool_mode == "progressive" and api_connectors:
                    _cred_map = {conn.id: creds for conn, creds in api_connectors}
                    meta_tool = build_connector_meta_tool(
                        [conn for conn, _ in api_connectors],
                        resolved_credentials=_cred_map,
                        on_call_complete=_log_connector_call,
                    )
                    tools.register(meta_tool)
                    total_actions = sum(
                        len(conn.actions or []) for conn, _ in api_connectors
                    )
                    logger.info(
                        "Loaded ConnectorMetaTool (progressive): %d connectors, "
                        "%d actions consolidated into 1 tool",
                        len(api_connectors),
                        total_actions,
                    )
                elif _connector_tool_mode == "legacy":
                    logger.info(
                        "Loaded %d API tools + %d DB tools from %d connectors",
                        api_tool_count,
                        db_tool_count,
                        len(connectors),
                    )
                else:
                    logger.info(
                        "Loaded %d API tools + %d DB tools from %d connectors",
                        api_tool_count,
                        db_tool_count,
                        len(connectors),
                    )

                # Progressive mode — build a single DatabaseMetaTool
                if _database_tool_mode == "progressive" and db_connectors_collected:
                    db_meta_tool = build_database_meta_tool(
                        db_connectors_collected,
                        on_call_complete=_log_connector_call,
                    )
                    tools.register(db_meta_tool)
                    total_tables = sum(
                        len(st) for _, _, st in db_connectors_collected
                    )
                    logger.info(
                        "Loaded DatabaseMetaTool (progressive): %d databases, "
                        "%d tables consolidated into 1 tool",
                        len(db_connectors_collected),
                        total_tables,
                    )
        except Exception:
            logger.warning("Failed to load connector tools", exc_info=True)

    elif user_id and not agent_cfg:
        # No-agent mode: auto-discover all visible connectors (progressive mode).
        try:
            from fim_one.core.tool.connector import build_connector_meta_tool
            from fim_one.core.tool.connector.database.meta_tool import (
                build_database_meta_tool as _ad_build_db_meta,
            )
            from fim_one.core.security.encryption import (
                decrypt_db_config,
            )
            from fim_one.db import create_session
            from fim_one.web.models.connector import Connector as ConnectorModel
            from fim_one.web.models.database_schema import (
                DatabaseSchema as DatabaseSchemaModel,
            )
            from fim_one.web.visibility import resolve_visibility

            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with create_session() as _ad_session:
                _ad_vis, _, _ = await resolve_visibility(
                    ConnectorModel, user_id, "connector", _ad_session
                )
                _ad_result = await _ad_session.execute(
                    select(ConnectorModel)
                    .where(_ad_vis, ConnectorModel.is_active == True)  # noqa: E712
                    .options(
                        selectinload(ConnectorModel.actions),
                        selectinload(ConnectorModel.database_schemas).selectinload(
                            DatabaseSchemaModel.columns
                        ),
                    )
                )
                _ad_connectors = _ad_result.scalars().unique().all()

            if _ad_connectors:
                # API connectors — use progressive mode (single meta-tool)
                _ad_api_connectors = [
                    c for c in _ad_connectors if c.type != "database"
                ]
                if _ad_api_connectors:
                    meta_tool = build_connector_meta_tool(_ad_api_connectors)
                    tools.register(meta_tool)
                    logger.info(
                        "Auto-discovered %d API connectors (progressive mode)",
                        len(_ad_api_connectors),
                    )

                # Database connectors — use progressive mode (single meta-tool)
                _ad_db_collected: list[tuple[Any, dict[str, Any], list[Any]]] = []
                for _ad_db_conn in _ad_connectors:
                    if _ad_db_conn.type == "database" and _ad_db_conn.db_config:
                        try:
                            config = decrypt_db_config(_ad_db_conn.db_config)
                            schema_tables = []
                            for schema_obj in (_ad_db_conn.database_schemas or []):
                                if not schema_obj.is_visible:
                                    continue
                                cols = []
                                for col in (schema_obj.columns or []):
                                    if not col.is_visible:
                                        continue
                                    cols.append({
                                        "column_name": col.column_name,
                                        "data_type": col.data_type,
                                        "is_nullable": col.is_nullable,
                                        "is_primary_key": col.is_primary_key,
                                        "display_name": col.display_name,
                                        "description": col.description,
                                    })
                                schema_tables.append({
                                    "table_name": schema_obj.table_name,
                                    "display_name": schema_obj.display_name,
                                    "description": schema_obj.description,
                                    "column_count": len(cols),
                                    "columns": cols,
                                })
                            _ad_db_collected.append(
                                (_ad_db_conn, config, schema_tables)
                            )
                        except Exception:
                            logger.warning(
                                "Failed to load DB connector tools: %s",
                                _ad_db_conn.name,
                                exc_info=True,
                            )

                if _ad_db_collected:
                    _ad_db_meta = _ad_build_db_meta(
                        _ad_db_collected,
                    )
                    tools.register(_ad_db_meta)
                    logger.info(
                        "Auto-discovered %d DB connectors (progressive mode)",
                        len(_ad_db_collected),
                    )
        except Exception:
            logger.warning("Failed to auto-discover connectors", exc_info=True)

    # Skills are global SOPs — always loaded regardless of agent selection.
    if user_id:
        _all_skill_ids = await _resolve_user_skill_ids(user_id)
        if _all_skill_ids and get_skill_tool_mode(agent_cfg) != "inline":
            from fim_one.core.tool.builtin.read_skill import ReadSkillTool

            tools.register(
                ReadSkillTool(
                    skill_ids=_all_skill_ids,
                    user_id=user_id,
                )
            )

    # File tools — always register when user is authenticated
    if user_id:
        from fim_one.core.tool.builtin.list_uploaded_files import ListUploadedFilesTool
        from fim_one.core.tool.builtin.read_uploaded_file import ReadUploadedFileTool
        tools.register(ListUploadedFilesTool(user_id=user_id))
        tools.register(ReadUploadedFileTool(user_id=user_id))

    # Inject Connector Builder tools when this is a Builder Agent.
    if agent_cfg and "builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re
        _instructions = agent_cfg.get("instructions") or ""
        _m = _re.search(r"connector_id=([a-f0-9-]{36})", _instructions)
        if _m:
            _builder_cid = _m.group(1)
            from fim_one.core.tool.builtin.connector_builder import (
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
                tools.register(_BCls(connector_id=_builder_cid, user_id=user_id or ""))  # type: ignore[abstract]
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
            from fim_one.core.tool.builtin.agent_builder import (
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
                tools.register(_BCls2(agent_id=_builder_aid, user_id=user_id or ""))  # type: ignore[abstract]
            logger.info(
                "Injected agent builder tools for agent_id=%s", _builder_aid
            )

    # Inject DB Builder tools when this is a DB Builder Agent.
    if agent_cfg and "db_builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re3
        _instructions3 = agent_cfg.get("instructions") or ""
        _m3 = _re3.search(r"connector_id=([a-f0-9-]{36})", _instructions3)
        if _m3:
            _builder_dbid = _m3.group(1)
            from fim_one.core.tool.builtin.db_builder import (
                DbGetConnectorSettingsTool,
                DbUpdateConnectorSettingsTool,
                DbTestConnectionTool,
                DbListTablesTool,
                DbGetTableDetailTool,
                DbAnnotateTableTool,
                DbAnnotateColumnTool,
                DbSetTableVisibilityTool,
                DbBatchSetVisibilityTool,
                DbRunSampleQueryTool,
            )
            for _BCls3 in [
                DbGetConnectorSettingsTool,
                DbUpdateConnectorSettingsTool,
                DbTestConnectionTool,
                DbListTablesTool,
                DbGetTableDetailTool,
                DbAnnotateTableTool,
                DbAnnotateColumnTool,
                DbSetTableVisibilityTool,
                DbBatchSetVisibilityTool,
                DbRunSampleQueryTool,
            ]:
                tools.register(_BCls3(connector_id=_builder_dbid, user_id=user_id or ""))  # type: ignore[abstract]
            logger.info("Injected db builder tools for connector_id=%s", _builder_dbid)

    # Filter out globally disabled built-in tools (admin setting).
    try:
        from fim_one.db import create_session as _cs_disabled
        from fim_one.web.api.admin_utils import get_setting as _get_setting

        from fim_one.web.api.admin import SETTING_DISABLED_BUILTIN_TOOLS as _SDBT
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
        from fim_one.db import create_session as _create_session
        from fim_one.web.models.mcp_server import MCPServer as _MCPServerModel
        from sqlalchemy import true as _true, false as _sa_false

        # Determine which MCP servers to load based on agent config
        _agent_mcp_ids = agent_cfg.get("mcp_server_ids") if agent_cfg else None

        if agent_cfg and not _agent_mcp_ids:
            # Agent exists but has no MCP servers selected — skip
            pass
        else:
            async with _create_session() as _mcp_db:
                if _agent_mcp_ids:
                    # Agent mode: load only the specified MCP servers
                    _stmt = sa_select(_MCPServerModel).where(
                        _MCPServerModel.id.in_(_agent_mcp_ids),
                        _MCPServerModel.is_active == _true(),
                    )
                else:
                    # No-agent mode: load all visible active MCP servers
                    from fim_one.web.visibility import resolve_visibility as _resolve_vis

                    if user_id:
                        _vis_filter, _, _ = await _resolve_vis(
                            _MCPServerModel, user_id, "mcp_server", _mcp_db
                        )
                    else:
                        _vis_filter = _sa_false()
                    _stmt = sa_select(_MCPServerModel).where(
                        _vis_filter,
                        _MCPServerModel.is_active == _true(),
                    )
                _result = await _mcp_db.execute(_stmt)
                _user_servers = _result.scalars().all()

            if _user_servers:
                tools._pending_mcp_servers = list(_user_servers)  # type: ignore[attr-defined]
                tools._mcp_user_id = user_id  # type: ignore[attr-defined]
    except Exception:
        logger.warning("Failed to load MCP server configs", exc_info=True)

    # ------------------------------------------------------------------
    # Register CallAgentTool with all visible agents for multi-agent
    # delegation.  The tool_resolver callback allows delegated agents to
    # inherit the full tool set (minus call_agent itself).
    # Only available when no specific agent is selected — prevents
    # marketplace agents from accessing other agents' private prompts.
    # ------------------------------------------------------------------
    if user_id and not agent_cfg:
        try:
            from fim_one.db import create_session as _cs_agents
            from fim_one.web.models.agent import Agent as AgentModel
            from fim_one.web.visibility import resolve_visibility as _rv_agents

            async with _cs_agents() as _cat_db:
                _cat_vis, _, _ = await _rv_agents(
                    AgentModel, user_id, "agent", _cat_db
                )
                _cat_result = await _cat_db.execute(
                    sa_select(AgentModel).where(
                        _cat_vis,
                        AgentModel.is_active == True,  # noqa: E712
                        AgentModel.is_builder == False,  # noqa: E712
                    )
                )
                _visible_agents = _cat_result.scalars().all()

            if _visible_agents:
                _agent_catalog = [
                    {
                        "id": a.id,
                        "name": a.name,
                        "description": a.description or "",
                        "instructions": a.instructions,
                        "model_config_json": a.model_config_json,
                        "tool_categories": a.tool_categories,
                        "kb_ids": a.kb_ids,
                        "connector_ids": a.connector_ids,
                        "mcp_server_ids": a.mcp_server_ids,
                        "grounding_config": a.grounding_config,
                        "sandbox_config": a.sandbox_config,
                        "owner_user_id": a.user_id,
                    }
                    for a in _visible_agents
                ]

                async def _sub_agent_tool_resolver(
                    sub_cfg: dict[str, Any], conv_id: str | None
                ) -> Any:
                    return await _resolve_tools(
                        sub_cfg, conv_id, user_id=user_id
                    )

                async def _sub_agent_llm_resolver(
                    sub_cfg: dict[str, Any],
                ) -> Any:
                    """Resolve LLM for a sub-agent with full DB-backed 3-tier fallback."""
                    from fim_one.db import create_session as _cs_llm
                    async with _cs_llm() as _llm_db:
                        return await _resolve_llm(sub_cfg, _llm_db)

                from fim_one.core.tool.builtin.call_agent import CallAgentTool

                tools.register(
                    CallAgentTool(
                        available_agents=_agent_catalog,
                        calling_user_id=user_id,
                        tool_resolver=_sub_agent_tool_resolver,
                        llm_resolver=_sub_agent_llm_resolver,
                    )
                )
        except Exception:
            logger.warning("Failed to build agent catalog", exc_info=True)

    return tools


async def _connect_pending_mcp_servers(
    tools: ToolRegistry,
    agent_cfg: dict[str, Any] | None = None,
) -> Any:
    """Connect to pending MCP servers and register their tools.

    Must be called from inside the SSE generator so that the anyio cancel
    scope created by stdio_client is entered and exited in the same coroutine.
    Returns the MCPClient (caller must call disconnect_all() in finally).

    When ``MCP_TOOL_MODE=progressive`` (the default), all MCP tools are
    consolidated into a single :class:`MCPServerMetaTool` with ``discover``
    and ``call`` subcommands instead of registering each tool individually.
    """
    import json as _json_mod
    pending = getattr(tools, "_pending_mcp_servers", None)
    if not pending:
        return None

    _mcp_user_id = getattr(tools, "_mcp_user_id", None)

    from fim_one.core.mcp import MCPClient as _MCPClient

    _mcp_client = _MCPClient()
    _loaded = 0

    # Determine MCP tool mode (progressive vs legacy)
    from fim_one.core.mcp import get_mcp_tool_mode, build_mcp_meta_tool

    _mcp_tool_mode = get_mcp_tool_mode(agent_cfg)
    # Collect adapters per server for progressive mode
    _servers_adapters: dict[str, list[Any]] = {}

    for _srv in pending:
        try:
            # Resolve per-user credentials vs server-level env/headers
            _effective_env = _srv.env
            _effective_headers = _srv.headers

            if _mcp_user_id:
                try:
                    from fim_one.db import create_session as _cs_cred
                    from fim_one.web.models.mcp_server_credential import MCPServerCredential as _MCPCred
                    from sqlalchemy import select as _sa_select

                    async with _cs_cred() as _cred_db:
                        _cred_res = await _cred_db.execute(
                            _sa_select(_MCPCred).where(
                                _MCPCred.server_id == _srv.id,
                                _MCPCred.user_id == _mcp_user_id,
                            )
                        )
                        _cred = _cred_res.scalar_one_or_none()

                    if _cred and _cred.env_blob:
                        _effective_env = _cred.env_blob
                        _effective_headers = (
                            _cred.headers_blob
                            if _cred.headers_blob
                            else _srv.headers
                        )
                    elif not getattr(_srv, "allow_fallback", True) and _srv.user_id != _mcp_user_id:
                        # allow_fallback=False and non-owner has no credential — skip
                        logger.info(
                            "Skipping MCP server %r: allow_fallback=False and user has no credentials",
                            _srv.name,
                        )
                        continue
                    # else: use server-level env (current behavior)
                except Exception:
                    logger.warning(
                        "Failed to resolve MCP credentials for server %r, using server-level env",
                        _srv.name,
                        exc_info=True,
                    )

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
                    env=_effective_env,
                    working_dir=_srv.working_dir,
                )
            elif _srv.transport == "sse" and _srv.url:
                _mcp_tools = await _mcp_client.connect_sse(
                    name=_srv.name,
                    url=_srv.url,
                    headers=_effective_headers,
                )
            elif _srv.transport == "streamable_http" and _srv.url:
                _mcp_tools = await _mcp_client.connect_streamable_http(
                    name=_srv.name,
                    url=_srv.url,
                    headers=_effective_headers,
                )
            else:
                continue

            if _mcp_tool_mode == "progressive":
                # Collect adapters per server for batch meta-tool creation
                _servers_adapters[_srv.name] = list(_mcp_tools)
            else:
                # Legacy mode — register each tool individually
                for _t in _mcp_tools:
                    tools.register(_t)
            _loaded += len(_mcp_tools)
        except Exception:
            logger.warning(
                "Failed to connect user MCP server %r",
                _srv.name,
                exc_info=True,
            )

    # Progressive mode — build a single MCPServerMetaTool
    if _mcp_tool_mode == "progressive" and _servers_adapters:
        meta_tool = build_mcp_meta_tool(_servers_adapters)
        tools.register(meta_tool)
        total_tools = sum(len(adapters) for adapters in _servers_adapters.values())
        logger.info(
            "Loaded MCPServerMetaTool (progressive): %d servers, "
            "%d tools consolidated into 1 tool",
            len(_servers_adapters),
            total_tools,
        )
    else:
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
            "\nThe confidence score indicates evidence quality \u2014 mention it for important claims. "
            "Source numbers are cumulative across calls "
            "(e.g., first call [1]-[5], second call [6]-[10])."
        )

    return hint


async def _resolve_skill_stubs(skill_ids: list[str]) -> str:
    """Return a compact stub block for all bound skills."""
    from fim_one.db import create_session
    from fim_one.web.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill.name, Skill.description).where(
                    Skill.id.in_(skill_ids), Skill.is_active == True  # noqa: E712
                )
            )
            rows = result.all()
        if not rows:
            return ""
        lines = [
            "\n\n## Available Skills",
            "Call read_skill(name) to load full content before executing any of these:",
        ]
        for name, desc in rows:
            if desc and len(desc) > _SKILL_STUB_DESC_LEN:
                desc = desc[:_SKILL_STUB_DESC_LEN - 3] + "..."
            stub = f"- **{name}**" + (f": {desc}" if desc else "")
            lines.append(stub)
        return "\n".join(lines)
    except Exception:
        return ""


async def _resolve_skill_descriptors(
    skill_ids: list[str],
) -> list[dict[str, str]]:
    """Return ``[{"name": ..., "description": ...}]`` for planner skill discovery."""
    from fim_one.db import create_session
    from fim_one.web.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill.name, Skill.description).where(
                    Skill.id.in_(skill_ids),
                    Skill.is_active == True,  # noqa: E712
                )
            )
            return [
                {"name": name, "description": desc or ""}
                for name, desc in result.all()
            ]
    except Exception:
        logger.warning("Failed to resolve skill descriptors", exc_info=True)
        return []


async def _resolve_user_skill_ids(user_id: str) -> list[str]:
    """Fetch all active, visible skill IDs for a user (own + org + subscribed)."""
    from fim_one.db import create_session
    from fim_one.web.models.skill import Skill
    from fim_one.web.visibility import resolve_visibility

    try:
        async with create_session() as session:
            vis_filter, _, _ = await resolve_visibility(
                Skill, user_id, "skill", session
            )
            result = await session.execute(
                sa_select(Skill.id).where(
                    vis_filter,
                    Skill.is_active == True,  # noqa: E712
                )
            )
            return list(result.scalars().all())
    except Exception:
        logger.warning("Failed to resolve user skill IDs", exc_info=True)
        return []


def get_skill_tool_mode(agent_cfg: dict[str, Any] | None = None) -> str:
    """Determine skill tool mode from agent config or environment.

    Priority:
        1. Agent-level ``model_config_json.skill_tool_mode``
        2. Environment variable ``SKILL_TOOL_MODE``
        3. Default: ``"progressive"``
    """
    if agent_cfg:
        model_cfg = agent_cfg.get("model_config_json") or {}
        if isinstance(model_cfg, dict):
            mode = model_cfg.get("skill_tool_mode")
            if mode in ("progressive", "inline"):
                return str(mode)
    env_mode = os.environ.get("SKILL_TOOL_MODE", "progressive").lower()
    if env_mode in ("progressive", "inline"):
        return env_mode
    return "progressive"


async def _resolve_skill_inline(skill_ids: list[str]) -> str:
    """Return full skill content for inline injection into system prompt."""
    from fim_one.db import create_session
    from fim_one.web.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill).where(
                    Skill.id.in_(skill_ids), Skill.is_active == True  # noqa: E712
                )
            )
            skills = result.scalars().all()
        if not skills:
            return ""
        parts = ["\n\n## Skills (inline)"]
        for skill in skills:
            parts.append(f"\n### {skill.name}")
            if skill.description:
                parts.append(skill.description)
            parts.append(skill.content or "")
            if skill.script and skill.script_type:
                parts.append(f"\n```{skill.script_type}\n{skill.script}\n```")
            if skill.resource_refs:
                parts.append("\nResource References:")
                for ref in skill.resource_refs:
                    alias = ref.get("alias", "")
                    rtype = ref.get("type", "unknown")
                    rname = ref.get("name", "")
                    parts.append(f'- {alias}: {rtype} "{rname}"')
        return "\n".join(parts)
    except Exception:
        return ""


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

    from fim_one.web.api.files import UPLOAD_ROOT, _is_image, _load_index

    index = _load_index(user_id)
    results: list[tuple[str, str, str]] = []

    for fid in image_ids.split(","):
        fid = fid.strip()
        if not fid:
            continue
        meta = index.get(fid)
        if not meta:
            continue
        suffix = Path(str(meta["filename"])).suffix.lower()
        if not _is_image(suffix):
            continue
        file_path = UPLOAD_ROOT / f"user_{user_id}" / str(meta["stored_name"])
        if not file_path.exists():
            continue
        mime = meta.get("mime_type", "image/png")
        raw = await asyncio.to_thread(file_path.read_bytes)
        b64 = await asyncio.to_thread(base64.b64encode, raw)
        data_url = f"data:{mime};base64,{b64.decode('ascii')}"
        results.append((fid, str(meta["filename"]), data_url))

    return results


_VISION_ERROR_KEYWORDS = (
    "image", "vision", "image_url", "content_type",
    "multimodal", "not supported", "invalid content",
)


def _is_vision_error(exc: Exception) -> bool:
    """Check if an exception is likely caused by unsupported vision content."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _VISION_ERROR_KEYWORDS)


async def _load_document_vision_urls(
    image_ids: str,
    user_id: str | None,
    doc_mode: str | None,
    model_supports_vision: bool = False,
) -> list[str]:
    """Load rendered PDF page images for vision-capable models.

    Scans the file IDs in *image_ids* for PDF documents and, when the active
    model supports vision (via Admin toggle), returns their pages as base64
    data URLs.

    Args:
        image_ids: Comma-separated file IDs from the chat request.
        user_id: Current user ID for file lookup.
        doc_mode: Explicit document processing mode (``"vision"`` / ``"text"``
            / ``None`` for auto).
        model_supports_vision: Whether the DB model config has vision enabled.

    Returns:
        List of base64 data URLs for rendered document pages / embedded images.
    """
    if not user_id or not image_ids:
        return []

    from fim_one.core.document.processor import _get_doc_processing_mode
    from fim_one.web.api.files import UPLOAD_ROOT, _load_index

    VISION_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}

    # Determine effective mode
    effective_mode = doc_mode or _get_doc_processing_mode()

    # Vision is only available when explicitly enabled in Admin → Models
    vision_available = model_supports_vision

    if effective_mode == "text" or (effective_mode == "auto" and not vision_available):
        return []

    index = _load_index(user_id)
    all_page_urls: list[str] = []

    for fid in image_ids.split(","):
        fid = fid.strip()
        if not fid:
            continue
        meta = index.get(fid)
        if not meta:
            continue
        suffix = Path(str(meta["filename"])).suffix.lower()
        if suffix not in VISION_DOC_EXTENSIONS:
            continue
        file_path = UPLOAD_ROOT / f"user_{user_id}" / str(meta["stored_name"])
        if not file_path.exists():
            continue

        from fim_one.core.document import DocumentProcessor

        if suffix == ".pdf":
            # Smart extraction: embedded images only for text-heavy pages,
            # full-page PNG only for scanned pages (no text layer).
            try:
                raw_images = await DocumentProcessor.extract_pdf_images(file_path)
                for img_bytes in raw_images:
                    if img_bytes[:2] == b"\xff\xd8":
                        mime = "image/jpeg"
                    elif img_bytes[:4] == b"\x89PNG":
                        mime = "image/png"
                    else:
                        mime = "image/png"
                    b64 = base64.b64encode(img_bytes).decode("ascii")
                    all_page_urls.append(f"data:{mime};base64,{b64}")
            except ImportError:
                logger.warning(
                    "PyMuPDF not installed, cannot extract PDF images"
                )
            except Exception:
                logger.warning(
                    "PDF smart image extraction failed", exc_info=True
                )
        else:
            # DOCX/PPTX: extract embedded images
            _, image_bytes_list = await DocumentProcessor.extract_with_images(file_path)
            for img_bytes in image_bytes_list:
                # Detect MIME type from magic bytes
                if img_bytes[:2] == b"\xff\xd8":
                    mime = "image/jpeg"
                elif img_bytes[:4] == b"\x89PNG":
                    mime = "image/png"
                else:
                    mime = "image/png"
                b64 = base64.b64encode(img_bytes).decode("ascii")
                all_page_urls.append(f"data:{mime};base64,{b64}")

    return all_page_urls


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
    from fim_one.web.auth import create_sse_ticket
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
    doc_mode: str | None = None  # "vision" | "text" | None (auto)


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
    from fim_one.web.models import Conversation as _ConvModel

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
        from fim_one.web.models import Message as MessageModel

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
    from fim_one.web.models import Conversation as _ConvModel

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
    doc_mode = body.doc_mode

    # Sensitive word check — block before starting the agent
    from fim_one.db import create_session as _create_sw_session

    async with _create_sw_session() as _sw_db:
        matched = await _check_sensitive_words(q, _sw_db)
    if matched:
        raise AppError(
            "sensitive_word_blocked",
            status_code=400,
            detail_args={"words": ", ".join(matched)},
        )

    # -- Pre-stream resolution (before StreamingResponse) -------------------
    current_user_id, user_system_instructions, preferred_language, user_timezone = await _resolve_user(token)
    if current_user_id is None:
        raise AppError("authentication_required", status_code=401)

    # Timezone: user profile → X-Timezone header → UTC fallback
    if not user_timezone:
        user_timezone = request.headers.get("X-Timezone") or None

    # Run independent validations and agent config resolution in parallel
    _parallel_tasks: list[Any] = [_check_token_quota(current_user_id)]
    if conversation_id:
        _parallel_tasks.append(_validate_conversation_ownership(conversation_id, current_user_id))
    _parallel_tasks.append(_resolve_agent_config(agent_id, conversation_id, user_id=current_user_id))
    _parallel_results = await asyncio.gather(*_parallel_tasks)
    # _resolve_agent_config is always the last task appended
    agent_cfg = _parallel_results[-1]

    from fim_one.db import create_session as _create_session
    try:
        async with _create_session() as _llm_db:
            llm, fast_llm, _context_budget, model_supports_vision = await asyncio.gather(
                _resolve_llm(agent_cfg, _llm_db),
                _resolve_fast_llm(agent_cfg, _llm_db),
                get_effective_context_budget(_llm_db),
                _resolve_model_supports_vision(agent_cfg, _llm_db),
            )
    except ValueError as exc:
        raise AppError(
            "agent_config_error",
            status_code=500,
            detail=str(exc),
            detail_args={"reason": str(exc)},
        ) from exc

    # -- Wrap primary LLM with fallback for availability resilience ----------
    if fast_llm and fast_llm is not llm:
        llm = FallbackLLM(primary=llm, fallback=fast_llm)

    # -- Run tool resolution and domain classification in parallel ----------
    # Both are independent: _resolve_tools hits DB/registry, classify_domain
    # calls the fast LLM.  Running concurrently saves ~1 RTT.
    from fim_one.core.planner.domain import classify_domain
    tools, _react_domain_hint = await asyncio.gather(
        _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id),
        classify_domain(q, fast_llm),
    )

    agent_instructions = agent_cfg["instructions"] if agent_cfg else None
    lang_directive = get_language_directive(preferred_language)

    # -- Layered extra_instructions assembly --------------------------------
    # Priority (high → low):
    #   1. Agent Directive   — the agent's core purpose / identity
    #   2. User Preferences  — language preference + personal instructions
    #   3. Capabilities      — KB hints, skills (additive)
    parts: list[str] = []
    if agent_instructions:
        parts.append(f"## Agent Directive\n{agent_instructions}")
    pref_parts: list[str] = []
    if lang_directive:
        pref_parts.append(lang_directive)
    if user_system_instructions:
        pref_parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if pref_parts:
        parts.append("## User Preferences\n" + "\n\n".join(pref_parts))
    extra_instructions = "\n\n".join(parts) if parts else None

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = _kb_system_hint(agent_cfg)
        extra_instructions = (extra_instructions or "") + grounding_hint

    # Inject skill stubs — skills are global SOPs
    _react_skill_ids = await _resolve_user_skill_ids(current_user_id) if current_user_id else None
    if _react_skill_ids:
        _skill_mode = get_skill_tool_mode(agent_cfg)
        if _skill_mode == "inline":
            _skill_block = await _resolve_skill_inline(_react_skill_ids)
        else:
            _skill_block = await _resolve_skill_stubs(_react_skill_ids)
        if _skill_block:
            extra_instructions = (extra_instructions or "") + _skill_block
    if _react_domain_hint:
        _domain_instructions = (
            f"\n\n## Domain: {_react_domain_hint}\n"
            f"This is a {_react_domain_hint}-domain task requiring high accuracy.\n"
            f"Guidelines for {_react_domain_hint} tasks:\n"
            f"1. If the task requires citing laws, regulations, or precedents, "
            f"use web_search to verify them BEFORE writing. Do NOT guess article "
            f"numbers or fabricate case references from training data.\n"
            f"2. If the user's query and provided context already contain all "
            f"necessary information (e.g. a yes/no question, analysis of attached "
            f"documents), answer directly — do not force unnecessary searches.\n"
            f"3. If a matching skill is available (e.g. a {_react_domain_hint}-"
            f"advisor skill), call read_skill(name) to load the SOP.\n"
            f"4. When external research IS needed, conduct targeted searches "
            f"and cite only verified sources."
        )
        extra_instructions = (extra_instructions or "") + _domain_instructions

        # Escalate to reasoning model for domain tasks — Sonnet-class models
        # produce citation-level errors (wrong article numbers) in legal/medical
        # analysis.  Opus-class reasoning models have significantly higher
        # factual accuracy for domain-specific content.
        from fim_one.web.deps import get_model_registry_with_group
        try:
            async with _create_session() as _esc_db:
                _esc_registry = await get_model_registry_with_group(_esc_db)
            _reasoning_llm = _esc_registry.get_by_role("reasoning")
            if _reasoning_llm is not llm:
                logger.info(
                    "Domain escalation: upgrading ReAct model from %s to %s "
                    "for %s-domain task",
                    getattr(llm, "model_id", "unknown"),
                    getattr(_reasoning_llm, "model_id", "unknown"),
                    _react_domain_hint,
                )
                llm = _reasoning_llm
        except (KeyError, Exception) as exc:
            logger.debug(
                "Domain escalation: no reasoning model available (%s), "
                "continuing with general model",
                exc,
            )

    # Load attached images (async to avoid blocking the event loop)
    image_data: list[tuple[str, str, str]] = []
    if image_ids:
        image_data = await _load_image_data_urls(image_ids, current_user_id)

    # Gate user-uploaded images: only send as vision content when model supports it
    if image_data and not model_supports_vision:
        # Model doesn't support vision -- annotate query with file_ids + filenames
        img_refs = ", ".join(f"{fname} (file_id: {fid})" for fid, fname, _ in image_data)
        q = f"{q}\n\n[Attached images (text-only model, not displayed): {img_refs}]"

    # Load document vision pages (PDF/DOCX/PPTX rendered as images for vision models)
    doc_vision_urls: list[str] = []
    if image_ids:
        doc_vision_urls = await _load_document_vision_urls(
            image_ids=image_ids,
            user_id=current_user_id,
            doc_mode=doc_mode,
            model_supports_vision=model_supports_vision,
        )

    # Annotate query with file_ids for ALL attached files so the agent
    # knows the correct UUID to use with read_uploaded_file — regardless
    # of whether vision processed them.
    if image_ids and current_user_id:
        from fim_one.web.api.files import _load_index

        _attach_index = _load_index(current_user_id)
        handled_fids = {fid for fid, _, _ in image_data}
        unhandled: list[str] = []
        for _fid in image_ids.split(","):
            _fid = _fid.strip()
            if not _fid or _fid in handled_fids:
                continue
            _meta = _attach_index.get(_fid)
            if _meta:
                unhandled.append(
                    f"  - {_meta.get('filename', 'unknown')} (file_id: {_fid})"
                )
        if unhandled:
            q += (
                "\n\n[Attached files — use these file_ids with "
                "read_uploaded_file to access content:\n"
                + "\n".join(unhandled) + "]"
            )

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []
        yield _emit(sse_events, "step", {"type": "thinking", "status": "start", "iteration": 1})

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        user_mcp_client = await _connect_pending_mcp_servers(tools, agent_cfg)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_one.db import create_session
                from fim_one.web.models import Message as MessageModel

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
                all_images: list[dict[str, str]] = []
                if image_data:
                    all_images.extend(
                        {
                            "file_id": fid,
                            "filename": fname,
                            "mime_type": durl.split(";")[0].split(":")[1],
                            "source": "upload",
                        }
                        for fid, fname, durl in image_data
                    )
                # Include document files that contributed vision content
                # so embedded images can be reconstructed in later turns.
                if doc_vision_urls and image_ids and current_user_id:
                    from fim_one.web.api.files import _load_index as _dv_load_index

                    _dv_index = _dv_load_index(current_user_id)
                    _DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}
                    for _dfid in image_ids.split(","):
                        _dfid = _dfid.strip()
                        _dmeta = _dv_index.get(_dfid)
                        if _dmeta:
                            _dname = str(_dmeta.get("filename", ""))
                            if Path(_dname).suffix.lower() in _DOC_EXTS:
                                all_images.append({
                                    "file_id": _dfid,
                                    "filename": _dname,
                                    "mime_type": str(_dmeta.get("mime_type", "")),
                                    "source": "document",
                                })
                if all_images:
                    final_metadata["images"] = all_images
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

                iter_start = time.time()
                _emit_step({"type": "answer", "status": "start"})
                answer_started = True
                return

        try:
            fast_usage_tracker = UsageTracker()
            memory = None
            if conversation_id:
                from fim_one.core.memory import DbMemory
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
                custom_compact_prompt=agent_cfg.get("compact_instructions") if agent_cfg else None,
            )

            # Inject fast usage tracker into grounded retrieve tool
            from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool
            for tool in tools._tools.values():
                if isinstance(tool, GroundedRetrieveTool):
                    tool.set_usage_tracker(fast_usage_tracker)

            # Always pin web_search — it's a fundamental capability that
            # tool selection should never drop, regardless of domain.
            _pinned: list[str] = ["web_search"]

            agent = ReActAgent(
                llm=llm,
                tools=tools,
                extra_instructions=extra_instructions,
                max_iterations=get_react_max_iterations(),
                memory=memory,
                context_guard=context_guard,
                fast_llm=fast_llm,
                user_timezone=user_timezone,
                agent_directive=agent_instructions,
                pinned_tools=_pinned,
                max_turn_tokens=get_react_max_turn_tokens(),
            )

            # Only send images as vision content when model supports it
            if image_data and model_supports_vision:
                image_urls: list[str] | None = [url for _, _, url in image_data]
            else:
                image_urls = None
            # Append document vision page images (rendered PDF/DOCX/PPTX pages)
            if doc_vision_urls:
                image_urls = (image_urls or []) + doc_vision_urls

            async def _run() -> Any:
                nonlocal image_urls
                try:
                    return await agent.run(
                        q, on_iteration=on_iteration, image_urls=image_urls,
                        interrupt_queue=interrupt_queue,
                    )
                except Exception as exc:
                    # Vision fallback: if document pages caused the error,
                    # strip them and retry with text-only mode.
                    if doc_vision_urls and _is_vision_error(exc):
                        logger.warning(
                            "Vision content rejected by model, retrying without "
                            "document page images: %s", exc,
                        )
                        # Keep user-uploaded images, only remove doc pages
                        if image_data and model_supports_vision:
                            image_urls = [url for _, _, url in image_data]
                        else:
                            image_urls = None
                        return await agent.run(
                            q, on_iteration=on_iteration, image_urls=image_urls,
                            interrupt_queue=interrupt_queue,
                        )
                    raise
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

            # Emit answer start step event if the on_iteration callback
            # didn't (e.g. max iterations exceeded, no final_answer action).
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

            # -- Stream answer to client (DAG-style) -----------------------
            yield _emit(sse_events, "answer", {"status": "start"})
            answer_chunks: list[str] = []
            try:
                async for _token in agent.stream_answer(
                    q, result, language_directive=lang_directive,
                ):
                    answer_chunks.append(_token)
                    yield _emit(
                        sse_events, "answer",
                        {"status": "delta", "content": _token},
                    )
                answer = "".join(answer_chunks)
            except Exception:
                logger.warning(
                    "stream_answer failed, falling back to result.answer",
                    exc_info=True,
                )
                answer = result.answer
                for _ans_chunk in _chunk_answer(answer):
                    yield _emit(
                        sse_events, "answer",
                        {"status": "delta", "content": _ans_chunk},
                    )
            yield _emit(sse_events, "answer", {"status": "done"})

            # -- Classify deliverables from all artifacts --
            all_artifacts_with_context: list[dict[str, Any]] = []
            for evt in sse_events:
                if evt["event"] == "step":
                    step_data = evt["data"]
                    if step_data.get("status") == "done" and step_data.get("artifacts"):
                        for a in step_data["artifacts"]:
                            all_artifacts_with_context.append({
                                **a,
                                "tool_name": step_data.get("tool_name", ""),
                            })

            deliverables: list[dict[str, Any]] = []
            if all_artifacts_with_context and fast_llm:
                deliverables = await _classify_deliverables(
                    fast_llm, answer, all_artifacts_with_context,
                    usage_tracker=fast_usage_tracker,
                )

            elapsed = round(time.time() - t0, 2)
            last_iter_elapsed = round(time.time() - iter_start, 2)
            done_payload: dict[str, Any] = {
                "answer": answer,
                "iterations": result.iterations,
                "elapsed": elapsed,
                "iter_elapsed": last_iter_elapsed,
            }
            if deliverables:
                # Strip tool_name from deliverable dicts before sending to client
                done_payload["deliverables"] = [
                    {k: v for k, v in d.items() if k != "tool_name"}
                    for d in deliverables
                ]
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
                from fim_one.db import create_session
                db_session = create_session()

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_one.web.models import (
                        Conversation,
                        Message as MessageModel,
                    )

                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        message_type="done",
                        metadata_={**done_payload, "sse_events": sse_events, "mode": "react"},
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

            # -- Unregister interrupt queue immediately after done ------
            # No agent loop is consuming injected messages anymore.
            # Unregistering causes inject API to return 404, signaling
            # the frontend to queue messages for the next turn instead.
            if interrupt_queue is not None and conversation_id:
                await get_broker().unregister(conversation_id)
                interrupt_queue = None  # prevent double-unregister in finally

            # -- Send end immediately — post-processing runs in background --
            yield _sse("end", {})

            # -- Background post-processing: suggestions, title, fast token accounting --
            # Fire-and-forget: the SSE stream closes right after end, so the
            # client won't receive these as SSE events.  Instead the background
            # task writes results directly to DB for the frontend to fetch via
            # the conversation API.
            _bg_conversation_id = conversation_id
            _bg_fast_llm = fast_llm
            _bg_query = q
            _bg_answer = result.answer
            _bg_preferred_language = preferred_language
            _bg_done_payload = done_payload

            async def _react_post_processing() -> None:
                """Background task for suggestions, title, and fast-LLM token tracking."""
                from fim_one.db import create_session as _bg_create_session
                _bg_db: AsyncSession | None = None
                try:
                    _bg_usage_tracker = UsageTracker()

                    async def _bg_maybe_generate_title() -> str | None:
                        if not _bg_conversation_id:
                            return None
                        try:
                            from sqlalchemy import func as _sa_func
                            from fim_one.web.models import (
                                Conversation,
                                Message as _MsgModel,
                            )
                            async with _bg_create_session() as _cnt_db:
                                msg_count = (
                                    await _cnt_db.execute(
                                        sa_select(_sa_func.count())
                                        .select_from(_MsgModel)
                                        .where(_MsgModel.conversation_id == _bg_conversation_id)
                                    )
                                ).scalar() or 0
                            if msg_count <= 2:
                                return await _generate_title(
                                    _bg_fast_llm, _bg_query, _bg_answer,
                                    preferred_language=_bg_preferred_language,
                                    usage_tracker=_bg_usage_tracker,
                                )
                        except Exception:
                            logger.debug("Auto-title generation failed", exc_info=True)
                        return None

                    suggestions, gen_title = await asyncio.gather(
                        _generate_suggestions(
                            _bg_fast_llm, _bg_query, _bg_answer,
                            preferred_language=_bg_preferred_language,
                            usage_tracker=_bg_usage_tracker,
                        ),
                        _bg_maybe_generate_title(),
                    )

                    if not _bg_conversation_id:
                        return

                    _bg_db = _bg_create_session()
                    if suggestions:
                        try:
                            from fim_one.web.models import Message as _MsgModel
                            # Store suggestions in the most recent assistant message's metadata
                            _last_msg_stmt = (
                                sa_select(_MsgModel)
                                .where(
                                    _MsgModel.conversation_id == _bg_conversation_id,
                                    _MsgModel.role == "assistant",
                                )
                                .order_by(_MsgModel.created_at.desc())
                                .limit(1)
                            )
                            _last_msg = (await _bg_db.execute(_last_msg_stmt)).scalar_one_or_none()
                            if _last_msg and _last_msg.metadata_:
                                _last_msg.metadata_["suggestions"] = suggestions
                            elif _last_msg:
                                _last_msg.metadata_ = {"suggestions": suggestions}
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist suggestions", exc_info=True)
                    if gen_title:
                        try:
                            from sqlalchemy import update as _sa_update
                            from fim_one.web.models import Conversation
                            await _bg_db.execute(
                                _sa_update(Conversation)
                                .where(Conversation.id == _bg_conversation_id)
                                .values(title=gen_title)
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist title", exc_info=True)

                    # Capture fast LLM token usage
                    bg_fast_summary = _bg_usage_tracker.get_summary()
                    if bg_fast_summary.total_tokens > 0:
                        try:
                            from sqlalchemy import update as _sa_update, func as _sa_func
                            from fim_one.web.models import Conversation
                            await _bg_db.execute(
                                _sa_update(Conversation)
                                .where(Conversation.id == _bg_conversation_id)
                                .values(
                                    total_tokens=_sa_func.coalesce(Conversation.total_tokens, 0) + bg_fast_summary.total_tokens,
                                    fast_llm_tokens=_sa_func.coalesce(Conversation.fast_llm_tokens, 0) + bg_fast_summary.total_tokens,
                                )
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.warning("Failed to persist fast LLM tokens", exc_info=True)
                except Exception:
                    logger.warning("ReAct post-processing background task failed", exc_info=True)
                finally:
                    if _bg_db:
                        await _bg_db.close()

            asyncio.create_task(_react_post_processing())
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
    dag_doc_mode = body.doc_mode

    # -- Pre-stream resolution ----------------------------------------------
    current_user_id, user_system_instructions, preferred_language, user_timezone = await _resolve_user(token)
    if current_user_id is None:
        raise AppError("authentication_required", status_code=401)

    # Timezone: user profile → X-Timezone header → UTC fallback
    if not user_timezone:
        user_timezone = request.headers.get("X-Timezone") or None

    # Run independent validations and agent config resolution in parallel
    _parallel_tasks_dag: list[Any] = [_check_token_quota(current_user_id)]
    if conversation_id:
        _parallel_tasks_dag.append(_validate_conversation_ownership(conversation_id, current_user_id))
    _parallel_tasks_dag.append(_resolve_agent_config(agent_id, conversation_id, user_id=current_user_id))
    _parallel_results_dag = await asyncio.gather(*_parallel_tasks_dag)
    # _resolve_agent_config is always the last task appended
    agent_cfg = _parallel_results_dag[-1]

    from fim_one.db import create_session as _create_session
    try:
        async with _create_session() as _llm_db:
            llm, fast_llm, _fast_context_budget, _context_budget, dag_model_supports_vision = (
                await asyncio.gather(
                    _resolve_llm(agent_cfg, _llm_db),
                    _resolve_fast_llm(agent_cfg, _llm_db),
                    get_effective_fast_context_budget(_llm_db),
                    get_effective_context_budget(_llm_db),
                    _resolve_model_supports_vision(agent_cfg, _llm_db),
                )
            )
    except ValueError as exc:
        raise AppError(
            "agent_config_error",
            status_code=500,
            detail=str(exc),
            detail_args={"reason": str(exc)},
        ) from exc

    # -- Wrap primary LLM with fallback for availability resilience ----------
    if fast_llm and fast_llm is not llm:
        llm = FallbackLLM(primary=llm, fallback=fast_llm)

    tools = await _resolve_tools(agent_cfg, conversation_id, user_id=current_user_id)
    agent_instructions = agent_cfg["instructions"] if agent_cfg else None
    lang_directive = get_language_directive(preferred_language)

    # -- Layered extra_instructions assembly (mirrors ReAct path) -----------
    parts: list[str] = []
    if agent_instructions:
        parts.append(f"## Agent Directive\n{agent_instructions}")
    pref_parts: list[str] = []
    if lang_directive:
        pref_parts.append(lang_directive)
    if user_system_instructions:
        pref_parts.append(f"User's personal instructions:\n{user_system_instructions}")
    if pref_parts:
        parts.append("## User Preferences\n" + "\n\n".join(pref_parts))
    extra_instructions = "\n\n".join(parts) if parts else None

    if agent_cfg and agent_cfg.get("kb_ids"):
        grounding_hint = _kb_system_hint(agent_cfg)
        extra_instructions = (extra_instructions or "") + grounding_hint

    # Inject skill stubs — skills are global SOPs
    _dag_skill_ids = await _resolve_user_skill_ids(current_user_id) if current_user_id else None
    _dag_skill_descs: list[dict[str, str]] = []
    if _dag_skill_ids:
        _skill_mode = get_skill_tool_mode(agent_cfg)
        if _skill_mode == "inline":
            _skill_block = await _resolve_skill_inline(_dag_skill_ids)
        else:
            _skill_block = await _resolve_skill_stubs(_dag_skill_ids)
        if _skill_block:
            extra_instructions = (extra_instructions or "") + _skill_block
        # Also resolve compact descriptors for planner skill discovery.
        _dag_skill_descs = await _resolve_skill_descriptors(_dag_skill_ids)

    # Load attached images (async to avoid blocking the event loop)
    dag_image_data: list[tuple[str, str, str]] = []
    if image_ids:
        dag_image_data = await _load_image_data_urls(image_ids, current_user_id)

    # Gate user-uploaded images: only send as vision content when model supports it
    if dag_image_data and not dag_model_supports_vision:
        img_refs = ", ".join(f"{fname} (file_id: {fid})" for fid, fname, _ in dag_image_data)
        q = f"{q}\n\n[Attached images (text-only model, not displayed): {img_refs}]"

    # Load document vision pages (PDF/DOCX/PPTX rendered as images for vision models)
    dag_doc_vision_urls: list[str] = []
    if image_ids:
        dag_doc_vision_urls = await _load_document_vision_urls(
            image_ids=image_ids,
            user_id=current_user_id,
            doc_mode=dag_doc_mode,
            model_supports_vision=dag_model_supports_vision,
        )

    async def generate() -> AsyncGenerator[str, None]:  # noqa: C901
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        dag_user_mcp_client = await _connect_pending_mcp_servers(tools, agent_cfg)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_one.db import create_session
                from fim_one.web.models import Message as MessageModel

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
                dag_all_images: list[dict[str, str]] = []
                if dag_image_data:
                    dag_all_images.extend(
                        {
                            "file_id": fid,
                            "filename": fname,
                            "mime_type": durl.split(";")[0].split(":")[1],
                            "source": "upload",
                        }
                        for fid, fname, durl in dag_image_data
                    )
                if dag_doc_vision_urls and image_ids and current_user_id:
                    from fim_one.web.api.files import _load_index as _dag_dv_load

                    _dag_dv_idx = _dag_dv_load(current_user_id)
                    _DAG_DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}
                    for _dag_fid in image_ids.split(","):
                        _dag_fid = _dag_fid.strip()
                        _dag_meta = _dag_dv_idx.get(_dag_fid)
                        if _dag_meta:
                            _dag_name = str(_dag_meta.get("filename", ""))
                            if Path(_dag_name).suffix.lower() in _DAG_DOC_EXTS:
                                dag_all_images.append({
                                    "file_id": _dag_fid,
                                    "filename": _dag_name,
                                    "mime_type": str(_dag_meta.get("mime_type", "")),
                                    "source": "document",
                                })
                if dag_all_images:
                    dag_final_metadata["images"] = dag_all_images
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
        if dag_image_data or dag_doc_vision_urls:
            annotations: list[str] = []
            if dag_image_data:
                img_refs = ", ".join(f"{fname} (file_id: {fid})" for fid, fname, _ in dag_image_data)
                annotations.append(f"Attached images: {img_refs}")
            if dag_doc_vision_urls:
                annotations.append(
                    f"Document vision: {len(dag_doc_vision_urls)} PDF page(s) rendered for visual analysis"
                )
            enriched_query = f"{q}\n\n[{'; '.join(annotations)}]"
        dag_memory = None
        if conversation_id:
            try:
                from fim_one.core.memory import DbMemory

                dag_memory = DbMemory(
                    conversation_id=conversation_id,
                    max_tokens=_context_budget,
                    compact_llm=fast_llm,
                    user_id=current_user_id,
                    usage_tracker=fast_usage_tracker,
                )
                history = await dag_memory.get_messages()
                if history:
                    from fim_one.core.memory.compact import CompactUtils as _CU
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
                    from fim_one.core.memory.compact import CompactUtils
                    from fim_one.core.memory.context_guard import _COMPACT_PROMPTS

                    enriched_tokens = CompactUtils.estimate_tokens(enriched_query)
                    if enriched_tokens > 16_000:
                        if fast_llm:
                            from fim_one.core.model.types import ChatMessage

                            summary_result = await fast_llm.chat([
                                ChatMessage(
                                    role="system",
                                    content=_COMPACT_PROMPTS["planner_input"],
                                ),
                                ChatMessage(role="user", content=context_str),
                            ])
                            if summary_result.usage:
                                await fast_usage_tracker.record(summary_result.usage)
                            _content = summary_result.message.content
                            summary = (
                                _content if isinstance(_content, str) else str(_content or "")
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
            # Accumulate (plan, analysis) from every completed round so
            # the re-planner can see the full execution history.
            round_history: list[tuple[ExecutionPlan, AnalysisResult]] = []

            # -- Domain detection middleware — classify query domain for
            # planner guidance.  Called once before the planning loop.
            from fim_one.core.planner.domain import classify_domain
            _domain_hint = await classify_domain(q, fast_llm)

            while True:
                round_num += 1
                inject_in_round = False
                # -- Build replan context from ALL previous rounds ---------
                replan_context = ""
                if round_history:
                    replan_context = _format_replan_context(round_history)

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

                tool_descriptors = [
                    {"name": t.name, "description": t.description}
                    for t in tools.list_tools()
                ]
                _domain_context = replan_context
                if _domain_hint:
                    _domain_guidance = (
                        f"[Domain: {_domain_hint}] This is a {_domain_hint}-domain task. "
                        f"For steps that involve analysis, synthesis, or report writing, "
                        f"set model_hint=\"reasoning\" to ensure accuracy and reduce "
                        f"citation hallucination.  Avoid splitting tightly coupled "
                        f"analysis dimensions into separate steps — keep cross-"
                        f"referencing analysis together in fewer, deeper steps."
                    )
                    _domain_context = (
                        _domain_guidance + "\n\n" + replan_context
                        if replan_context
                        else _domain_guidance
                    )

                planner = DAGPlanner(llm=llm, language_directive=lang_directive)
                plan = await planner.plan(
                    enriched_query,
                    context=_domain_context,
                    tools=tool_descriptors,
                    skill_descriptions=_dag_skill_descs or None,
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
                    custom_compact_prompt=agent_cfg.get("compact_instructions") if agent_cfg else None,
                )

                # Inject fast usage tracker into grounded retrieve tool
                from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool
                for tool in tools._tools.values():
                    if isinstance(tool, GroundedRetrieveTool):
                        tool.set_usage_tracker(fast_usage_tracker)

                agent = ReActAgent(
                    llm=fast_llm,
                    tools=tools,
                    extra_instructions=extra_instructions,
                    max_iterations=dag_step_max_iters,
                    context_guard=dag_context_guard,
                    user_timezone=user_timezone,
                    agent_directive=agent_instructions,
                )
                from fim_one.db import create_session as _create_registry_session
                async with _create_registry_session() as _registry_db:
                    registry = await get_model_registry_with_group(_registry_db)
                exec_stop_event = asyncio.Event()
                executor = DAGExecutor(
                    agent=agent,
                    max_concurrency=get_max_concurrency(),
                    model_registry=registry,
                    context_guard=dag_context_guard,
                    original_goal=enriched_query,
                    stop_event=exec_stop_event,
                    enable_tool_cache=get_dag_tool_cache_enabled(),
                    verify_llm=fast_llm if get_dag_step_verification() else None,
                    domain_hint=_domain_hint,
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
                                "result": s.result.summary if s.result else None,
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

                # -- Record this round for future re-planning context ------
                round_history.append((plan, analysis))

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
                    answer = "The task could not be completed — none of the planned steps produced a result. You can try rephrasing the goal or breaking it into simpler steps."
                for _ans_chunk in _chunk_answer(answer):
                    yield _emit(
                        sse_events, "answer",
                        {"status": "delta", "content": _ans_chunk},
                    )

            yield _emit(sse_events, "answer", {"status": "done"})

            # -- Classify deliverables from all artifacts --
            dag_all_artifacts: list[dict[str, Any]] = []
            for evt in sse_events:
                if evt["event"] == "step_progress":
                    sp_data = evt["data"]
                    if sp_data.get("artifacts"):
                        for a in sp_data["artifacts"]:
                            dag_all_artifacts.append({
                                **a,
                                "tool_name": sp_data.get("tool_name", ""),
                                "step_id": sp_data.get("step_id", ""),
                            })

            dag_deliverables: list[dict[str, Any]] = []
            if dag_all_artifacts:
                # Terminal steps = steps that no other step depends on (DAG sinks)
                all_dep_targets: set[str] = set()
                for s in plan.steps:
                    all_dep_targets.update(s.dependencies)
                terminal_step_ids = {s.id for s in plan.steps} - all_dep_targets

                # Artifacts from terminal steps are deliverables
                dag_deliverables = [
                    a for a in dag_all_artifacts
                    if a.get("step_id") in terminal_step_ids
                ]
                # Fallback: if terminal steps produced no artifacts, use all
                if not dag_deliverables:
                    dag_deliverables = dag_all_artifacts

            dag_done_payload: dict[str, Any] = {
                "answer": answer,
                "achieved": analysis.achieved,
                "confidence": analysis.confidence,
                "elapsed": elapsed,
                "rounds": plan.current_round,
            }
            if dag_deliverables:
                # Strip internal keys from deliverable dicts before sending to client
                dag_done_payload["deliverables"] = [
                    {k: v for k, v in d.items() if k not in ("tool_name", "step_id")}
                    for d in dag_deliverables
                ]
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
                from fim_one.db import create_session
                db_session = create_session()

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_one.web.models import (
                        Conversation as ConvModel,
                        Message as MessageModel,
                    )

                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        message_type="done",
                        metadata_={**dag_done_payload, "sse_events": sse_events, "mode": "dag"},
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

            # -- Unregister interrupt queue immediately after done ------
            # No agent loop is consuming injected messages anymore.
            # Unregistering causes inject API to return 404, signaling
            # the frontend to queue messages for the next turn instead.
            if dag_interrupt_queue is not None and conversation_id:
                await get_broker().unregister(conversation_id)
                dag_interrupt_queue = None  # prevent double-unregister in finally

            # -- Send end immediately — post-processing runs in background --
            yield _sse("end", {})

            # -- Background post-processing: suggestions, title, fast token accounting --
            _dag_bg_conversation_id = conversation_id
            _dag_bg_fast_llm = fast_llm
            _dag_bg_query = q
            _dag_bg_answer = answer
            _dag_bg_preferred_language = preferred_language

            async def _dag_post_processing() -> None:
                """Background task for DAG suggestions, title, and fast-LLM token tracking."""
                from fim_one.db import create_session as _bg_create_session
                _bg_db: AsyncSession | None = None
                try:
                    _bg_usage_tracker = UsageTracker()

                    async def _bg_dag_maybe_generate_title() -> str | None:
                        if not _dag_bg_conversation_id:
                            return None
                        try:
                            from sqlalchemy import func as _sa_func
                            from fim_one.web.models import (
                                Conversation as ConvModel,
                                Message as _MsgModel,
                            )
                            async with _bg_create_session() as _cnt_db:
                                msg_count = (
                                    await _cnt_db.execute(
                                        sa_select(_sa_func.count())
                                        .select_from(_MsgModel)
                                        .where(_MsgModel.conversation_id == _dag_bg_conversation_id)
                                    )
                                ).scalar() or 0
                            if msg_count <= 2:
                                return await _generate_title(
                                    _dag_bg_fast_llm, _dag_bg_query, _dag_bg_answer,
                                    preferred_language=_dag_bg_preferred_language,
                                    usage_tracker=_bg_usage_tracker,
                                )
                        except Exception:
                            logger.debug("Auto-title generation failed", exc_info=True)
                        return None

                    dag_suggestions, dag_gen_title = await asyncio.gather(
                        _generate_suggestions(
                            _dag_bg_fast_llm, _dag_bg_query, _dag_bg_answer,
                            preferred_language=_dag_bg_preferred_language,
                            usage_tracker=_bg_usage_tracker,
                        ),
                        _bg_dag_maybe_generate_title(),
                    )

                    if not _dag_bg_conversation_id:
                        return

                    _bg_db = _bg_create_session()
                    if dag_suggestions:
                        try:
                            from fim_one.web.models import Message as _MsgModel
                            # Store suggestions in the most recent assistant message's metadata
                            _last_msg_stmt = (
                                sa_select(_MsgModel)
                                .where(
                                    _MsgModel.conversation_id == _dag_bg_conversation_id,
                                    _MsgModel.role == "assistant",
                                )
                                .order_by(_MsgModel.created_at.desc())
                                .limit(1)
                            )
                            _last_msg = (await _bg_db.execute(_last_msg_stmt)).scalar_one_or_none()
                            if _last_msg and _last_msg.metadata_:
                                _last_msg.metadata_["suggestions"] = dag_suggestions
                            elif _last_msg:
                                _last_msg.metadata_ = {"suggestions": dag_suggestions}
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist suggestions", exc_info=True)
                    if dag_gen_title:
                        try:
                            from sqlalchemy import update as _sa_update
                            from fim_one.web.models import Conversation as ConvModel
                            await _bg_db.execute(
                                _sa_update(ConvModel)
                                .where(ConvModel.id == _dag_bg_conversation_id)
                                .values(title=dag_gen_title)
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist title", exc_info=True)

                    # Capture fast LLM token usage
                    bg_fast_summary = _bg_usage_tracker.get_summary()
                    if bg_fast_summary.total_tokens > 0:
                        try:
                            from sqlalchemy import update as _sa_update, func as _sa_func
                            from fim_one.web.models import Conversation as ConvModel
                            await _bg_db.execute(
                                _sa_update(ConvModel)
                                .where(ConvModel.id == _dag_bg_conversation_id)
                                .values(
                                    total_tokens=_sa_func.coalesce(ConvModel.total_tokens, 0) + bg_fast_summary.total_tokens,
                                    fast_llm_tokens=_sa_func.coalesce(ConvModel.fast_llm_tokens, 0) + bg_fast_summary.total_tokens,
                                )
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.warning("Failed to persist fast LLM tokens", exc_info=True)
                except Exception:
                    logger.warning("DAG post-processing background task failed", exc_info=True)
                finally:
                    if _bg_db:
                        await _bg_db.close()

            asyncio.create_task(_dag_post_processing())
        except StructuredOutputError as exc:
            logger.warning("Structured output failed for model %s: %s", getattr(llm, 'model_id', '?'), exc)
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "done",
                {
                    "answer": (
                        "The current planning model failed to generate a valid task plan after multiple attempts. "
                        "This usually means the model's structured output capability is insufficient. "
                        "Please try switching to a more capable planning model (e.g. GPT-4o, Claude).\n\n"
                        "当前规划模型多次尝试后仍无法生成有效的任务计划。"
                        "这通常意味着该模型的结构化输出能力不足，建议更换为更强的规划模型（如 GPT-4o、Claude）。"
                    ),
                    "achieved": False,
                    "confidence": 0.0,
                    "elapsed": elapsed,
                },
            )
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


# ---------------------------------------------------------------------------
# Auto-routing endpoint
# ---------------------------------------------------------------------------


@router.post("/auto")
async def auto_endpoint(
    request: Request,
    body: ChatStreamRequest,
) -> StreamingResponse:
    """Auto-route a query to ReAct or DAG based on LLM classification.

    Emits a ``routing`` SSE event with ``{"mode": "react"|"dag", "reasoning": ...}``
    before delegating to the corresponding generation logic.

    Parameters
    ----------
    request : Request
        The incoming HTTP request; used to detect client disconnects.
    body : ChatStreamRequest
        JSON request body containing the query and optional parameters.
    """
    q = body.q
    token = body.token

    # -- Pre-stream: resolve auth to get fast_llm for classification --------
    current_user_id, _, _, _ = await _resolve_user(token)
    if current_user_id is None:
        raise AppError("authentication_required", status_code=401)

    # Determine execution mode
    from fim_one.core.planner.router import classify_execution_mode

    from fim_one.db import create_session as _create_session
    async with _create_session() as _llm_db:
        fast_llm = await _resolve_fast_llm(
            await _resolve_agent_config(body.agent_id, body.conversation_id, user_id=current_user_id),
            _llm_db,
        )

    decision = await classify_execution_mode(q, fast_llm)
    mode = decision.mode
    reasoning = decision.reasoning

    # Domain detection is handled independently inside each endpoint.

    # Wrap the inner endpoint's StreamingResponse to prepend the routing event
    if mode == "dag":
        inner_response = await dag_endpoint(request, body)
    else:
        inner_response = await react_endpoint(request, body)

    async def auto_generate() -> AsyncGenerator[str, None]:
        # Emit routing decision first
        yield _sse("routing", {"mode": mode, "reasoning": reasoning})
        # Then delegate to the inner endpoint's generator
        async for chunk in inner_response.body_iterator:
            yield str(chunk) if not isinstance(chunk, str) else chunk

    return StreamingResponse(
        auto_generate(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
        },
    )
