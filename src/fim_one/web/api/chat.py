"""SSE chat endpoints for ReAct and DAG agent modes.

Both endpoints stream Server-Sent Events with the following event names:

- ``step``           – ReAct iteration progress (tool calls, thinking).
- ``step_title``     – Async fast-LLM label for a ReAct iteration
  (``{"iteration", "title"}``).  Shown in the live step timeline; arrival
  order is not guaranteed relative to the iteration's own step events.
- ``step_progress``  – DAG per-step progress (started / iteration / completed).
- ``phase``          – Pipeline phase transitions (selecting_tools / planning / executing / analyzing).
- ``compact``        – Context compaction occurred (original_messages, kept_messages).
- ``answer``         – Streamed answer text (start / delta / done) emitted before ``done``.
- ``error``          – Structured terminator (currently only ``code="QUOTA_EXCEEDED"``).
  Emitted mid-stream when the user's monthly token quota runs out
  during answer streaming.  Carries ``tokens_used``, ``quota``,
  ``reset_at`` (ISO-8601), and ``plan_slug`` so the frontend can
  render an upgrade dialog instead of misreading the early stream
  termination as a generic network error.  Always followed by the
  normal ``done`` / ``end`` events with the partial answer persisted.
- ``guardrail_tripwired`` – Content guardrail blocked the turn.  Carries
  ``kind`` (``"input"`` or ``"output"``), ``guardrail_name``, a
  human-readable ``reason``, and arbitrary ``output_info`` debug payload
  from the guardrail.  Always followed by a ``done`` event with
  ``guardrail_tripwired: True`` and the reason as the answer.
- ``done``           – Final result payload (answer complete, emitted immediately).
- ``post_processing`` – Phase marker emitted between ``done`` and ``end`` while
  follow-up suggestions are being generated.  Emitted for plain no-agent
  chats (always on) and for agents with ``suggest_followups=True``
  (opt-in per agent, default off).
- ``suggestions``    – Follow-up question list ``{"items": list[str]}``.
  Same gating as ``post_processing``.
- ``end``            – Stream terminator (emitted right after the optional
  ``suggestions`` event, NOT persisted).  Auto-title generation and
  fast-LLM token accounting run as a background task after ``end``.

A keepalive comment (``": keepalive\\n\\n"``) is emitted every 15 seconds of
inactivity to prevent proxy/browser timeouts during long LLM calls.

When the SSE client disconnects, running agent tasks are cancelled promptly
(checked every 0.5 s) so that LLM and tool work does not continue in the
background.
"""

from __future__ import annotations

import asyncio
import dataclasses
import base64
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.agent import ReActAgent
from fim_one.core.agent.react import FINISH_SIGNAL_ENABLED
from fim_one.core.assembly.principal import (
    AuthMethod,
    CallContext,
    Entry,
    Principal,
    provisional_principal,
    resolve_principal,
)
from fim_one.core.assembly.quota import check_quota, quota_reset_at, quota_status
from fim_one.core.assembly.sandbox import is_run_sandbox, sandbox_base
from fim_one.core.assembly.sandbox import sandbox_root as run_sandbox_root
from fim_one.core.assembly.toolset import (
    connector_tools,
    database_tools,
    kb_binding,
    mcp_tools,
)
from fim_one.core.assembly.errors import AGENT_UNAVAILABLE, AssemblyRefusal
from fim_one.core.assembly.visibility import ResourceKind, authorize_use, list_usable
from fim_one.core.agent.workspace import AgentWorkspace
from fim_one.core.agent.guardrail import (
    InputGuardrailResult,
    InputGuardrailTripwireTriggered,
    OutputGuardrailResult,
    OutputGuardrailTripwireTriggered,
)
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.model import BaseLLM
from fim_one.core.model.fallback import FallbackLLM
from fim_one.core.model.rate_limit import set_current_user_id as _rl_set_user
from fim_one.core.model.structured import StructuredOutputError
from fim_one.core.model.types import ChatMessage
from fim_one.core.model.usage import UsageSummary, UsageTracker
from fim_one.core.planner import (
    AnalysisResult,
    DAGExecutor,
    DAGPlanner,
    ExecutionPlan,
    PlanAnalyzer,
    PlanStep,
)
from fim_one.core.planner.checkpoint import DAGCheckpoint
from fim_one.core.tool import ToolRegistry
from fim_one.core.utils import (
    extract_json_value,
    get_language_directive,
    spawn_background,
    strip_tool_protocol,
)
from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.services import agent_runtime
from fim_one.db.models import User

from ..deps import (
    get_dag_max_replan_rounds,
    get_dag_replan_stop_confidence,
    get_dag_step_max_iterations,
    get_dag_step_verification,
    get_dag_tool_cache_enabled,
    get_effective_context_budget,
    get_effective_fast_context_budget,
    get_llm_by_config_id,
    get_llm_from_config,
    get_max_concurrency,
    get_model_registry_with_group,
    get_react_max_iterations,
    get_react_max_turn_seconds,
    get_react_max_turn_tokens,
    get_tools,
    resolve_chat_context_budgets,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensitive word check helper
# ---------------------------------------------------------------------------


def _build_guardrail_tripwired_payload(
    exc: InputGuardrailTripwireTriggered | OutputGuardrailTripwireTriggered,
) -> dict[str, Any]:
    """Serialise a guardrail tripwire exception for the SSE stream.

    Mirrors the schema the frontend renderer expects::

        {
            "kind": "input" | "output",
            "guardrail_name": str,
            "reason": str,           # human-readable
            "output_info": Any | None,
        }

    Field names are part of the public SSE contract — do not rename.
    """
    if isinstance(exc, InputGuardrailTripwireTriggered):
        result: InputGuardrailResult | OutputGuardrailResult = exc.result
        kind = "input"
        reason = (
            f"Input was blocked by guardrail '{result.guardrail.name}'. "
            "The agent will not run for this request."
        )
    else:
        result = exc.result
        kind = "output"
        reason = (
            f"Agent output was blocked by guardrail '{result.guardrail.name}'. "
            "The original answer has been suppressed."
        )
    return {
        "kind": kind,
        "guardrail_name": result.guardrail.name,
        "reason": reason,
        "output_info": result.output.output_info,
    }


async def _check_sensitive_words(text: str, db: AsyncSession) -> list[str]:
    """Return list of matched blocked words. Empty = clean."""
    from fim_one.db.models import SensitiveWord

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


def _next_cursor(sse_events: list[dict[str, Any]]) -> int:
    """Return the next monotonic cursor for the given sse_events list.

    Every persisted event carries a ``"cursor": int`` field so the
    ``/chat/resume`` endpoint can replay everything after a given
    position.  Cursors are assigned in append order, starting at ``0``.
    """
    return len(sse_events)


def _append_event(
    sse_events: list[dict[str, Any]],
    event: str,
    data: Any,
) -> dict[str, Any]:
    """Append an event frame to the persistence list with a cursor.

    Returns the stored dict so callers can still inspect or mutate it.
    Kept as a single entry point so cursor semantics stay monotonic even
    if multiple code paths in the same generator append directly.
    """
    entry = {"event": event, "data": data, "cursor": _next_cursor(sse_events)}
    sse_events.append(entry)
    return entry


def _emit(sse_events: list[dict[str, Any]], event: str, data: Any) -> str:
    """Persist an event and return the SSE frame to stream.

    The frame carries its cursor in the same ``{"cursor", "data"}``
    envelope ``/chat/resume`` replays, so a client tracks its position
    identically on both paths.  Emitting the bare payload here left the
    cursor only in the persisted copy: the client never saw one, kept
    ``lastCursor`` at its initial ``-1``, and a resume therefore matched
    every stored event and replayed the whole turn.
    """
    entry = _append_event(sse_events, event, data)
    return _sse(event, {"cursor": entry["cursor"], "data": data})


def _start_confirmation_pump(
    agent_id: str | None,
    conversation_id: str | None,
    user_id: str | None,
    progress_queue: "asyncio.Queue[str]",
    sse_events: list[dict[str, Any]],
) -> "asyncio.Task[None] | None":
    """Start the pump that puts this turn's confirmation and question cards on its stream.

    One entry for the ReAct and the DAG handler, so both listen on the
    scope the bridge files a request under: the agent, or the
    conversation for a chat with no agent. Events already queued for that
    scope were left by an earlier turn and are discarded first; a turn
    shows only the requests it raises. Returns ``None`` when the turn has
    no addressable scope (no agent and no conversation, or no user).
    """
    from fim_one.web.confirmation_sse import discard_pending, scope_for

    scope = scope_for(str(agent_id or ""), str(conversation_id or ""))
    user = str(user_id or "")
    if not scope or not user:
        return None
    stale = discard_pending(scope, user)
    if stale:
        logger.info(
            "Discarded %d confirmation event(s) left over from an earlier turn "
            "(scope=%s user=%s)",
            stale,
            scope,
            user,
        )
    return asyncio.create_task(
        _pump_inline_confirmations(scope, user, progress_queue, sse_events)
    )


async def _pump_inline_confirmations(
    scope: str,
    user_id: str,
    progress_queue: "asyncio.Queue[str]",
    sse_events: list[dict[str, Any]],
) -> None:
    """Forward confirmation / question events from the in-process queue.

    Runs for the lifetime of a single SSE response.  The bridge
    (``fim_one.web.confirmation_sse``) parks each inline ``ConfirmationRequest``
    on a ``(scope, user_id)`` queue (scope = agent_id, or
    ``conv:<conversation_id>`` for agent-less runs); this coroutine drains
    that queue and pushes frames onto the SSE ``progress_queue`` so the
    frontend sees ``awaiting_confirmation`` / ``awaiting_user_question``
    MID-stream (while the gate hook's or ask_user_question tool's DB poll
    is still blocked).

    Cancelled by the handler when the stream ends; never raises.
    """
    # Imported lazily so the import graph stays acyclic during module
    # initialisation (chat.py is imported by app.py before lifespan runs).
    from fim_one.web.confirmation_sse import queue_for

    if not scope or not user_id:
        return

    q = queue_for(scope, user_id)
    try:
        while True:
            event = await q.get()
            # The payload's ``type`` doubles as the SSE event name so one
            # pump serves both confirmation cards and question cards.
            event_name = str(event.get("type") or "awaiting_confirmation")
            _append_event(sse_events, event_name, event)
            try:
                progress_queue.put_nowait(_sse(event_name, event))
            except asyncio.QueueFull:
                logger.warning(
                    "SSE progress queue full — dropping %s "
                    "event for scope=%s user=%s",
                    event_name,
                    scope,
                    user_id,
                )
    except asyncio.CancelledError:
        # Expected at stream teardown.
        raise
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "inline-confirmation pump crashed for scope=%s user=%s",
            scope,
            user_id,
        )


def _extract_final_thinking(
    messages: list[Any] | None,
) -> dict[str, Any] | None:
    """Return the final assistant turn's thinking metadata.

    Walks ``messages`` in reverse and returns the first assistant
    ``ChatMessage`` carrying any thinking data.  Used by the ReAct/DAG
    endpoints to persist that metadata so subsequent turns can replay it:
    Anthropic rejects thinking blocks whose ``signature`` is missing or
    altered, and GPT-5.x loses its chain of thought without its reasoning
    items.

    Only the last such turn is stored, which is all the replay contract
    needs and keeps the row from growing with the conversation.

    Returns:
        A dict with ``content`` / ``signature`` keys (either may be an
        empty string when only one side is present), plus
        ``encrypted_items`` when the turn carried Responses-API reasoning
        items.  ``None`` when no assistant message carries thinking data.
    """
    if not messages:
        return None
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "assistant":
            continue
        reasoning = getattr(msg, "reasoning_content", None)
        signature = getattr(msg, "signature", None)
        items = getattr(msg, "reasoning_items", None)
        if reasoning or signature or items:
            thinking: dict[str, Any] = {
                "content": reasoning or "",
                "signature": signature or "",
            }
            if items:
                thinking["encrypted_items"] = items
            return thinking
    return None


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


def _should_stop_replanning(
    analysis: AnalysisResult,
    stop_confidence: float,
) -> bool:
    """Decide whether an unachieved goal is worth another autonomous round.

    Stop only when the analyzer judged the goal unreachable and is confident
    about that.  Confidence on its own is the wrong signal: it scores how
    sure the analyzer is of its verdict, so a confident "the deliverable is
    missing" — precisely the case re-planning exists to fix — would end the
    run instead of retrying it.

    Args:
        analysis: The analyzer's verdict for the round that just finished.
        stop_confidence: Minimum certainty required to give up.

    Returns:
        ``True`` when re-planning should stop.
    """
    return analysis.unrecoverable and analysis.confidence >= stop_confidence


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


def _suggest_followups_enabled(agent_cfg: dict[str, Any] | None) -> bool:
    """Whether this turn should generate follow-up suggestions.

    Plain no-agent chats are exploratory Q&A, so they always get
    suggestions.  Agent-bound chats follow the agent's per-agent
    ``suggest_followups`` toggle (default off — task-style agents skip
    the extra fast-model round-trip).
    """
    if agent_cfg is None:
        return True
    return bool(agent_cfg.get("suggest_followups"))


async def _generate_suggestions(
    fast_llm: BaseLLM,
    query: str,
    answer: str,
    *,
    count: int = 3,
    preferred_language: str | None = None,
    usage_tracker: UsageTracker | None = None,
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
            f"- {lang_directive}\n"
            if lang_directive
            else "- Match the language of the original query (e.g. Chinese query -> Chinese questions).\n"
        )
        # Chips are injected as the user's next message when clicked, so
        # every item must read in the user's voice (requesting the
        # assistant), never as the assistant interviewing the user.
        system_prompt = (
            "You write short follow-up chips a user can click to send as "
            "their next message after reading an answer.\n\n"
            "Voice (critical):\n"
            "- Phrase each chip as something the USER would type to the "
            "assistant — a request or question directed at the assistant.\n"
            "- NEVER reverse roles. Do not write preference surveys, "
            "multi-choice polls, or interviewer questions such as "
            "\"Which do you prefer…?\", \"你更偏好…还是…?\", "
            "\"What are your priorities?\", or \"Would you like A or B?\".\n"
            "- Prefer concrete, self-contained asks (deeper detail on a "
            "named point, alternatives, how/where/when/why about items "
            "already in the answer, practical next steps).\n"
            "- Do not restate the original query without a new angle; "
            "avoid near-duplicates across chips.\n\n"
            "Rules:\n"
            f"- Return AT MOST {count} items; fewer is fine.\n"
            "- If the exchange doesn't invite further discussion "
            "(greetings, thanks, farewells, brief acknowledgements), "
            "return an empty JSON array [].\n"
            "- Each item: single sentence, under 80 characters.\n"
            f"{lang_rule}"
            "- Return ONLY a JSON array of strings, no other text."
        )
        user_content = (
            f"User query: {query}\n\n"
            f"Assistant answer (truncated): {truncated_answer}\n\n"
            "Write chips the user would click to send next "
            "(user → assistant only)."
        )

        # This call sits inline between ``done`` and ``end``, so its latency
        # is directly visible as the chips' load time.  Reasoning-capable
        # fast models (GPT-5.x family) default to a mid reasoning effort on
        # bare requests and can burn 20s+ thinking about three one-liners —
        # pin effort low and cap output.  Non-reasoning models drop the
        # param silently (litellm.drop_params).
        result = await fast_llm.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ],
            max_tokens=1000,
            reasoning_effort="low",
        )

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
    fast_llm: BaseLLM,
    query: str,
    answer: str,
    *,
    preferred_language: str | None = None,
    usage_tracker: UsageTracker | None = None,
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
            f"- {lang_directive}\n"
            if lang_directive
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
        user_content = f"User: {query[:500]}\n\nAssistant: {answer[:500]}"

        result = await fast_llm.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ]
        )

        raw = str(result.message.content or "").strip().strip("\"'")
        if usage_tracker and result.usage:
            await usage_tracker.record(result.usage)

        if raw and len(raw) <= 100:
            return raw
        return None
    except Exception:
        logger.debug("_generate_title failed", exc_info=True)
        return None


async def _generate_step_title(
    fast_llm: BaseLLM,
    query: str,
    reasoning: str,
    tool_name: str,
    *,
    tool_args: str = "",
    prev_titles: list[str] | None = None,
    preferred_language: str | None = None,
) -> str | None:
    """Generate a one-line label for a ReAct iteration.

    Shown in the step timeline while the agent works (and kept for history),
    e.g. "核实定价数据并准备比较分析". Uses the fast LLM; on any failure
    returns ``None`` so the run is never disturbed.

    ``tool_args`` and ``prev_titles`` exist to keep consecutive labels
    distinct: the arguments (search query, URL, …) are what actually differ
    between look-alike iterations, and earlier labels are provided so the
    model describes what THIS step adds instead of re-paraphrasing the task.
    """
    try:
        lang_directive = get_language_directive(preferred_language)
        lang_rule = (
            f"- {lang_directive}\n"
            if lang_directive
            else "- Match the language of the user query.\n"
        )
        system_prompt = (
            "Label one step of an AI assistant's multi-step work. You get "
            "the assistant's reasoning for this step, the tool it is about "
            "to use with its arguments, and the labels of earlier steps.\n\n"
            "Rules:\n"
            "- One short sentence describing THIS step's specific action — "
            "lean on the tool arguments (what is being searched, fetched, "
            "run), not the overall task.\n"
            "- Do NOT repeat or paraphrase an earlier label. If this step "
            "retries a similar action, say what changed (narrower query, "
            "different source, next data set).\n"
            "- Under 40 characters. No quotes, no markdown, no trailing "
            "period.\n"
            f"{lang_rule}"
            "- Return ONLY the label text, nothing else."
        )
        prev_block = (
            "\n\nEarlier step labels:\n"
            + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(prev_titles))
            if prev_titles
            else ""
        )
        user_content = (
            f"User query: {query[:300]}{prev_block}\n\n"
            f"Step reasoning: {reasoning[:600]}\n\n"
            f"Tool about to run: {tool_name or '(none)'}\n"
            f"Tool arguments: {tool_args[:300] or '(none)'}"
        )
        result = await fast_llm.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ]
        )
        # Strip stray wrapping/quote/bracket chars the model sometimes echoes
        # from the reasoning text (e.g. a label ending in ``"】【``).
        raw = (
            str(result.message.content or "")
            .strip()
            .strip("\"'“”‘’「」『』【】()（）[]{}<>《》:：,，。.")
        )
        # A label that ballooned into a paragraph is worse than none.
        if raw and len(raw) <= 80 and "\n" not in raw:
            return raw
        return None
    except Exception:
        logger.debug("_generate_step_title failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Deliverable classification helper
# ---------------------------------------------------------------------------

# Tool default filenames that carry no information about the content.  When a
# content-duplicate exists under a real name, the generic one is the copy to drop.
_GENERIC_ARTIFACT_NAMES = frozenset({"rendered.html"})


def _dedupe_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse content-identical artifacts registered more than once.

    The same output is often registered twice in one run — e.g.
    ``template_render`` registers ``rendered.html`` and the agent then saves
    the identical HTML via file_ops under a real filename.  Group by content
    hash and keep one per group, preferring a specifically named file over a
    tool's generic default.  Artifacts without a hash (pre-upgrade history)
    are always kept.
    """
    kept_index_by_sha: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for a in artifacts:
        sha = a.get("sha256") or ""
        if not sha or sha not in kept_index_by_sha:
            if sha:
                kept_index_by_sha[sha] = len(result)
            result.append(a)
            continue
        idx = kept_index_by_sha[sha]
        kept = result[idx]
        if (
            kept.get("name") in _GENERIC_ARTIFACT_NAMES
            and a.get("name") not in _GENERIC_ARTIFACT_NAMES
        ):
            result[idx] = a
    return result


def _collect_dag_tools_used(steps: list[PlanStep]) -> list[str]:
    """Return the tools a DAG run actually called, in first-use order.

    The executor records each react step's real tool calls in
    ``result.data["tools_used"]``.  Checkpoints do not persist ``data``, so a
    completed step restored from one falls back to the planner's
    ``tool_hint``; failed and skipped steps without a record contribute
    nothing rather than a guess.
    """
    tools: list[str] = []
    for step in steps:
        data = step.result.data if step.result is not None else None
        if data is not None and "tools_used" in data:
            names = list(data["tools_used"])
        elif data is None and step.status == "completed" and step.tool_hint:
            names = [step.tool_hint]
        else:
            names = []
        for name in names:
            if name and name not in tools:
                tools.append(name)
    return tools


async def _classify_deliverables(
    fast_llm: BaseLLM,
    answer: str,
    artifacts: list[dict[str, Any]],
    *,
    usage_tracker: UsageTracker | None = None,
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
            "Watch for the same deliverable registered twice: a template renderer's "
            "generic output (e.g. 'rendered.html') next to a specifically named file "
            "of the same type and similar size is one deliverable, not two — include "
            "only the specifically named one.\n\n"
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
            size = a.get("size", 0)
            artifact_lines.append(f"[{i}] {name} ({mime}, {size} bytes, from {tool})")

        truncated_answer = answer[:2000]
        user_content = f"Agent answer (truncated):\n{truncated_answer}\n\nArtifacts:\n" + "\n".join(
            artifact_lines
        )

        result = await fast_llm.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_content),
            ]
        )

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
) -> tuple[Principal | None, str | None, str | None, str | None]:
    """Validate a JWT/API-key token and return
    ``(principal, system_instructions, preferred_language, timezone)``.

    The principal is who the turn acts as: the account, its organizations,
    how it authenticated and, for an API key, the key's scopes. Decoding
    the token, its type and its revocation are checked here; the principal
    itself is built by the assembly layer.

    Returns ``(None, None, None, None)`` when *token* is not provided, the
    account is switched off, or the token was revoked.
    Raises HTTPException(401) on invalid/expired tokens.
    """
    if not token:
        return None, None, None, None

    # -- API key authentication (fim_-prefixed tokens) ----------------------
    if token.startswith("fim_"):
        from fim_one.db import create_session
        from fim_one.web.auth import _authenticate_api_key

        from fim_one.web.api_key_scopes import enforce_scope

        async with create_session() as session:
            user, scopes = await _authenticate_api_key(token, session)
            # Every caller of this helper is a chat stream endpoint.
            enforce_scope(scopes, "chat")
            return (
                await resolve_principal(
                    session,
                    user_id=user.id,
                    auth=AuthMethod.API_KEY,
                    scopes=frozenset(scopes) if scopes is not None else None,
                    user=user,
                ),
                user.system_instructions,
                user.preferred_language,
                getattr(user, "timezone", None),
            )

    # -- JWT authentication -------------------------------------------------
    import jwt as pyjwt

    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    # Decode the JWT — works for both SSE tickets and normal access tokens
    try:
        payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise AppError("token_expired", status_code=401)
    except pyjwt.InvalidTokenError:
        raise AppError("invalid_token", status_code=401)

    # Token-type confinement: the SSE endpoints accept either a normal access
    # token (Authorization header) or a short-lived sse_ticket (query param).
    # Every other token type (refresh / bind_ticket / oauth_state / 2fa temp
    # token) shares the signing key and carries "sub", so without this check it
    # could be replayed here as an access token.
    if payload.get("type") not in ("access", "sse_ticket"):
        raise AppError("invalid_token_type", status_code=401)

    # SSE ticket or normal access token — both have "sub" as user_id
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise AppError("invalid_token_payload", status_code=401)

    # -- Fetch full user record from DB ------------------------------------
    system_instructions: str | None = None
    preferred_language: str | None = None
    user_timezone: str | None = None
    principal: Principal | None = None
    try:
        from fim_one.db import create_session
        from fim_one.db.models import User

        async with create_session() as session:
            result = await session.execute(sa_select(User).where(User.id == user_id))
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
                    datetime.fromtimestamp(iat, tz=UTC) if isinstance(iat, (int, float)) else iat
                )
                if token_issued <= user.tokens_invalidated_at.replace(tzinfo=UTC):
                    return None, None, None, None

            system_instructions = user.system_instructions
            preferred_language = user.preferred_language
            user_timezone = getattr(user, "timezone", None)
            principal = await resolve_principal(
                session, user_id=user_id, auth=AuthMethod.SESSION, user=user
            )
    except HTTPException:
        raise
    except Exception:
        logger.warning("Failed to load user record", exc_info=True)

    # The record could not be read (the token itself was valid): the turn
    # goes on as that user, with nothing known beyond the id.
    if principal is None:
        principal = provisional_principal(user_id)
    return principal, system_instructions, preferred_language, user_timezone


async def _validate_conversation_ownership(
    conversation_id: str,
    user_id: str,
) -> None:
    """Ensure the conversation belongs to *user_id*.  Raises 404 otherwise."""
    from fim_one.db import create_session
    from fim_one.db.models import Conversation

    async with create_session() as session:
        result = await session.execute(
            sa_select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise AppError("conversation_not_found", status_code=404)


def _build_quota_terminator_payload(
    *,
    monthly_tokens: int,
    user_quota: int,
    reset_at: str,
) -> dict[str, Any]:
    """Construct the ``error`` event payload for mid-stream quota cutoff.

    Schema (matches the structured terminator contract documented in
    the chat module docstring):

    - ``type``: always ``"error"`` — a sentinel for the frontend's
      generic event router so it can branch on the same field other
      events use (compare ``step.type``, ``answer.status``).
    - ``code``: machine-readable reason; ``"QUOTA_EXCEEDED"`` here.
    - ``tokens_used`` / ``quota``: live counters for the dialog.
    - ``reset_at``: ISO-8601 of the next quota reset — billing-aligned
      (subscription anniversary for paid, calendar month for free),
      resolved by the caller via ``core.assembly.quota.quota_reset_at``.
    - ``plan_slug``: reserved for the upcoming Stripe billing
      integration; ``"free"`` for now since plan tiers don't exist yet.
    """
    return {
        "type": "error",
        "code": "QUOTA_EXCEEDED",
        "tokens_used": monthly_tokens,
        "quota": user_quota,
        "reset_at": reset_at,
        "plan_slug": "free",
    }


def _run_failed_payload(exc: BaseException, elapsed: float) -> dict[str, Any]:
    """Construct the ``error`` event payload for a crashed run.

    A crash used to be delivered as a ``done`` event whose ``answer`` read
    ``"Agent error: ..."``.  The frontend renders ``done`` with a green
    check under a "Result" heading, so a failed turn was indistinguishable
    from a successful one and the raw exception text was presented as the
    agent's answer.  Routing it to the typed ``error`` channel instead
    keeps the two apart.

    ``message`` is what the user is shown; ``detail`` carries the
    exception for the developer-facing view.  The traceback stays in the
    server log.
    """
    return {
        "type": "error",
        "code": "AGENT_ERROR",
        "message": "The agent stopped because of an internal error.",
        "detail": f"{type(exc).__name__}: {exc}",
        "elapsed": elapsed,
    }


def _use_context(
    user_id: str | None,
    agent_cfg: dict[str, Any] | None,
    *,
    conversation_id: str | None = None,
    entry: Entry | None = None,
) -> CallContext:
    """The assembly-layer context of a resource lookup made for a chat turn.

    The caller is the principal. A resource reached through an agent is
    judged by the agent owner's view (the binding is a delegation); with no
    agent, or an agent config that names no owner, by the caller's own.
    """
    return agent_runtime.call_context(
        provisional_principal(user_id or ""),
        agent_cfg,
        conversation_id=conversation_id,
        entry=entry,
    )


async def _resolve_agent_config(
    agent_id: str | None,
    conversation_id: str | None,
    user_id: str | None = None,
    *,
    refuse_unavailable: bool = False,
    ctx: CallContext | None = None,
) -> dict[str, Any] | None:
    """Load agent configuration from DB.

    Resolution priority: explicit ``agent_id`` > conversation's bound agent.
    When *user_id* is provided, the agent must be usable by that user: one
    they own or are subscribed to, and switched on. *ctx*, when given, is
    the context the agent is entered under and replaces the plain "this
    user, directly" one built from *user_id*.

    An agent that is not usable is, with ``refuse_unavailable``, a refusal
    (the chat endpoints): ``agent_unavailable`` (409) when the conversation
    is bound to it, so the user is told to start a new conversation;
    ``resource_not_found`` / ``resource_inactive`` for an agent named
    explicitly. Without it, ``None``: the caller carries on with no agent
    (the composer's vision check, the only caller that does not refuse).

    Returns a dict with ``instructions``, ``tool_categories``,
    ``model_config_json``, ``kb_ids``, and ``grounding_config``, or ``None``.
    """
    from fim_one.db import create_session
    from fim_one.db.models import Agent, Conversation

    async def _bound_agent_id() -> str | None:
        if not conversation_id:
            return None
        async with create_session() as session:
            conv_result = await session.execute(
                sa_select(Conversation.agent_id).where(
                    Conversation.id == conversation_id,
                )
            )
            return conv_result.scalar_one_or_none() or None

    resolved_id = agent_id or await _bound_agent_id()
    if not resolved_id:
        return None

    async with create_session() as session:
        if user_id:
            _agent_decision = await authorize_use(
                session,
                ctx or _use_context(user_id, None, entry="chat"),
                ResourceKind.AGENT,
                [resolved_id],
            )
            if not refuse_unavailable:
                # The composer's vision check: a switched-off agent still
                # names the model its conversation would be answered by.
                _agents = _agent_decision.including("resource_inactive")
                agent = _agents[0] if _agents else None
            elif _agent_decision.usable:
                agent = _agent_decision.usable[0]
            else:
                refusal = _agent_decision.refused.get(resolved_id) or AssemblyRefusal(
                    "resource_not_found", 404
                )
                # The explicit agent and the conversation's own are told
                # apart only now, so a turn that goes through costs no
                # extra query.
                if not agent_id or await _bound_agent_id() == resolved_id:
                    # The conversation's own agent: nothing the user can do
                    # in this conversation puts it back.
                    raise AssemblyRefusal(
                        AGENT_UNAVAILABLE,
                        409,
                        "The agent this conversation uses is no longer available to you "
                        "(unpublished, unsubscribed or switched off). Start a new "
                        "conversation to continue.",
                        {"agent_id": resolved_id, "reason": refusal.error_code},
                    )
                raise refusal
        else:
            agent_result = await session.execute(sa_select(Agent).where(Agent.id == resolved_id))
            agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None
        return {
            "agent_id": agent.id,
            "name": agent.name,
            "instructions": agent.instructions,
            "tool_categories": agent.tool_categories,
            "model_config_json": agent.model_config_json,
            "kb_ids": agent.kb_ids,
            "connector_ids": agent.connector_ids,
            "mcp_server_ids": agent.mcp_server_ids,
            "grounding_config": agent.grounding_config,
            "sandbox_config": agent.sandbox_config,
            "owner_user_id": agent.user_id,
            "org_id": agent.org_id,
            "compact_instructions": agent.compact_instructions,
            "suggest_followups": bool(getattr(agent, "suggest_followups", False)),
        }


async def _admit_turn(
    principal: Principal, conversation_id: str | None, agent_id: str | None
) -> dict[str, Any] | None:
    """The checks a chat turn passes before it streams; returns the agent config.

    The conversation is confirmed to be the caller's before its agent is
    looked at: resolving the agent of someone else's conversation would
    answer ``agent_unavailable`` with that agent's id where "not found" is
    due. The quota check and the agent resolution then run together.
    """
    if conversation_id:
        await _validate_conversation_ownership(conversation_id, principal.user_id)
    _, agent_cfg = await asyncio.gather(
        check_quota(principal),
        agent_runtime.resolve_agent(
            CallContext(principal=principal, binding=None, entry="chat"),
            agent_id,
            conversation_id=conversation_id,
        ),
    )
    return agent_cfg


@dataclasses.dataclass(frozen=True)
class _AdmittedTurn:
    """A chat turn that passed :func:`_admit_turn`, and who it acts as.

    Auto mode admits the turn before it asks a model how to route, then
    hands this to the endpoint it routes to, so the user is resolved and
    the turn admitted once.
    """

    principal: Principal
    system_instructions: str | None
    preferred_language: str | None
    timezone: str | None
    agent_cfg: dict[str, Any] | None


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
    from fim_one.db.models.model_config import ModelConfig as ModelConfigORM

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
    from fim_one.db.models.model_provider import ModelGroup

    group_stmt = (
        sa_select(ModelGroup)
        .where(
            ModelGroup.is_active == True  # noqa: E712
        )
        .limit(1)
    )
    group_result = await db.execute(group_stmt)
    group = group_result.scalar_one_or_none()
    if group is not None and group.general_model:
        return bool(group.general_model.supports_vision)

    # System default model: check is_default=True system config
    default_stmt = (
        sa_select(ModelConfigORM.supports_vision)
        .where(
            ModelConfigORM.user_id == None,  # noqa: E711
            ModelConfigORM.category == "llm",
            ModelConfigORM.is_default == True,  # noqa: E712
            ModelConfigORM.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    default_result = await db.execute(default_stmt)
    val = default_result.scalar_one_or_none()
    if val is not None:
        return bool(val)

    return False


async def _resolve_vision_llm(
    agent_cfg: dict[str, Any] | None,
    db: AsyncSession,
) -> BaseLLM | None:
    """Find the best vision-capable LLM for OCR / image description.

    Used by :class:`MarkItDownTool` and the RAG ingestion pipeline
    (:mod:`fim_one.web.api.knowledge_bases._ingest_document`) to power
    ``markitdown-ocr``. Returns ``None`` when no vision-capable model
    is available — callers MUST gracefully fall back to text-only
    extraction in that case (no regression vs. the pre-OCR behavior).

    Resolution order (first hit wins):

    1. **Primary LLM (consistency)** — if the agent's configured model
       (via ``model_config_id`` or the active ModelGroup's general
       model) has ``supports_vision=True``, reuse it. Same API key,
       same billing, same rate-limit bucket as the agent's main
       conversation. This is the happy path for the common
       ``gpt-4o / claude-3-5-sonnet / gemini-1.5-pro`` deployments.
    2. **Active ModelGroup's fast model** — if the primary cannot do
       vision, prefer the group's fast model next. Fast models
       (``gpt-4o-mini``, ``claude-haiku``, ``gemini-1.5-flash``) are
       the ideal OCR workhorse: cheap, low-latency, and usually
       multimodal.
    3. **Active ModelGroup's general model** — quality fallback.
       Usually redundant with step 1 but caught as a separate step
       for tenants whose ``agent_cfg`` points to a non-group model.
    4. **ENV fallback (optimistic)** — when NO active ModelGroup is
       configured (pure ENV mode), return the primary ENV LLM on the
       assumption that the user's ``LLM_MODEL`` supports vision.
       Bypassed when ``LLM_SUPPORTS_VISION=false`` is set — use that
       flag to opt out when the ENV-configured model does not support
       vision (e.g. DeepSeek-V3, Qwen-chat) to avoid a failing
       ``chat.completions.create`` call on every document upload.

    Reasoning models are intentionally **never** preferred. Reasoning
    tiers (o1, o3-mini, DeepSeek-R1) historically lack vision support
    and are the wrong tool for OCR (perception ≠ deliberation). If a
    workspace has only a reasoning model with ``supports_vision=True``
    it will still be picked up via the primary-LLM path, but this
    resolver does not actively rank it above fast/general.
    """
    from fim_one.web.deps import _build_llm_from_group_model
    from fim_one.db.models.model_provider import ModelGroup

    # Step 1 — Primary LLM if it advertises vision support.
    if await _resolve_model_supports_vision(agent_cfg, db):
        try:
            return await _resolve_llm(agent_cfg, db)
        except Exception:
            logger.warning(
                "Primary LLM marked supports_vision=True but resolution failed",
                exc_info=True,
            )

    # Step 2/3 — Walk the active ModelGroup. When a group is active,
    # it is the single source of truth — do NOT fall through to ENV
    # below, because the admin has explicitly curated the pool.
    group_stmt = (
        sa_select(ModelGroup)
        .where(
            ModelGroup.is_active == True  # noqa: E712
        )
        .limit(1)
    )
    group_result = await db.execute(group_stmt)
    group = group_result.scalar_one_or_none()

    if group is not None:
        # Priority order: fast (cost-optimal for OCR batching) →
        # general (quality fallback). Reasoning intentionally omitted.
        for candidate in (group.fast_model, group.general_model):
            if candidate is None or not candidate.supports_vision:
                continue
            built = _build_llm_from_group_model(candidate)
            if built is not None:
                return built
        # No model explicitly flagged as vision-capable. Rather than
        # disabling OCR entirely, try the general model as best-effort —
        # markitdown_core's try/except will fall back to text-only if
        # the model truly cannot handle vision at the wire level.
        if group.general_model is not None:
            logger.info(
                "No model in active ModelGroup has supports_vision=True; "
                "trying general model '%s' as best-effort OCR backend. "
                "To guarantee OCR: set supports_vision=True on a "
                "vision-capable model in Admin → Models.",
                group.general_model.name,
            )
            built = _build_llm_from_group_model(group.general_model)
            if built is not None:
                return built
        return None

    # Step 4 — ENV mode fallback (no active ModelGroup curated by admin).
    # Default is optimistic: assume LLM_MODEL supports vision (covers
    # gpt-4o, claude-3-5-sonnet, gemini-1.5-pro — the usual suspects).
    # Users whose ENV model can't do vision set LLM_SUPPORTS_VISION=false
    # to skip the optimistic attempt and avoid per-upload failure noise.
    if (os.getenv("LLM_SUPPORTS_VISION") or "").strip().lower() == "false":
        return None
    try:
        return await _resolve_llm(agent_cfg, db)
    except Exception:
        logger.warning("ENV vision fallback resolution failed", exc_info=True)
        return None


async def _build_markitdown_vision_deps(
    agent_cfg: dict[str, Any] | None,
    db: AsyncSession,
) -> OpenAICompatibleLLM | None:
    """Narrow the resolver's return to ``OpenAICompatibleLLM | None``.

    :class:`MarkItDownTool` and the RAG pipeline both need an
    ``OpenAICompatibleLLM`` (the concrete type that can be adapted by
    :class:`LiteLLMOpenAIShim`). :func:`_resolve_vision_llm` returns the
    wider ``BaseLLM`` type for orthogonality with the rest of the chat
    resolvers. This helper is the one place both callers reach for when
    they want MarkItDown-ready vision deps, so a future priority-rule
    change only happens here.
    """
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

    llm = await _resolve_vision_llm(agent_cfg, db)
    if llm is None:
        logger.debug("Vision LLM resolver returned None — OCR disabled")
        return None

    if isinstance(llm, OpenAICompatibleLLM):
        logger.debug(
            "Vision LLM resolved: %s (model_id=%s)",
            type(llm).__name__,
            getattr(llm, "model_id", "unknown"),
        )
        return llm

    # Handle FallbackLLM: unwrap to primary if it's OpenAI-compatible
    from fim_one.core.model.fallback import FallbackLLM

    if isinstance(llm, FallbackLLM) and isinstance(llm.primary, OpenAICompatibleLLM):
        logger.debug(
            "Unwrapping FallbackLLM → primary %s for OCR",
            type(llm.primary).__name__,
        )
        return llm.primary

    logger.warning(
        "Vision LLM resolver returned %s (not OpenAICompatibleLLM) — "
        "OCR disabled for this conversation. Model: %s",
        type(llm).__name__,
        getattr(llm, "model_id", "unknown"),
    )
    return None


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
    return sandbox_base() / conversation_id


def _conversation_workspace(conversation_id: str | None) -> AgentWorkspace | None:
    """Build the conversation's AgentWorkspace on the SHARED sandbox directory.

    The sandboxed tools (file_ops, shell_exec, python_exec, node_exec) all
    operate on ``{sandbox_root}/workspace`` — see ``discover_builtin_tools``.
    The workspace must sit on the same directory: it is where large tool
    outputs get offloaded, and a file the model was just told about must be
    visible to every tool that could read it (including inside exec
    containers, which mount this directory as ``/workspace``).  Splitting
    the two produced "list shows it, file_ops can't find it" dead ends.
    """
    if not conversation_id:
        return None
    sandbox_root = _conversation_sandbox_root(conversation_id)
    assert sandbox_root is not None  # conversation_id was checked above
    return AgentWorkspace(conversation_id, directory=sandbox_root / "workspace")


async def _resolve_bound_kb_owner_map(
    kb_ids: list[str], agent_owner_id: str | None
) -> dict[str, str]:
    """Map bound KB ids to their owners, gated by agent-owner visibility.

    A bound KB delegates its content to whoever runs the agent, but the
    delegation is only valid while the AGENT OWNER can currently see the KB
    (own + subscribed). Bindings are validated at bind time, but
    subscriptions can be reclaimed later (org exit), so the reference must
    be re-checked at use time. KBs that fail the check, or that their owner
    has disabled, are simply absent from the returned map.
    """
    from fim_one.db import create_session as _kb_cs
    from fim_one.db.models.knowledge_base import KnowledgeBase as _KBModel

    async with _kb_cs() as _kb_db:
        if agent_owner_id:
            # The agent owner is the subject; the KB rule reads nothing of
            # the caller. A disabled KB is out of service for every agent
            # bound to it.
            return await kb_binding(
                _kb_db, _use_context(agent_owner_id, None, entry="chat"), kb_ids
            )
        kb_owner_map: dict[str, str] = {}
        _kb_result = await _kb_db.execute(
            sa_select(_KBModel.id, _KBModel.user_id).where(
                _KBModel.id.in_(kb_ids),
                _KBModel.is_active == True,  # noqa: E712
            )
        )
        for row in _kb_result.all():
            kb_owner_map[row[0]] = row[1]
    return kb_owner_map


async def _resolve_tools(
    agent_cfg: dict[str, Any] | None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    *,
    entry: Entry | None = None,
    ctx: CallContext | None = None,
    sandbox_id: str | None = None,
) -> ToolRegistry:
    """Build tool registry, optionally scoped to a per-conversation sandbox.

    *entry* names where the tools will be used when that is not a chat turn
    (a ``call_agent`` delegate); it is what their call records carry. *ctx*,
    when the caller already holds the run's call context, is used as it is.

    *sandbox_id* names the directory the shell, code-execution and file
    tools work in when that is not *conversation_id*'s: a run's own
    directory (``core.assembly.sandbox``), or the conversation a delegate
    was called from. With neither, the registry has none of those tools.
    """
    # The context connector and MCP tools are built and recorded under.
    _tool_ctx = ctx or _use_context(
        user_id, agent_cfg, conversation_id=conversation_id, entry=entry
    )
    sandbox_config = agent_cfg.get("sandbox_config") if agent_cfg else None
    sandbox_root: Path | None
    uploads_root: Path | None = None
    if is_run_sandbox(sandbox_id):
        # A run's own directory: everything the run writes is under it, so
        # removing it when the run ends removes all of it.
        assert sandbox_id is not None
        sandbox_root = run_sandbox_root(sandbox_id)
        uploads_root = sandbox_root / "uploads"
    else:
        _files_of = sandbox_id or conversation_id
        sandbox_root = _conversation_sandbox_root(_files_of)
        # Per-conversation uploads dir so generated images are isolated and
        # cleaned up when the conversation is deleted.
        if _files_of:
            uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
            uploads_root = uploads_base / "conversations" / _files_of
    tools = get_tools(
        sandbox_root=sandbox_root,
        sandbox_config=sandbox_config,
        uploads_root=uploads_root,
    )
    if agent_cfg:
        cats = agent_cfg.get("tool_categories") or []
        tools = tools.filter_by_category(*cats)

    # ``send_notification`` acts for the caller, whichever agent it is used
    # through: it reaches the channels of the caller's organizations.
    tools = agent_runtime.bind_send_notification(tools, user_id)

    # Register the MarkItDown built-in tool with an injected vision LLM
    # resolved from the active workspace. Always registered (not gated by
    # category filter) because `convert_to_markdown` is a general-purpose
    # "read any file / URL" capability — the same tier as web_fetch.
    #
    # Resolved inside a local DB session (matches the GroundedRetrieveTool
    # pattern below). When no vision-capable model is available the tool
    # is still registered but runs in text-only mode, so agents never
    # lose the conversion capability on vision-less deployments.
    try:
        from fim_one.core.tool.builtin.markitdown_tool import MarkItDownTool
        from fim_one.db import create_session as _md_cs

        async with _md_cs() as _md_db:
            _md_vision_llm = await _build_markitdown_vision_deps(agent_cfg, _md_db)
        tools = tools.exclude_by_name("convert_to_markdown")
        tools.register(MarkItDownTool(vision_llm=_md_vision_llm, user_id=user_id))  # type: ignore[arg-type]
    except Exception:
        logger.warning(
            "Failed to register MarkItDownTool — Agent will not be able to "
            "call convert_to_markdown in this conversation",
            exc_info=True,
        )

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
        # The map is visibility-gated against the agent owner (see
        # _resolve_bound_kb_owner_map); KBs that fail the check are dropped
        # from kb_ids too — retrieval must not fall back to resolving them
        # under the caller's namespace.
        kb_owner_map: dict[str, str] = {}
        try:
            _kb_owner_user = (
                (agent_cfg.get("owner_user_id") or user_id) if agent_cfg else user_id
            )
            kb_owner_map = await _resolve_bound_kb_owner_map(
                kb_ids, _kb_owner_user
            )
            _dropped_kbs = [k for k in kb_ids if k not in kb_owner_map]
            if _dropped_kbs:
                logger.info(
                    "Dropping %d bound KB(s) no longer visible to agent "
                    "owner %s: %s",
                    len(_dropped_kbs),
                    _kb_owner_user,
                    _dropped_kbs,
                )
                kb_ids = [k for k in kb_ids if k in kb_owner_map]
        except Exception:
            logger.warning("Failed to resolve KB owners", exc_info=True)

        if retrieval_mode == "simple":
            from fim_one.core.tool.builtin.kb_retrieve import KBRetrieveTool

            tools.register(
                KBRetrieveTool(
                    user_id=user_id,
                    kb_ids=kb_ids,
                    kb_owner_map=kb_owner_map,
                )
            )
        else:
            from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

            grounding_config: dict[str, Any] = (agent_cfg or {}).get("grounding_config") or {}
            confidence_threshold = grounding_config.get("confidence_threshold")
            tools.register(
                GroundedRetrieveTool(
                    kb_ids=kb_ids,
                    user_id=user_id,
                    kb_owner_map=kb_owner_map,
                    confidence_threshold=confidence_threshold,
                )
            )
    elif user_id:
        # No bound KBs — keep basic kb_retrieve with user scope: the model
        # names a knowledge base per call, and the caller may read the ones
        # they own that are switched on.
        from fim_one.core.tool.builtin.kb_retrieve import KBRetrieveTool

        async def _own_kbs(named_kb_ids: Sequence[str]) -> dict[str, str]:
            from fim_one.db import create_session as _own_kb_cs

            async with _own_kb_cs() as _own_kb_db:
                return await kb_binding(
                    _own_kb_db,
                    _use_context(user_id, None, conversation_id=conversation_id),
                    named_kb_ids,
                )

        tools = tools.exclude_by_name("kb_retrieve")
        tools.register(KBRetrieveTool(user_id=user_id, kb_resolver=_own_kbs))

    # Load connector tools when the agent has bound connectors.
    connector_ids = agent_cfg.get("connector_ids") if agent_cfg else None
    if connector_ids:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from fim_one.core.tool.connector import get_connector_tool_mode
        from fim_one.core.tool.connector.database.meta_tool import get_database_tool_mode
        from fim_one.db import create_session
        from fim_one.db.models.connector import Connector as ConnectorModel

        try:
            async with create_session() as session:
                from fim_one.db.models.database_schema import (
                    DatabaseSchema as DatabaseSchemaModel,
                )

                # Judged by the agent owner's view (own + subscribed), so
                # org-shared and Market-installed connectors are accessible,
                # not just owner-owned ones. A connector its owner switched
                # off must not stay callable through agents that still
                # reference it.
                _conn_user_id = (
                    (agent_cfg.get("owner_user_id") or user_id) if agent_cfg else user_id
                )
                _conn_options = (
                    selectinload(ConnectorModel.actions),
                    selectinload(ConnectorModel.database_schemas).selectinload(
                        DatabaseSchemaModel.columns
                    ),
                )
                connectors: Sequence[Any]
                if _conn_user_id:
                    _conn_decision = await authorize_use(
                        session,
                        _use_context(user_id, agent_cfg, conversation_id=conversation_id),
                        ResourceKind.CONNECTOR,
                        connector_ids,
                        options=_conn_options,
                    )
                    # ``owner_only`` is applied by database_tools() below.
                    connectors = _conn_decision.including("owner_only")
                else:
                    result = await session.execute(
                        select(ConnectorModel)
                        .options(*_conn_options)
                        .where(
                            ConnectorModel.id.in_(connector_ids),
                            ConnectorModel.user_id == user_id,
                            ConnectorModel.is_active == True,  # noqa: E712
                        )
                    )
                    connectors = result.scalars().all()

                # The caller's credential decides whether each connector is
                # offered, and every call is recorded (core/assembly/toolset).
                for _conn_tool in await connector_tools(
                    session,
                    _tool_ctx,
                    [c for c in connectors if c.type != "database"],
                    mode=get_connector_tool_mode(agent_cfg),
                ):
                    tools.register(_conn_tool)
                for _db_tool in await database_tools(
                    session,
                    _tool_ctx,
                    [c for c in connectors if c.type == "database"],
                    mode=get_database_tool_mode(agent_cfg),
                ):
                    tools.register(_db_tool)
        except Exception:
            logger.warning("Failed to load connector tools", exc_info=True)

    elif user_id and not agent_cfg:
        # No-agent mode: auto-discover every connector the caller can use
        # (progressive mode). Credentials, the fallback gate and the call
        # record are the same as in a chat with an agent.
        try:
            from sqlalchemy.orm import selectinload

            from fim_one.db import create_session
            from fim_one.db.models.connector import Connector as ConnectorModel
            from fim_one.db.models.database_schema import (
                DatabaseSchema as DatabaseSchemaModel,
            )

            async with create_session() as _ad_session:
                _ad_connectors = await list_usable(
                    _ad_session,
                    _use_context(user_id, None, conversation_id=conversation_id),
                    ResourceKind.CONNECTOR,
                    options=(
                        selectinload(ConnectorModel.actions),
                        selectinload(ConnectorModel.database_schemas).selectinload(
                            DatabaseSchemaModel.columns
                        ),
                    ),
                )
                for _ad_tool in await connector_tools(
                    _ad_session,
                    _tool_ctx,
                    [c for c in _ad_connectors if c.type != "database"],
                    mode="progressive",
                ):
                    tools.register(_ad_tool)
                for _ad_db_tool in await database_tools(
                    _ad_session,
                    _tool_ctx,
                    [c for c in _ad_connectors if c.type == "database"],
                    mode="progressive",
                ):
                    tools.register(_ad_db_tool)
        except Exception:
            logger.warning("Failed to auto-discover connectors", exc_info=True)

    # Skills are global SOPs — always loaded regardless of agent selection.
    # Skipped entirely when the Skills module is soft-shelved (off by default).
    if user_id:
        from fim_one.db import create_session as _feat_cs
        from fim_one.web.services.feature_flags import (
            is_feature_enabled as _is_feat,
            SETTING_FEATURE_SKILLS as _SK,
            SETTING_FEATURE_WORKFLOWS as _WF,
        )

        async with _feat_cs() as _feat_db:
            _skills_on = await _is_feat(_feat_db, _SK)
            _workflows_on = await _is_feat(_feat_db, _WF)

        if _skills_on:
            _all_skill_ids = await _resolve_user_skill_ids(user_id)
            if _all_skill_ids and get_skill_tool_mode(agent_cfg) != "inline":
                from fim_one.core.tool.builtin.read_skill import ReadSkillTool

                tools.register(
                    ReadSkillTool(
                        skill_ids=_all_skill_ids,
                        user_id=user_id,
                    )
                )

        # Workflows are static recipes — the deterministic twin of skills.
        # Exposed the same way (global, pull-on-demand via run_workflow),
        # regardless of agent selection. Skipped when the module is shelved.
        if _workflows_on:
            _all_workflow_ids = await _resolve_user_workflow_ids(user_id)
            if _all_workflow_ids:
                from fim_one.core.tool.builtin.run_workflow import RunWorkflowTool

                tools.register(
                    RunWorkflowTool(
                        workflow_ids=_all_workflow_ids,
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
                ConnectorCreateActionTool,
                ConnectorDeleteActionTool,
                ConnectorGetSettingsTool,
                ConnectorImportOpenAPITool,
                ConnectorListActionsTool,
                ConnectorTestActionTool,
                ConnectorTestConnectionTool,
                ConnectorUpdateActionTool,
                ConnectorUpdateSettingsTool,
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
            logger.info("Injected connector builder tools for connector_id=%s", _builder_cid)

    # Inject Agent Builder tools when this is an Agent Builder Agent.
    if agent_cfg and "agent_builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re2

        _instructions2 = agent_cfg.get("instructions") or ""
        _m2 = _re2.search(r"target_agent_id=([a-f0-9-]{36})", _instructions2)
        if _m2:
            _builder_aid = _m2.group(1)
            from fim_one.core.tool.builtin.agent_builder import (
                AgentAddConnectorTool,
                AgentGetSettingsTool,
                AgentListConnectorsTool,
                AgentRemoveConnectorTool,
                AgentSetModelTool,
                AgentUpdateSettingsTool,
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
            logger.info("Injected agent builder tools for agent_id=%s", _builder_aid)

    # Inject DB Builder tools when this is a DB Builder Agent.
    if agent_cfg and "db_builder" in (agent_cfg.get("tool_categories") or []):
        import re as _re3

        _instructions3 = agent_cfg.get("instructions") or ""
        _m3 = _re3.search(r"connector_id=([a-f0-9-]{36})", _instructions3)
        if _m3:
            _builder_dbid = _m3.group(1)
            from fim_one.core.tool.builtin.db_builder import (
                DbAnnotateColumnTool,
                DbAnnotateTableTool,
                DbBatchSetVisibilityTool,
                DbGetConnectorSettingsTool,
                DbGetTableDetailTool,
                DbListTablesTool,
                DbRunSampleQueryTool,
                DbSetTableVisibilityTool,
                DbTestConnectionTool,
                DbUpdateConnectorSettingsTool,
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
        from fim_one.web.api.admin import SETTING_DISABLED_BUILTIN_TOOLS as _SDBT
        from fim_one.web.api.admin_utils import get_setting as _get_setting

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

        # Determine which MCP servers to load based on agent config
        _agent_mcp_ids = agent_cfg.get("mcp_server_ids") if agent_cfg else None
        _user_servers: Sequence[Any] = ()

        if agent_cfg and not _agent_mcp_ids:
            # Agent exists but has no MCP servers selected — skip
            pass
        else:
            async with _create_session() as _mcp_db:
                if _agent_mcp_ids:
                    # Agent mode: load only the specified MCP servers, and
                    # only those the AGENT OWNER can currently see (own +
                    # subscribed) — mirrors the connector path's
                    # _conn_user_id. Binding is validated at bind time, but
                    # subscriptions can be reclaimed later (org exit), so the
                    # reference must be re-checked at use time. Credentials
                    # still resolve against the CALLER when the servers are
                    # connected (core/assembly/credentials).
                    _mcp_owner_id = (
                        agent_cfg.get("owner_user_id") or user_id
                        if agent_cfg
                        else user_id
                    )
                    if _mcp_owner_id:
                        _mcp_decision = await authorize_use(
                            _mcp_db,
                            _use_context(user_id, agent_cfg, conversation_id=conversation_id),
                            ResourceKind.MCP_SERVER,
                            _agent_mcp_ids,
                        )
                        _user_servers = _mcp_decision.usable
                elif user_id:
                    # No-agent mode: load all visible active MCP servers
                    _user_servers = await list_usable(
                        _mcp_db,
                        _use_context(user_id, None, conversation_id=conversation_id),
                        ResourceKind.MCP_SERVER,
                    )

            if _user_servers:
                tools._pending_mcp_servers = list(_user_servers)  # type: ignore[attr-defined]
                tools._mcp_call_context = _tool_ctx  # type: ignore[attr-defined]
    except Exception:
        logger.warning("Failed to load MCP server configs", exc_info=True)

    # ------------------------------------------------------------------
    # Register CallAgentTool with all visible agents for multi-agent
    # delegation.
    # Only available when no specific agent is selected — prevents
    # marketplace agents from accessing other agents' private prompts.
    # ------------------------------------------------------------------
    if user_id and not agent_cfg:
        try:
            from fim_one.db import create_session as _cs_agents
            from fim_one.db.models.agent import Agent as AgentModel

            async with _cs_agents() as _cat_db:
                _visible_agents = await list_usable(
                    _cat_db,
                    _use_context(user_id, None, conversation_id=conversation_id),
                    ResourceKind.AGENT,
                    where=(AgentModel.is_builder == False,),  # noqa: E712
                )

            if _visible_agents:
                from fim_one.core.tool.builtin.call_agent import CallAgentTool

                # A catalogue of names. The delegate's configuration, tools
                # (its MCP servers included) and hooks are assembled by the
                # agent runtime when it is called, as for a chat with it.
                tools.register(
                    CallAgentTool(
                        available_agents=[
                            {"id": a.id, "name": a.name, "description": a.description or ""}
                            for a in _visible_agents
                        ],
                        # The delegate's calls are recorded as its own,
                        # not as the conversation's.
                        call_context=dataclasses.replace(
                            _tool_ctx,
                            binding=None,
                            entry="sub_agent",
                            conversation_id=None,
                            agent_id=None,
                        ),
                        # Its files are the conversation's, as in a chat
                        # with that agent in this conversation.
                        sandbox_id=conversation_id,
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

    pending = getattr(tools, "_pending_mcp_servers", None)
    if not pending:
        return None

    from fim_one.core.mcp import get_mcp_tool_mode

    # Whose credential each server is opened with, and which servers the
    # caller may not open at all, is decided in core/assembly.
    # A tool whose name is already registered is left out there, so that one
    # clashing name does not cost the turn.
    _mcp_tools, _mcp_client = await mcp_tools(
        getattr(tools, "_mcp_call_context", None) or _use_context(None, None),
        pending,
        mode=get_mcp_tool_mode(agent_cfg),
        taken=[_t.name for _t in tools.list_tools()],
    )
    for _mcp_tool in _mcp_tools:
        try:
            tools.register(_mcp_tool)
        except Exception:
            # From here the client is ours to hand back: nothing may raise
            # before the caller holds it and can disconnect it.
            logger.warning("Failed to register MCP tool %r", _mcp_tool.name, exc_info=True)
    return _mcp_client


# ---------------------------------------------------------------------------
# Image loading helper
# ---------------------------------------------------------------------------


def _get_retrieval_mode(agent_cfg: dict[str, Any] | None) -> str:
    """Resolve retrieval mode: agent grounding_config > RETRIEVAL_MODE env > 'grounding'."""
    grounding_config = (agent_cfg.get("grounding_config") or {}) if agent_cfg else {}
    return grounding_config.get("retrieval_mode") or os.environ.get("RETRIEVAL_MODE", "grounding")


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
    from fim_one.db.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill.name, Skill.description).where(
                    Skill.id.in_(skill_ids),
                    Skill.is_active == True,  # noqa: E712
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
                desc = desc[: _SKILL_STUB_DESC_LEN - 3] + "..."
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
    from fim_one.db.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill.name, Skill.description).where(
                    Skill.id.in_(skill_ids),
                    Skill.is_active == True,  # noqa: E712
                )
            )
            return [{"name": name, "description": desc or ""} for name, desc in result.all()]
    except Exception:
        logger.warning("Failed to resolve skill descriptors", exc_info=True)
        return []


async def _resolve_user_skill_ids(user_id: str) -> list[str]:
    """Fetch all active, visible skill IDs for a user (own + org + subscribed).

    Returns ``[]`` while the Skills module is shelved, so no caller can
    advertise skills (prompt stubs or inline content) that the disabled
    module's ``read_skill`` tool is not there to serve.
    """
    from fim_one.db import create_session
    from fim_one.db.models.skill import Skill
    from fim_one.web.services.feature_flags import (
        SETTING_FEATURE_SKILLS,
        is_feature_enabled,
    )

    try:
        async with create_session() as session:
            if not await is_feature_enabled(session, SETTING_FEATURE_SKILLS):
                return []
            rows = await list_usable(
                session, _use_context(user_id, None), ResourceKind.SKILL, columns=(Skill.id,)
            )
            return [row[0] for row in rows]
    except Exception:
        logger.warning("Failed to resolve user skill IDs", exc_info=True)
        return []


async def _resolve_user_workflow_ids(user_id: str) -> list[str]:
    """Fetch active, visible, inline-runnable workflow IDs for a user.

    Workflows are the *static recipe* twin of skills.  Excludes workflows
    carrying a ``HUMAN_INTERVENTION`` node — those block on approval and so are
    only triggerable from the Workflows page, never inline from an agent loop.
    Returns ``[]`` while the Workflows module is shelved.
    """
    from fim_one.core.workflow.blueprint_v2 import node_type_of
    from fim_one.core.workflow.types import NodeType
    from fim_one.db import create_session
    from fim_one.db.models.workflow import Workflow
    from fim_one.web.services.feature_flags import (
        SETTING_FEATURE_WORKFLOWS,
        is_feature_enabled,
    )

    try:
        async with create_session() as session:
            if not await is_feature_enabled(session, SETTING_FEATURE_WORKFLOWS):
                return []
            rows = await list_usable(
                session,
                _use_context(user_id, None),
                ResourceKind.WORKFLOW,
                columns=(Workflow.id, Workflow.blueprint),
                where=(Workflow.status == "active",),
            )
            ids: list[str] = []
            for wid, blueprint in rows:
                nodes = (blueprint or {}).get("nodes") or []
                if any(
                    node_type_of(n or {}) == NodeType.HUMAN_INTERVENTION
                    for n in nodes
                ):
                    continue
                ids.append(wid)
            return ids
    except Exception:
        logger.warning("Failed to resolve user workflow IDs", exc_info=True)
        return []


def _format_workflow_inputs(input_schema: Any) -> str:
    """Render a workflow's input schema as a compact ``name?:type`` field list."""
    if not isinstance(input_schema, dict):
        return ""
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(input_schema.get("required") or [])
    parts: list[str] = []
    for key, spec in props.items():
        typ = spec.get("type", "any") if isinstance(spec, dict) else "any"
        mark = "" if key in required else "?"
        parts.append(f"{key}{mark}:{typ}")
    return ", ".join(parts)


async def _resolve_workflow_stubs(workflow_ids: list[str]) -> str:
    """Return a compact stub block advertising runnable workflows."""
    from fim_one.db import create_session
    from fim_one.db.models.workflow import Workflow

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(
                    Workflow.name, Workflow.description, Workflow.input_schema
                ).where(Workflow.id.in_(workflow_ids))
            )
            rows = result.all()
        if not rows:
            return ""
        lines = [
            "\n\n## Available Workflows",
            "Call run_workflow(name, inputs) to execute one of these "
            "deterministic automations:",
        ]
        for name, desc, input_schema in rows:
            if desc and len(desc) > _SKILL_STUB_DESC_LEN:
                desc = desc[: _SKILL_STUB_DESC_LEN - 3] + "..."
            stub = f"- **{name}**" + (f": {desc}" if desc else "")
            fields = _format_workflow_inputs(input_schema)
            if fields:
                stub += f" (inputs: {fields})"
            lines.append(stub)
        return "\n".join(lines)
    except Exception:
        return ""


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
    from fim_one.db.models.skill import Skill

    try:
        async with create_session() as session:
            result = await session.execute(
                sa_select(Skill).where(
                    Skill.id.in_(skill_ids),
                    Skill.is_active == True,  # noqa: E712
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
    image_ids: str,
    user_id: str | None,
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
    "image",
    "vision",
    "image_url",
    "content_type",
    "multimodal",
    "not supported",
    "invalid content",
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
                logger.warning("PyMuPDF not installed, cannot extract PDF images")
            except Exception:
                logger.warning("PDF smart image extraction failed", exc_info=True)
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
    from fim_one.db.models import Conversation as _ConvModel

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
        from fim_one.db.models import Message as MessageModel

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
    from fim_one.db.models import Conversation as _ConvModel

    _conv_result = await db.execute(
        sa_select(_ConvModel.user_id).where(_ConvModel.id == body.conversation_id)
    )
    _conv_owner = _conv_result.scalar_one_or_none()
    if _conv_owner is None or str(_conv_owner) != str(current_user.id):
        raise AppError("forbidden", status_code=403)

    recalled = await get_broker().recall(body.conversation_id, body.inject_id)
    return {"success": recalled}


# ---------------------------------------------------------------------------
# Resume endpoint (Conversation Recovery MVP)
# ---------------------------------------------------------------------------


class ResumeStreamRequest(BaseModel):
    """Request body for ``POST /chat/resume``.

    Clients pass the last-seen monotonic cursor; the server replays every
    persisted SSE event with ``cursor > request.cursor`` from the most
    recent assistant message on the conversation, followed by a final
    ``resume_done`` frame.
    """

    conversation_id: str
    cursor: int = -1  # -1 replays the full event log


@router.post("/chat/resume")
async def resume_stream(
    body: ResumeStreamRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Replay cached SSE events for a disconnected stream.

    The ReAct / DAG endpoints persist every SSE frame to the assistant
    message's ``metadata_["sse_events"]`` with a monotonic ``cursor``.
    When a client's EventSource drops mid-turn, it can call this endpoint
    with the last cursor it successfully consumed and receive every event
    that landed afterwards — no agent work is re-run.

    Errors:
        - 404 ``conversation_not_found`` — conversation does not exist or
          is owned by a different user.
        - 404 ``no_recent_assistant_message`` — conversation has no
          assistant reply to resume from (e.g. the turn never completed).
    """
    from fim_one.db.models import (
        Conversation as _ConvModel,
    )
    from fim_one.db.models import (
        Message as MessageModel,
    )

    # -- Ownership check (never trust user_id from request body) -----------
    _owner = (
        await db.execute(sa_select(_ConvModel.user_id).where(_ConvModel.id == body.conversation_id))
    ).scalar_one_or_none()
    if _owner is None or str(_owner) != str(current_user.id):
        raise AppError("conversation_not_found", status_code=404)

    # -- Load the most recent assistant message with persisted events ------
    stmt = (
        sa_select(MessageModel)
        .where(
            MessageModel.conversation_id == body.conversation_id,
            MessageModel.role == "assistant",
        )
        .order_by(MessageModel.created_at.desc())
        .limit(1)
    )
    last_assistant = (await db.execute(stmt)).scalar_one_or_none()
    if last_assistant is None:
        raise AppError("no_recent_assistant_message", status_code=404)

    meta = last_assistant.metadata_ if isinstance(last_assistant.metadata_, dict) else {}
    cached_events: list[dict[str, Any]] = meta.get("sse_events", []) if meta else []
    if not isinstance(cached_events, list):
        cached_events = []

    # Filter by cursor.  Legacy events (persisted before this feature
    # shipped) may lack a cursor field — fall back to positional index so
    # resume still works on historical conversations.
    filtered: list[dict[str, Any]] = []
    for idx, evt in enumerate(cached_events):
        if not isinstance(evt, dict):
            continue
        raw_cursor = evt.get("cursor")
        cursor_val = raw_cursor if isinstance(raw_cursor, int) else idx
        if cursor_val > body.cursor:
            filtered.append({**evt, "cursor": cursor_val})

    async def _replay() -> AsyncGenerator[str, None]:
        for evt in filtered:
            event_name = str(evt.get("event", "message"))
            payload = {
                "cursor": evt.get("cursor"),
                "data": evt.get("data"),
            }
            yield _sse(event_name, payload)
        yield _sse(
            "resume_done",
            {
                "replayed": len(filtered),
                "last_cursor": filtered[-1]["cursor"] if filtered else body.cursor,
            },
        )

    return StreamingResponse(
        _replay(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
        },
    )


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
    return await _react_turn(request, body, None)


async def _react_turn(
    request: Request,
    body: ChatStreamRequest,
    admitted: _AdmittedTurn | None,
) -> StreamingResponse:
    """A ReAct turn. *admitted* is set when Auto mode already admitted it."""
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
    principal: Principal | None
    if admitted is not None:
        principal = admitted.principal
        user_system_instructions = admitted.system_instructions
        preferred_language = admitted.preferred_language
        user_timezone = admitted.timezone
    else:
        (
            principal,
            user_system_instructions,
            preferred_language,
            user_timezone,
        ) = await _resolve_user(token)
    if principal is None:
        raise AppError("authentication_required", status_code=401)
    current_user_id: str = principal.user_id

    # Bind the user id to the rate-limiter contextvar so per-user token
    # buckets can partition state without threading user_id through every
    # BaseLLM wrapper.  Scoped to this task; released automatically when
    # the handler returns because StreamingResponse runs us in a fresh
    # asyncio task copy of the context.
    _rl_set_user(str(current_user_id))

    # Timezone: user profile → X-Timezone header → UTC fallback
    if not user_timezone:
        user_timezone = request.headers.get("X-Timezone") or None

    # Auto mode admitted the turn before routing it here; once is enough.
    agent_cfg = (
        admitted.agent_cfg
        if admitted is not None
        else await _admit_turn(principal, conversation_id, agent_id)
    )
    # The context this turn's resources are used and recorded under.
    turn_ctx = agent_runtime.call_context(
        principal, agent_cfg, conversation_id=conversation_id
    )

    from fim_one.db import create_session as _create_session

    try:
        async with _create_session() as _llm_db:
            (llm, fast_llm), _context_budget, model_supports_vision = await asyncio.gather(
                agent_runtime.resolve_models(agent_cfg, _llm_db),
                get_effective_context_budget(_llm_db),
                _resolve_model_supports_vision(agent_cfg, _llm_db),
            )
            # Budget against the models this turn resolved, not the system
            # default.  The loop sends one message list to both, so it must
            # fit the smaller window.
            _budgets = await resolve_chat_context_budgets(
                llm, fast_llm, _llm_db, general_default=_context_budget
            )
            _context_budget = _budgets.shared
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
        agent_runtime.resolve_toolset(turn_ctx, agent_cfg),
        classify_domain(q, fast_llm),
    )

    # ask_user_question — ReAct-only human-in-the-loop clarification tool.
    # Registered here (not in _resolve_tools) so the DAG executor never
    # sees it: mid-plan questions don't fit a precomputed DAG.  Needs a
    # session factory independent of the request session because it polls
    # the confirmation_requests table while the response is streaming.
    try:
        from fim_one.core.tool.builtin.ask_user_question import AskUserQuestionTool
        from fim_one.db import create_session as _auq_create_session

        tools.register(
            AskUserQuestionTool(
                session_factory=_auq_create_session,
                user_id=str(current_user_id),
                agent_id=(agent_cfg or {}).get("agent_id"),
                org_id=(agent_cfg or {}).get("org_id"),
                conversation_id=conversation_id,
            )
        )
    except Exception:
        logger.warning(
            "Failed to register AskUserQuestionTool — agent cannot ask "
            "clarifying questions in this conversation",
            exc_info=True,
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

    # Inject workflow stubs — static recipes, the deterministic twin of skills
    _react_workflow_ids = (
        await _resolve_user_workflow_ids(current_user_id) if current_user_id else None
    )
    if _react_workflow_ids:
        _wf_block = await _resolve_workflow_stubs(_react_workflow_ids)
        if _wf_block:
            extra_instructions = (extra_instructions or "") + _wf_block
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
                    "Domain escalation: upgrading ReAct model from %s to %s for %s-domain task",
                    getattr(llm, "model_id", "unknown"),
                    getattr(_reasoning_llm, "model_id", "unknown"),
                    _react_domain_hint,
                )
                llm = _reasoning_llm
                # The escalated model reads the same message list.
                _budgets = _budgets.with_main_model(llm)
                _context_budget = _budgets.shared
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
                unhandled.append(f"  - {_meta.get('filename', 'unknown')} (file_id: {_fid})")
        if unhandled:
            q += (
                "\n\n[Attached files — use these file_ids with "
                "read_uploaded_file to access content:\n" + "\n".join(unhandled) + "]"
            )

    async def generate() -> AsyncGenerator[str, None]:
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []
        yield _emit(sse_events, "step", {"type": "thinking", "status": "start", "iteration": 1})

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        user_mcp_client = await agent_runtime.connect_mcp(tools, agent_cfg)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_one.db import create_session
                from fim_one.db.models import Message as MessageModel

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
                            "mime_type": durl.split(";")[0].split(":", 1)[1] if ":" in durl else "application/octet-stream",
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
                                all_images.append(
                                    {
                                        "file_id": _dfid,
                                        "filename": _dname,
                                        "mime_type": str(_dmeta.get("mime_type", "")),
                                        "source": "document",
                                    }
                                )
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

        # Fan pending inline confirmations / user questions into the SSE
        # stream mid-turn.  The hook (or the ask_user_question tool) inside
        # the agent task commits a ConfirmationRequest row and fires the
        # bridge listener; this pump turns those rows into
        # ``awaiting_confirmation`` / ``awaiting_user_question`` SSE frames
        # while the DB poll is still blocked.  Scope falls back to the
        # conversation for agent-less playground runs so question cards
        # work without an agent binding.  Cancelled below at teardown.
        _confirmation_pump_task = _start_confirmation_pump(
            (agent_cfg or {}).get("agent_id"),
            conversation_id,
            current_user_id,
            progress_queue,
            sse_events,
        )
        iter_start = time.time()
        thinking_done_iter = 0  # track which iteration's thinking-done was emitted
        current_iteration = 1  # track the current iteration for _on_answer_token
        answer_started = False
        # Fire-and-forget fast-LLM labels for the step timeline. Awaited (with
        # a short timeout) before ``sse_events`` is persisted so a late task
        # can't mutate the list mid-serialization.
        step_title_tasks: set[asyncio.Task[None]] = set()
        # Labels already produced, oldest first — fed back into the prompt so
        # consecutive look-alike iterations get distinct labels.
        step_title_history: list[str] = []
        # Two-phase labeling. An EARLY label fires mid-thinking (as soon as
        # enough reasoning has streamed) so the header updates before the
        # iteration's tool even starts; the DEFINITIVE label fires at tool
        # start with full context (tool + arguments) and overwrites it.
        early_title_spawned: set[int] = set()
        definitive_title_done: set[int] = set()

        async def _emit_step_title(
            iteration_no: int,
            reasoning: str,
            tool_name: str,
            tool_args: str,
            *,
            definitive: bool,
        ) -> None:
            title = await _generate_step_title(
                fast_llm,
                q,
                reasoning,
                tool_name,
                tool_args=tool_args,
                prev_titles=list(step_title_history[-5:]),
                preferred_language=preferred_language,
            )
            if not title:
                return
            # An early label must never clobber a definitive one that won the
            # race (fast thinking → both tasks in flight simultaneously).
            if not definitive and iteration_no in definitive_title_done:
                return
            if definitive:
                definitive_title_done.add(iteration_no)
            step_title_history.append(title)
            payload = {"iteration": iteration_no, "title": title}
            _append_event(sse_events, "step_title", payload)
            try:
                progress_queue.put_nowait(_sse("step_title", payload))
            except asyncio.QueueFull:
                logger.warning("SSE progress queue full, dropping step_title")

        def _spawn_step_title(
            iteration_no: int,
            reasoning: str,
            tool_name: str,
            tool_args: dict[str, Any] | None = None,
            *,
            definitive: bool = True,
        ) -> None:
            if fast_llm is None:
                return
            args_snip = ""
            if tool_args:
                try:
                    args_snip = json.dumps(tool_args, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    args_snip = str(tool_args)
            task = asyncio.create_task(
                _emit_step_title(
                    iteration_no, reasoning, tool_name, args_snip,
                    definitive=definitive,
                )
            )
            step_title_tasks.add(task)
            task.add_done_callback(step_title_tasks.discard)
        # Track unique tool names invoked this run — surfaced in the
        # per-agent completion notification (see
        # ``fim_one.web.notifications.notify_agent_completion``).
        tools_used_in_run: list[str] = []

        def _emit_step(payload: dict[str, Any]) -> None:
            """Emit a step SSE event to both persistence list and queue."""
            _append_event(sse_events, "step", payload)
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
                _append_event(sse_events, "inject", inject_payload)
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
                _append_event(sse_events, "phase", phase_payload)
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
                    _emit_step(
                        {
                            "type": "thinking",
                            "status": "start",
                            "iteration": iteration,
                        }
                    )
                return

            # -- Tool call lifecycle --
            if action.type == "tool_call":
                is_starting = observation is None and error is None

                if is_starting:
                    # Emit thinking done (once per iteration, before first tool)
                    if thinking_done_iter < iteration:
                        _emit_step(
                            {
                                "type": "thinking",
                                "status": "done",
                                "iteration": iteration,
                                "reasoning": action.reasoning,
                            }
                        )
                        thinking_done_iter = iteration
                        # Label the step off the loop's critical path.
                        _spawn_step_title(
                            iteration,
                            action.reasoning or "",
                            action.tool_name or "",
                            action.tool_args,
                        )

                    # Emit iteration start
                    iter_start = time.time()
                    # Record the tool for the completion notification.
                    # Deduplication happens inside the notifier — we
                    # record every call so order-of-first-use is stable.
                    if action.tool_name and action.tool_name not in tools_used_in_run:
                        tools_used_in_run.append(action.tool_name)
                    _emit_step(
                        {
                            "type": "iteration",
                            "status": "start",
                            "iteration": iteration,
                            "tool_name": action.tool_name,
                            "tool_args": action.tool_args,
                            "reasoning": action.reasoning,
                        }
                    )
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
                            payload["artifacts"] = (
                                [
                                    {
                                        "name": a["name"],
                                        "url": f"/api/conversations/{conversation_id}/artifacts/{a['path'].split('/')[-1].split('_', 1)[0]}",
                                        "mime_type": a["mime_type"],
                                        "size": a["size"],
                                        "sha256": a.get("sha256", ""),
                                    }
                                    for a in step_result.artifacts
                                ]
                                if conversation_id
                                else step_result.artifacts
                            )
                    _emit_step(payload)
                return

            # -- Final answer --
            if action.type == "final_answer":
                if thinking_done_iter < iteration:
                    _emit_step(
                        {
                            "type": "thinking",
                            "status": "done",
                            "iteration": iteration,
                            "reasoning": action.reasoning,
                        }
                    )
                    thinking_done_iter = iteration
                    # Label the concluding iteration too — without this the
                    # final timeline node falls back to a raw reasoning
                    # excerpt while every tool iteration shows a clean title.
                    if iteration > 1 and action.reasoning:
                        _spawn_step_title(
                            iteration,
                            action.reasoning,
                            "final_answer",
                        )

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
                    compact_input_budget=_budgets.fast,
                )
            context_guard = ContextGuard(
                compact_llm=fast_llm,
                default_budget=_context_budget,
                usage_tracker=fast_usage_tracker,
                custom_compact_prompt=agent_cfg.get("compact_instructions") if agent_cfg else None,
                compact_input_budget=_budgets.fast,
            )

            # Inject fast usage tracker into grounded retrieve tool
            from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

            for tool in tools._tools.values():
                if isinstance(tool, GroundedRetrieveTool):
                    tool.set_usage_tracker(fast_usage_tracker)

            # Always pin web_search — it's a fundamental capability that
            # tool selection should never drop, regardless of domain.
            # ask_user_question likewise: clarification must stay available
            # no matter which domain the tool selector picks.
            _pinned: list[str] = ["web_search", "ask_user_question"]

            # --- Hook System bootstrap ---
            # Load any class_hooks declared on this agent's ``model_config_json``
            # and hand the resulting HookRegistry to ReAct.  The session factory
            # (``create_session``) is independent of the per-request session
            # so hooks can open/commit their own transactions (ApprovalGateHook
            # polls the ``confirmation_requests`` table from a background
            # task while the request is still streaming).
            _hook_registry = await agent_runtime.build_hooks(
                agent_cfg, conversation_id=conversation_id
            )

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
                max_turn_seconds=get_react_max_turn_seconds(),
                hook_registry=_hook_registry,
                agent_id=(agent_cfg or {}).get("agent_id"),
                org_id=(agent_cfg or {}).get("org_id"),
                user_id=current_user_id,
                # Per-conversation workspace: enables large-output offload,
                # budget-truncation rescue (workspace:// pointer) and the
                # pre-compaction transcript snapshot.  Without it those
                # recovery paths silently degrade to permanent data loss.
                workspace=_conversation_workspace(conversation_id),
                # FINAL-first answering: the loop ends on a finish signal
                # and stream_answer generates the answer as a genuinely
                # streamed turn.  Safe here because this path ALWAYS calls
                # stream_answer after run(); kill switch REACT_FINISH_SIGNAL=0.
                finish_signal=FINISH_SIGNAL_ENABLED,
            )

            # Only send images as vision content when model supports it
            if image_data and model_supports_vision:
                image_urls: list[str] | None = [url for _, _, url in image_data]
            else:
                image_urls = None
            # Append document vision page images (rendered PDF/DOCX/PPTX pages)
            if doc_vision_urls:
                image_urls = (image_urls or []) + doc_vision_urls

            # Rolling buffer of the CURRENT iteration's thinking tokens,
            # feeding the early (mid-thinking) step label. Fires on the FIRST
            # complete sentence — tool-decision reasoning is often a single
            # short CJK sentence, so a large char threshold would never
            # trigger and the label would always wait for the tool boundary.
            _think_buf: dict[str, Any] = {"iter": 0, "chars": 0, "parts": []}
            _EARLY_TITLE_MIN_CHARS = 12
            _EARLY_TITLE_MAX_CHARS = 80
            _SENTENCE_ENDS = ("。", "！", "？", "．", ".", "!", "?", "\n")

            def on_thinking_delta(token: str) -> None:
                """Push reasoning/thinking tokens to the SSE stream."""
                _emit_step({"type": "thinking", "status": "delta", "content": token})
                # Early label: as soon as this iteration's reasoning yields a
                # first sentence, label the step — BEFORE its tool starts.
                # The definitive label (tool + args context) overwrites it.
                if _think_buf["iter"] != current_iteration:
                    _think_buf["iter"] = current_iteration
                    _think_buf["chars"] = 0
                    _think_buf["parts"] = []
                if (
                    current_iteration in early_title_spawned
                    or current_iteration in definitive_title_done
                ):
                    return
                _think_buf["parts"].append(token)
                _think_buf["chars"] += len(token)
                sentence_done = _think_buf["chars"] >= _EARLY_TITLE_MIN_CHARS and any(
                    p in token for p in _SENTENCE_ENDS
                )
                if sentence_done or _think_buf["chars"] >= _EARLY_TITLE_MAX_CHARS:
                    early_title_spawned.add(current_iteration)
                    _spawn_step_title(
                        current_iteration,
                        "".join(_think_buf["parts"]),
                        "",
                        None,
                        definitive=False,
                    )

            async def _run() -> Any:
                nonlocal image_urls
                try:
                    return await agent.run(
                        q,
                        on_iteration=on_iteration,
                        image_urls=image_urls,
                        interrupt_queue=interrupt_queue,
                        on_thinking_delta=on_thinking_delta,
                    )
                except Exception as exc:
                    # Vision fallback: if document pages caused the error,
                    # strip them and retry with text-only mode.
                    if doc_vision_urls and _is_vision_error(exc):
                        logger.warning(
                            "Vision content rejected by model, retrying without "
                            "document page images: %s",
                            exc,
                        )
                        # Keep user-uploaded images, only remove doc pages
                        if image_data and model_supports_vision:
                            image_urls = [url for _, _, url in image_data]
                        else:
                            image_urls = None
                        return await agent.run(
                            q,
                            on_iteration=on_iteration,
                            image_urls=image_urls,
                            interrupt_queue=interrupt_queue,
                            on_thinking_delta=on_thinking_delta,
                        )
                    raise
                finally:
                    done_event.set()

            run_task = asyncio.create_task(_run())

            last_keepalive = time.time()
            while not done_event.is_set():
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except TimeoutError:
                    if await request.is_disconnected():
                        logger.info("Client disconnected — cancelling ReAct task")
                        run_task.cancel()
                        try:
                            with suppress(asyncio.CancelledError, TimeoutError):
                                await asyncio.wait_for(run_task, timeout=5.0)
                        except Exception:
                            logger.exception("Unexpected error while cancelling ReAct task")
                        # Pump task is cancelled in the outer ``finally``;
                        # the early return here is still safe.
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
            quota_exceeded_mid_stream = False
            quota_terminator_payload: dict[str, Any] | None = None
            # Tokens already accounted for from the planner/tool LLM calls
            # (result.usage).  The streamed answer adds completion tokens on
            # top — we estimate them from emitted chunk lengths since the
            # streaming API does not surface per-delta usage.
            _result_tokens_so_far = (
                result.usage.total_tokens if result.usage is not None else 0
            )
            _streamed_chars_since_check = 0
            try:
                async for _token in agent.stream_answer(
                    q,
                    result,
                    language_directive=lang_directive,
                ):
                    answer_chunks.append(_token)
                    yield _emit(
                        sse_events,
                        "answer",
                        {"status": "delta", "content": _token},
                    )
                    # -- Mid-stream quota guard --------------------------
                    # Re-check the user's monthly quota at most once every
                    # ~200 characters (~50 estimated tokens) to keep DB
                    # load bounded while still cutting the stream within
                    # a small overshoot of the quota.  When the quota is
                    # exhausted we emit a structured terminator instead
                    # of letting the connection drop into a generic
                    # network error on the client.
                    if current_user_id:
                        _streamed_chars_since_check += len(_token)
                        if _streamed_chars_since_check >= 200:
                            _streamed_chars_since_check = 0
                            _est_completion_tokens = (
                                sum(len(c) for c in answer_chunks) // 4
                            )
                            _approx_total = (
                                _result_tokens_so_far + _est_completion_tokens
                            )
                            _quota_now = await quota_status(current_user_id)
                            _monthly_used, _quota_cap = _quota_now.used, _quota_now.cap
                            if (
                                _quota_cap > 0
                                and _monthly_used + _est_completion_tokens
                                >= _quota_cap
                            ):
                                quota_exceeded_mid_stream = True
                                quota_terminator_payload = (
                                    _build_quota_terminator_payload(
                                        monthly_tokens=(
                                            _monthly_used + _est_completion_tokens
                                        ),
                                        user_quota=_quota_cap,
                                        reset_at=(
                                            await quota_reset_at(current_user_id)
                                        ).isoformat(),
                                    )
                                )
                                logger.info(
                                    "Token quota exhausted mid-stream for user=%s "
                                    "(used=%s, quota=%s, approx_total=%s) — emitting "
                                    "structured terminator",
                                    current_user_id,
                                    _monthly_used + _est_completion_tokens,
                                    _quota_cap,
                                    _approx_total,
                                )
                                break
                answer = "".join(answer_chunks)
            except Exception:
                logger.warning(
                    "stream_answer failed, falling back to result.answer",
                    exc_info=True,
                )
                answer = strip_tool_protocol(result.answer or "")
                for _ans_chunk in _chunk_answer(answer):
                    yield _emit(
                        sse_events,
                        "answer",
                        {"status": "delta", "content": _ans_chunk},
                    )
            yield _emit(sse_events, "answer", {"status": "done"})

            # -- Mid-stream quota terminator (clean close) ----------------
            # Emit the structured ``error`` event BEFORE ``done`` so the
            # frontend dialog reads the latest counters.  We still proceed
            # through the normal done/end path so the partial answer and
            # consumed tokens are persisted, mirroring how a successful
            # turn terminates.
            if quota_exceeded_mid_stream and quota_terminator_payload is not None:
                yield _emit(sse_events, "error", quota_terminator_payload)

            # -- Classify deliverables from all artifacts --
            all_artifacts_with_context: list[dict[str, Any]] = []
            for evt in sse_events:
                if evt["event"] == "step":
                    step_data = evt["data"]
                    if step_data.get("status") == "done" and step_data.get("artifacts"):
                        for a in step_data["artifacts"]:
                            all_artifacts_with_context.append(
                                {
                                    **a,
                                    "tool_name": step_data.get("tool_name", ""),
                                }
                            )

            all_artifacts_with_context = _dedupe_artifacts(all_artifacts_with_context)

            deliverables: list[dict[str, Any]] = []
            if all_artifacts_with_context and fast_llm:
                deliverables = await _classify_deliverables(
                    fast_llm,
                    answer,
                    all_artifacts_with_context,
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
                    {k: v for k, v in d.items() if k != "tool_name"} for d in deliverables
                ]
            if result.usage is not None:
                done_payload["usage"] = {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens,
                }
                # Prompt-cache observability: surface Anthropic-style
                # cache counters to the client so it can eventually
                # render cost savings.  Always present (zeros when the
                # provider doesn't report caching) so the frontend can
                # treat the field as non-optional.
                done_payload["cache"] = {
                    "read_tokens": result.usage.cache_read_input_tokens,
                    "creation_tokens": result.usage.cache_creation_input_tokens,
                }
            # Final drain of any remaining injected messages.
            if interrupt_queue is not None:
                remaining = await interrupt_queue.drain()
                if remaining:
                    done_payload["pending_injections"] = [m.content for m in remaining]

            # Settle outstanding step-title tasks before ``sse_events`` is
            # serialized below; anything still running after the grace period
            # is cancelled (a late label is decoration, not data).
            if step_title_tasks:
                _, _pending_titles = await asyncio.wait(
                    step_title_tasks, timeout=2.0
                )
                for _pt in _pending_titles:
                    _pt.cancel()
                while not progress_queue.empty():
                    yield progress_queue.get_nowait()

            # Appended here rather than at the yield below because the
            # assistant row (which stores ``sse_events``) is written in
            # between.  The cursor is kept so the wire frame can carry the
            # same one the persisted copy got.
            _done_entry = _append_event(sse_events, "done", done_payload)

            # -- Re-open a fresh DB session for persistence ----------------
            # The original session was closed right after saving the user
            # message to avoid holding a connection during LLM execution.
            if conversation_id:
                from fim_one.db import create_session

                db_session = create_session()

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_one.db.models import (
                        Conversation,
                    )
                    from fim_one.db.models import (
                        Message as MessageModel,
                    )

                    react_thinking = _extract_final_thinking(result.messages)
                    react_metadata: dict[str, Any] = {
                        **done_payload,
                        "sse_events": sse_events,
                        "mode": "react",
                    }
                    if react_thinking is not None:
                        react_metadata["thinking"] = react_thinking
                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        message_type="done",
                        metadata_=react_metadata,
                    )
                    db_session.add(assistant_msg)
                    if "usage" in done_payload:
                        stmt = sa_select(Conversation).where(Conversation.id == conversation_id)
                        conv = (await db_session.execute(stmt)).scalar_one_or_none()
                        if conv:
                            conv.total_tokens = (conv.total_tokens or 0) + done_payload[
                                "usage"
                            ].get("total_tokens", 0)
                            if conv.model_name is None and llm.model_id:
                                conv.model_name = llm.model_id
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            # -- Yield done IMMEDIATELY (no suggestions/title yet) ------
            yield _sse(
                "done",
                {"cursor": _done_entry["cursor"], "data": done_payload},
            )

            # -- Fire per-agent completion notification (if configured) ----
            # Fire-and-forget: the notifier ALWAYS catches its own errors
            # and never raises, so we don't need to guard it here.  It is
            # posted AFTER the ``done`` event reaches the client, so the
            # user never waits on outbound IM calls.
            try:
                from fim_one.db import create_session as _notify_create_session
                from fim_one.web.notifications import notify_agent_completion

                _agent_notify_shim = SimpleNamespace(
                    id=(agent_cfg or {}).get("agent_id"),
                    name=(agent_cfg or {}).get("name") or "Agent",
                    org_id=(agent_cfg or {}).get("org_id"),
                    user_id=(agent_cfg or {}).get("owner_user_id"),
                    model_config_json=(agent_cfg or {}).get("model_config_json"),
                )
                spawn_background(
                    notify_agent_completion(
                        agent=_agent_notify_shim,
                        conversation_id=conversation_id,
                        user_message=q,
                        final_answer=answer,
                        tools_used=list(tools_used_in_run),
                        duration_seconds=float(elapsed),
                        session_factory=_notify_create_session,
                    )
                )
            except Exception:
                # Scheduling itself should never fail, but do not let an
                # import/construction error tank the chat response.
                logger.debug(
                    "Failed to schedule completion notification task",
                    exc_info=True,
                )

            # -- Unregister interrupt queue immediately after done ------
            # No agent loop is consuming injected messages anymore.
            # Unregistering causes inject API to return 404, signaling
            # the frontend to queue messages for the next turn instead.
            if interrupt_queue is not None and conversation_id:
                await get_broker().unregister(conversation_id)
                interrupt_queue = None  # prevent double-unregister in finally

            # -- Inline suggestions (no-agent always on; agents opt in) -----
            # Generated synchronously between ``done`` and ``end`` so the
            # frontend can render them without a refetch round-trip.  Skipped
            # when the agent toggle is off or the answer is empty — both
            # eliminate the extra LLM call.
            _suggest_usage_tracker = UsageTracker()
            _suggest_followups_on = _suggest_followups_enabled(agent_cfg)
            _react_suggestions: list[str] = []
            if _suggest_followups_on and (result.answer or "").strip():
                yield _emit(sse_events, "post_processing", {})
                try:
                    _react_suggestions = await _generate_suggestions(
                        fast_llm,
                        q,
                        result.answer,
                        preferred_language=preferred_language,
                        usage_tracker=_suggest_usage_tracker,
                    )
                except Exception:
                    logger.debug(
                        "Inline suggestions generation failed", exc_info=True
                    )
                yield _emit(
                    sse_events, "suggestions", {"items": _react_suggestions}
                )
                # -- Persist suggestions into the assistant message metadata.
                # Refresh ``sse_events`` on disk so stored-conversation replay
                # surfaces both events on the next page-load.
                if conversation_id and _react_suggestions:
                    try:
                        from fim_one.db import create_session as _persist_create_session
                        from fim_one.db.models import Message as _MsgModel

                        async with _persist_create_session() as _persist_db:
                            _last_msg = (
                                await _persist_db.execute(
                                    sa_select(_MsgModel)
                                    .where(
                                        _MsgModel.conversation_id == conversation_id,
                                        _MsgModel.role == "assistant",
                                    )
                                    .order_by(_MsgModel.created_at.desc())
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                            if _last_msg is not None:
                                _md = dict(_last_msg.metadata_ or {})
                                _md["suggestions"] = _react_suggestions
                                _md["sse_events"] = sse_events
                                _last_msg.metadata_ = _md
                                await _persist_db.commit()
                    except Exception:
                        logger.debug(
                            "Failed to persist inline suggestions", exc_info=True
                        )

            # -- Send end after optional suggestions ------------------------
            yield _sse("end", {})

            # -- Background: title generation + fast-LLM token accounting --
            # These don't need to stream to the user; the conversation list
            # refetches title and tokens are observability only.
            _bg_conversation_id = conversation_id
            _bg_fast_llm = fast_llm
            _bg_query = q
            _bg_answer = result.answer
            _bg_preferred_language = preferred_language
            _bg_inline_summary = _suggest_usage_tracker.get_summary()

            async def _react_post_processing() -> None:
                """Background task for title + fast-LLM token tracking."""
                from fim_one.db import create_session as _bg_create_session

                _bg_db: AsyncSession | None = None
                try:
                    _bg_usage_tracker = UsageTracker()

                    if not _bg_conversation_id:
                        return

                    gen_title: str | None = None
                    try:
                        from sqlalchemy import func as _sa_func

                        from fim_one.db.models import (
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
                            gen_title = await _generate_title(
                                _bg_fast_llm,
                                _bg_query,
                                _bg_answer,
                                preferred_language=_bg_preferred_language,
                                usage_tracker=_bg_usage_tracker,
                            )
                    except Exception:
                        logger.debug("Auto-title generation failed", exc_info=True)

                    _bg_db = _bg_create_session()
                    if gen_title:
                        try:
                            from sqlalchemy import update as _sa_update

                            from fim_one.db.models import Conversation

                            await _bg_db.execute(
                                _sa_update(Conversation)
                                .where(Conversation.id == _bg_conversation_id)
                                .values(title=gen_title)
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist title", exc_info=True)

                    # Capture fast LLM token usage (combined inline + bg).
                    bg_fast_summary = _bg_usage_tracker.get_summary()
                    combined_tokens = (
                        bg_fast_summary.total_tokens
                        + _bg_inline_summary.total_tokens
                    )
                    if combined_tokens > 0:
                        try:
                            from sqlalchemy import func as _sa_func
                            from sqlalchemy import update as _sa_update

                            from fim_one.db.models import Conversation

                            await _bg_db.execute(
                                _sa_update(Conversation)
                                .where(Conversation.id == _bg_conversation_id)
                                .values(
                                    total_tokens=_sa_func.coalesce(Conversation.total_tokens, 0)
                                    + combined_tokens,
                                    fast_llm_tokens=_sa_func.coalesce(
                                        Conversation.fast_llm_tokens, 0
                                    )
                                    + combined_tokens,
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

            spawn_background(_react_post_processing())
        except (
            InputGuardrailTripwireTriggered,
            OutputGuardrailTripwireTriggered,
        ) as exc:
            # Content guardrail aborted the turn.  Emit a structured
            # ``guardrail_tripwired`` event the frontend renders as a
            # "blocked" notice, then close the stream cleanly so the
            # response never falls through to a 500.
            logger.info(
                "Guardrail tripwired (%s) — aborting ReAct turn",
                type(exc).__name__,
            )
            payload = _build_guardrail_tripwired_payload(exc)
            yield _emit(sse_events, "guardrail_tripwired", payload)
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "done",
                {
                    "answer": payload["reason"],
                    "iterations": 0,
                    "elapsed": elapsed,
                    "guardrail_tripwired": True,
                },
            )
            yield _sse("end", {})
        except Exception as exc:
            logger.exception("ReAct agent failed")
            elapsed = round(time.time() - t0, 2)
            yield _emit(sse_events, "error", _run_failed_payload(exc, elapsed))
            yield _sse("end", {})
        finally:
            if _confirmation_pump_task is not None:
                _confirmation_pump_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await _confirmation_pump_task
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
    return await _dag_turn(request, body, None)


async def _dag_turn(
    request: Request,
    body: ChatStreamRequest,
    admitted: _AdmittedTurn | None,
) -> StreamingResponse:
    """A plan (DAG) turn. *admitted* is set when Auto mode already admitted it."""
    q = body.q
    conversation_id = body.conversation_id
    agent_id = body.agent_id
    token = body.token
    image_ids = body.image_ids
    dag_user_metadata_str = body.user_metadata
    dag_doc_mode = body.doc_mode

    # -- Pre-stream resolution ----------------------------------------------
    principal: Principal | None
    if admitted is not None:
        principal = admitted.principal
        user_system_instructions = admitted.system_instructions
        preferred_language = admitted.preferred_language
        user_timezone = admitted.timezone
    else:
        (
            principal,
            user_system_instructions,
            preferred_language,
            user_timezone,
        ) = await _resolve_user(token)
    if principal is None:
        raise AppError("authentication_required", status_code=401)
    current_user_id: str = principal.user_id

    # Bind the user id to the rate-limiter contextvar (see ReAct handler
    # above for the rationale).
    _rl_set_user(str(current_user_id))

    # Timezone: user profile → X-Timezone header → UTC fallback
    if not user_timezone:
        user_timezone = request.headers.get("X-Timezone") or None

    # Auto mode admitted the turn before routing it here; once is enough.
    agent_cfg = (
        admitted.agent_cfg
        if admitted is not None
        else await _admit_turn(principal, conversation_id, agent_id)
    )
    # The context this turn's resources are used and recorded under.
    turn_ctx = agent_runtime.call_context(
        principal, agent_cfg, conversation_id=conversation_id
    )

    from fim_one.db import create_session as _create_session

    try:
        async with _create_session() as _llm_db:
            (
                (llm, fast_llm),
                _fast_context_budget,
                _context_budget,
                dag_model_supports_vision,
            ) = await asyncio.gather(
                agent_runtime.resolve_models(agent_cfg, _llm_db),
                get_effective_fast_context_budget(_llm_db),
                get_effective_context_budget(_llm_db),
                _resolve_model_supports_vision(agent_cfg, _llm_db),
            )
            # Budget against the models this turn resolved, not the system
            # defaults: history and planning on the main model, steps and
            # compaction on the fast one.
            _budgets = await resolve_chat_context_budgets(
                llm,
                fast_llm,
                _llm_db,
                general_default=_context_budget,
                fast_default=_fast_context_budget,
            )
            _context_budget, _fast_context_budget = _budgets.main, _budgets.fast
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

    tools = await agent_runtime.resolve_toolset(turn_ctx, agent_cfg)
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

    # Inject workflow stubs — static recipes, the deterministic twin of skills
    _dag_workflow_ids = (
        await _resolve_user_workflow_ids(current_user_id) if current_user_id else None
    )
    if _dag_workflow_ids:
        _wf_block = await _resolve_workflow_stubs(_dag_workflow_ids)
        if _wf_block:
            extra_instructions = (extra_instructions or "") + _wf_block

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

    async def generate() -> AsyncGenerator[str, None]:
        t0 = time.time()
        sse_events: list[dict[str, Any]] = []

        # -- MCP connection (must happen inside generator for anyio cancel scope) --
        dag_user_mcp_client = await agent_runtime.connect_mcp(tools, agent_cfg)

        # -- Optional persistence setup ------------------------------------
        db_session = None
        if conversation_id:
            try:
                from fim_one.db import create_session
                from fim_one.db.models import Message as MessageModel

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
                            "mime_type": durl.split(";")[0].split(":", 1)[1] if ":" in durl else "application/octet-stream",
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
                                dag_all_images.append(
                                    {
                                        "file_id": _dag_fid,
                                        "filename": _dag_name,
                                        "mime_type": str(_dag_meta.get("mime_type", "")),
                                        "source": "document",
                                    }
                                )
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
                img_refs = ", ".join(
                    f"{fname} (file_id: {fid})" for fid, fname, _ in dag_image_data
                )
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
                    compact_input_budget=_fast_context_budget,
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
                        f"Previous conversation:\n{context_str}\n\nCurrent request: {q}"
                    )

                    # Truncate enriched_query if too large for planner.
                    from fim_one.core.memory.compact import CompactUtils
                    from fim_one.core.memory.context_guard import _COMPACT_PROMPTS

                    enriched_tokens = CompactUtils.estimate_tokens(enriched_query)
                    if enriched_tokens > 16_000:
                        # Summarised in parts when the history does not fit
                        # the fast model's window; None means it could not
                        # be summarised at all.
                        summary = (
                            await CompactUtils.summarize_history(
                                history,
                                fast_llm,
                                _COMPACT_PROMPTS["planner_input"],
                                input_budget=_fast_context_budget,
                                usage_tracker=fast_usage_tracker,
                            )
                            if fast_llm
                            else None
                        )
                        if summary:
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
                    "Failed to load conversation history for DAG planning (conversation %s)",
                    conversation_id,
                    exc_info=True,
                )

        progress_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        done_event = asyncio.Event()

        # Inline confirmation pump: the same entry as the ReAct handler,
        # so a chat with no agent is served under its conversation here too.
        _dag_confirmation_pump_task = _start_confirmation_pump(
            (agent_cfg or {}).get("agent_id"),
            conversation_id,
            current_user_id,
            progress_queue,
            sse_events,
        )

        # Step-level checkpoint: keyed by conversation + raw query hash so a
        # retry of the same request after a mid-execution crash resumes the
        # completed steps instead of redoing them (dev/incremental-dag.md).
        _dag_checkpoint = DAGCheckpoint()

        def on_step_progress(step_id: str, event: str, data: dict[str, Any]) -> None:
            # Persist step state on every completion so a crash loses at
            # most the in-flight steps.
            if event == "completed" and conversation_id and plan is not None:
                _dag_checkpoint.save(conversation_id, q, plan, round_num)
            # Convert raw artifact dicts (path-based) to download URLs.
            if conversation_id and "artifacts" in data and data["artifacts"]:
                data["artifacts"] = [
                    {
                        "name": a["name"],
                        "url": f"/api/conversations/{conversation_id}/artifacts/{a['path'].split('/')[-1].split('_', 1)[0]}",
                        "mime_type": a["mime_type"],
                        "size": a["size"],
                        "sha256": a.get("sha256", ""),
                    }
                    for a in data["artifacts"]
                ]
            step_payload = {"step_id": step_id, "event": event, **data}
            _append_event(sse_events, "step_progress", step_payload)
            try:
                progress_queue.put_nowait(_sse("step_progress", step_payload))
            except asyncio.QueueFull:
                logger.warning("SSE progress queue full, dropping event")

        def on_dag_thinking_delta(token: str, step_id: str) -> None:
            """Push thinking delta tokens as step_progress events."""
            payload = {
                "step_id": step_id,
                "event": "thinking_delta",
                "content": token,
            }
            # Don't persist deltas — they are transient streaming tokens.
            try:
                progress_queue.put_nowait(_sse("step_progress", payload))
            except asyncio.QueueFull:
                pass

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

            # -- Incremental replan carryover ---------------------------
            # Completed steps survive replan rounds (and crashes, via the
            # checkpoint) instead of being re-planned and re-executed.
            carryover_steps: list[PlanStep] = []
            if conversation_id:
                carryover_steps = _dag_checkpoint.load_completed_steps(
                    conversation_id, q,
                )
                if carryover_steps:
                    logger.info(
                        "Resuming DAG from checkpoint: %d completed step(s) carried",
                        len(carryover_steps),
                    )
                    yield _emit(
                        sse_events,
                        "phase",
                        {
                            "name": "checkpoint_resume",
                            "status": "done",
                            "carried_steps": [
                                {"id": s.id, "task": s.task} for s in carryover_steps
                            ],
                        },
                    )

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
                        _append_event(sse_events, "inject", inject_payload)
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
                    {"name": t.name, "description": t.description} for t in tools.list_tools()
                ]
                _domain_context = replan_context
                if _domain_hint:
                    _domain_guidance = (
                        f"[Domain: {_domain_hint}] This is a {_domain_hint}-domain task. "
                        f"For steps that involve analysis, synthesis, or report writing, "
                        f'set model_hint="reasoning" to ensure accuracy and reduce '
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
                    completed_steps=carryover_steps or None,
                )
                plan.current_round = round_num
                if conversation_id:
                    _dag_checkpoint.save(conversation_id, q, plan, round_num)
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
                                # "completed" marks carried-over steps the
                                # frontend should render as already done.
                                "status": s.status,
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
                    custom_compact_prompt=agent_cfg.get("compact_instructions")
                    if agent_cfg
                    else None,
                    compact_input_budget=_fast_context_budget,
                )

                # Inject fast usage tracker into grounded retrieve tool
                from fim_one.core.tool.builtin.grounded_retrieve import GroundedRetrieveTool

                for tool in tools._tools.values():
                    if isinstance(tool, GroundedRetrieveTool):
                        tool.set_usage_tracker(fast_usage_tracker)

                # --- Hook System bootstrap for DAG ---
                _hook_registry_dag = await agent_runtime.build_hooks(
                    agent_cfg, conversation_id=conversation_id
                )

                agent = ReActAgent(
                    llm=fast_llm,
                    tools=tools,
                    extra_instructions=extra_instructions,
                    max_iterations=dag_step_max_iters,
                    context_guard=dag_context_guard,
                    user_timezone=user_timezone,
                    agent_directive=agent_instructions,
                    hook_registry=_hook_registry_dag,
                    agent_id=(agent_cfg or {}).get("agent_id"),
                    org_id=(agent_cfg or {}).get("org_id"),
                    user_id=current_user_id,
                    # DAG steps are single focused sub-tasks — a plan board
                    # is noise there; planning lives at the DAG level.
                    enable_plan_tool=False,
                    # Same per-conversation workspace as the ReAct path so
                    # step tool outputs get offload/rescue instead of loss.
                    workspace=_conversation_workspace(conversation_id),
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
                    on_thinking_delta=on_dag_thinking_delta,
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
                    except TimeoutError:
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
                                _append_event(sse_events, "inject", inject_payload)
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
                        _append_event(sse_events, "inject", inject_payload)
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

                # -- Update carryover for the next round -------------------
                # Everything that completed (including previously carried
                # steps) survives the next replan instead of being redone.
                carryover_steps = [
                    s
                    for s in plan.steps
                    if s.status == "completed" and s.result is not None
                ]

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
                    if _should_stop_replanning(analysis, replan_stop_confidence):
                        logger.info(
                            "DAG round %d: goal judged unreachable with high "
                            "confidence (%.1f), stopping re-planning",
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

            dag_quota_exceeded_mid_stream = False
            dag_quota_terminator_payload: dict[str, Any] | None = None
            _dag_streamed_chars_since_check = 0

            if analysis.achieved:
                # Real streaming synthesis from LLM
                answer_chunks = []
                try:
                    async for _token in analyzer.stream_synthesize(
                        enriched_query,
                        plan,
                        analysis,
                    ):
                        answer_chunks.append(_token)
                        yield _emit(
                            sse_events,
                            "answer",
                            {"status": "delta", "content": _token},
                        )
                        # Mid-stream quota guard — see ReAct handler for
                        # the rationale and the 200-char throttle.
                        if current_user_id:
                            _dag_streamed_chars_since_check += len(_token)
                            if _dag_streamed_chars_since_check >= 200:
                                _dag_streamed_chars_since_check = 0
                                _dag_est_completion = (
                                    sum(len(c) for c in answer_chunks) // 4
                                )
                                _dag_quota_now = await quota_status(current_user_id)
                                _dag_used, _dag_cap = _dag_quota_now.used, _dag_quota_now.cap
                                if (
                                    _dag_cap > 0
                                    and _dag_used + _dag_est_completion >= _dag_cap
                                ):
                                    dag_quota_exceeded_mid_stream = True
                                    dag_quota_terminator_payload = (
                                        _build_quota_terminator_payload(
                                            monthly_tokens=(
                                                _dag_used + _dag_est_completion
                                            ),
                                            user_quota=_dag_cap,
                                            reset_at=(
                                                await quota_reset_at(current_user_id)
                                            ).isoformat(),
                                        )
                                    )
                                    logger.info(
                                        "Token quota exhausted mid-stream (DAG) for "
                                        "user=%s (used=%s, quota=%s) — emitting "
                                        "structured terminator",
                                        current_user_id,
                                        _dag_used + _dag_est_completion,
                                        _dag_cap,
                                    )
                                    break
                    answer = "".join(answer_chunks)
                except Exception:
                    logger.warning(
                        "stream_synthesize failed, falling back to analysis.final_answer",
                        exc_info=True,
                    )
                    answer = strip_tool_protocol(analysis.final_answer or "")
                    for _ans_chunk in _chunk_answer(answer):
                        yield _emit(
                            sse_events,
                            "answer",
                            {"status": "delta", "content": _ans_chunk},
                        )
            else:
                # Goal not achieved — use fallback answer
                completed = [s for s in plan.steps if s.status == "completed" and s.result]
                if completed:
                    answer = "\n\n---\n\n".join(f"**{s.id}**: {s.result}" for s in completed)
                else:
                    answer = "The task could not be completed — none of the planned steps produced a result. You can try rephrasing the goal or breaking it into simpler steps."
                for _ans_chunk in _chunk_answer(answer):
                    yield _emit(
                        sse_events,
                        "answer",
                        {"status": "delta", "content": _ans_chunk},
                    )

            yield _emit(sse_events, "answer", {"status": "done"})

            # Emit the structured ``error`` terminator BEFORE ``done`` when
            # the quota was exhausted mid-stream so the frontend dialog
            # picks up fresh counters.  See the ReAct handler for details.
            if (
                dag_quota_exceeded_mid_stream
                and dag_quota_terminator_payload is not None
            ):
                yield _emit(sse_events, "error", dag_quota_terminator_payload)

            # -- Classify deliverables from all artifacts --
            dag_all_artifacts: list[dict[str, Any]] = []
            for evt in sse_events:
                if evt["event"] == "step_progress":
                    sp_data = evt["data"]
                    if sp_data.get("artifacts"):
                        for a in sp_data["artifacts"]:
                            dag_all_artifacts.append(
                                {
                                    **a,
                                    "tool_name": sp_data.get("tool_name", ""),
                                    "step_id": sp_data.get("step_id", ""),
                                }
                            )

            dag_all_artifacts = _dedupe_artifacts(dag_all_artifacts)

            dag_deliverables: list[dict[str, Any]] = []
            if dag_all_artifacts:
                # Terminal steps = steps that no other step depends on (DAG sinks)
                all_dep_targets: set[str] = set()
                for s in plan.steps:
                    all_dep_targets.update(s.dependencies)
                terminal_step_ids = {s.id for s in plan.steps} - all_dep_targets

                # Artifacts from terminal steps are deliverables
                dag_deliverables = [
                    a for a in dag_all_artifacts if a.get("step_id") in terminal_step_ids
                ]
                # Fallback: if terminal steps produced no artifacts, use all
                if not dag_deliverables:
                    dag_deliverables = dag_all_artifacts
                # Let the fast LLM drop intermediates and near-duplicate
                # registrations of the same deliverable (e.g. a generic
                # 'rendered.html' next to the specifically named save) that
                # the exact-hash dedup above cannot catch.  Falls back to
                # the full list on any failure.
                if len(dag_deliverables) > 1 and fast_llm:
                    dag_deliverables = await _classify_deliverables(
                        fast_llm,
                        answer,
                        dag_deliverables,
                        usage_tracker=fast_usage_tracker,
                    )

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
                # Prompt-cache observability: surface Anthropic-style
                # cache counters to the client so it can eventually
                # render cost savings.  Always present (zeros when the
                # provider doesn't report caching) so the frontend can
                # treat the field as non-optional.
                dag_done_payload["cache"] = {
                    "read_tokens": cumulative_usage.cache_read_input_tokens,
                    "creation_tokens": (cumulative_usage.cache_creation_input_tokens),
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
                    _append_event(sse_events, "inject", inject_payload)
                    yield _sse("inject", inject_payload)
                if remaining:
                    dag_done_payload["pending_injections"] = [m.content for m in remaining]

            _dag_done_entry = _append_event(sse_events, "done", dag_done_payload)

            # -- Re-open a fresh DB session for persistence ----------------
            # The original session was closed right after saving the user
            # message to avoid holding a connection during DAG execution.
            if conversation_id:
                from fim_one.db import create_session

                db_session = create_session()

            # -- Persist assistant message BEFORE yielding done -----------
            if db_session and conversation_id:
                try:
                    from fim_one.db.models import (
                        Conversation as ConvModel,
                    )
                    from fim_one.db.models import (
                        Message as MessageModel,
                    )

                    # DAG plans don't hold ChatMessages directly, but when
                    # a future change surfaces the analyzer / last-step
                    # ChatMessage list here we will automatically pick up
                    # the thinking block via the same helper.
                    dag_thinking = _extract_final_thinking(
                        getattr(plan, "messages", None),
                    )
                    dag_metadata: dict[str, Any] = {
                        **dag_done_payload,
                        "sse_events": sse_events,
                        "mode": "dag",
                    }
                    if dag_thinking is not None:
                        dag_metadata["thinking"] = dag_thinking
                    assistant_msg = MessageModel(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        message_type="done",
                        metadata_=dag_metadata,
                    )
                    db_session.add(assistant_msg)
                    if "usage" in dag_done_payload:
                        stmt = sa_select(ConvModel).where(ConvModel.id == conversation_id)
                        conv = (await db_session.execute(stmt)).scalar_one_or_none()
                        if conv:
                            conv.total_tokens = (conv.total_tokens or 0) + dag_done_payload[
                                "usage"
                            ].get("total_tokens", 0)
                            if conv.model_name is None and llm.model_id:
                                conv.model_name = llm.model_id
                    await db_session.commit()
                except Exception:
                    logger.warning(
                        "Failed to persist assistant message for conversation %s",
                        conversation_id,
                        exc_info=True,
                    )

            # Turn finished and persisted — the checkpoint has served its
            # purpose; drop it so a later identical query starts fresh.
            if conversation_id:
                _dag_checkpoint.clear(conversation_id)

            # -- Yield done IMMEDIATELY (no suggestions/title yet) ------
            yield _sse(
                "done",
                {"cursor": _dag_done_entry["cursor"], "data": dag_done_payload},
            )

            # -- Fire per-agent completion notification (if configured) ----
            # See the ReAct handler above for the full rationale.  For
            # DAG, ``tools_used`` comes from the tools each step actually
            # called, recorded by the executor in ``result.data``.
            try:
                from fim_one.db import create_session as _notify_create_session
                from fim_one.web.notifications import notify_agent_completion

                _dag_tools_used = _collect_dag_tools_used(plan.steps)

                _dag_agent_notify_shim = SimpleNamespace(
                    id=(agent_cfg or {}).get("agent_id"),
                    name=(agent_cfg or {}).get("name") or "Agent",
                    org_id=(agent_cfg or {}).get("org_id"),
                    user_id=(agent_cfg or {}).get("owner_user_id"),
                    model_config_json=(agent_cfg or {}).get("model_config_json"),
                )
                spawn_background(
                    notify_agent_completion(
                        agent=_dag_agent_notify_shim,
                        conversation_id=conversation_id,
                        user_message=q,
                        final_answer=answer,
                        tools_used=_dag_tools_used,
                        duration_seconds=float(elapsed),
                        session_factory=_notify_create_session,
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to schedule DAG completion notification task",
                    exc_info=True,
                )

            # -- Unregister interrupt queue immediately after done ------
            # No agent loop is consuming injected messages anymore.
            # Unregistering causes inject API to return 404, signaling
            # the frontend to queue messages for the next turn instead.
            if dag_interrupt_queue is not None and conversation_id:
                await get_broker().unregister(conversation_id)
                dag_interrupt_queue = None  # prevent double-unregister in finally

            # -- Inline suggestions (no-agent always on; agents opt in) -----
            _dag_suggest_usage_tracker = UsageTracker()
            _dag_suggest_followups_on = _suggest_followups_enabled(agent_cfg)
            _dag_suggestions: list[str] = []
            if _dag_suggest_followups_on and (answer or "").strip():
                yield _emit(sse_events, "post_processing", {})
                try:
                    _dag_suggestions = await _generate_suggestions(
                        fast_llm,
                        q,
                        answer,
                        preferred_language=preferred_language,
                        usage_tracker=_dag_suggest_usage_tracker,
                    )
                except Exception:
                    logger.debug(
                        "Inline DAG suggestions generation failed", exc_info=True
                    )
                yield _emit(
                    sse_events, "suggestions", {"items": _dag_suggestions}
                )
                if conversation_id and _dag_suggestions:
                    try:
                        from fim_one.db import create_session as _persist_create_session
                        from fim_one.db.models import Message as _MsgModel

                        async with _persist_create_session() as _persist_db:
                            _last_msg = (
                                await _persist_db.execute(
                                    sa_select(_MsgModel)
                                    .where(
                                        _MsgModel.conversation_id == conversation_id,
                                        _MsgModel.role == "assistant",
                                    )
                                    .order_by(_MsgModel.created_at.desc())
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                            if _last_msg is not None:
                                _md = dict(_last_msg.metadata_ or {})
                                _md["suggestions"] = _dag_suggestions
                                _md["sse_events"] = sse_events
                                _last_msg.metadata_ = _md
                                await _persist_db.commit()
                    except Exception:
                        logger.debug(
                            "Failed to persist inline DAG suggestions",
                            exc_info=True,
                        )

            # -- Send end after optional suggestions ------------------------
            yield _sse("end", {})

            # -- Background: title generation + fast-LLM token accounting --
            _dag_bg_conversation_id = conversation_id
            _dag_bg_fast_llm = fast_llm
            _dag_bg_query = q
            _dag_bg_answer = answer
            _dag_bg_preferred_language = preferred_language
            _dag_bg_inline_summary = _dag_suggest_usage_tracker.get_summary()

            async def _dag_post_processing() -> None:
                """Background task for DAG title + fast-LLM token tracking."""
                from fim_one.db import create_session as _bg_create_session

                _bg_db: AsyncSession | None = None
                try:
                    _bg_usage_tracker = UsageTracker()

                    if not _dag_bg_conversation_id:
                        return

                    dag_gen_title: str | None = None
                    try:
                        from sqlalchemy import func as _sa_func

                        from fim_one.db.models import (
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
                            dag_gen_title = await _generate_title(
                                _dag_bg_fast_llm,
                                _dag_bg_query,
                                _dag_bg_answer,
                                preferred_language=_dag_bg_preferred_language,
                                usage_tracker=_bg_usage_tracker,
                            )
                    except Exception:
                        logger.debug("Auto-title generation failed", exc_info=True)

                    _bg_db = _bg_create_session()
                    if dag_gen_title:
                        try:
                            from sqlalchemy import update as _sa_update

                            from fim_one.db.models import Conversation as ConvModel

                            await _bg_db.execute(
                                _sa_update(ConvModel)
                                .where(ConvModel.id == _dag_bg_conversation_id)
                                .values(title=dag_gen_title)
                            )
                            await _bg_db.commit()
                        except Exception:
                            logger.debug("Failed to persist title", exc_info=True)

                    bg_fast_summary = _bg_usage_tracker.get_summary()
                    combined_tokens = (
                        bg_fast_summary.total_tokens
                        + _dag_bg_inline_summary.total_tokens
                    )
                    if combined_tokens > 0:
                        try:
                            from sqlalchemy import func as _sa_func
                            from sqlalchemy import update as _sa_update

                            from fim_one.db.models import Conversation as ConvModel

                            await _bg_db.execute(
                                _sa_update(ConvModel)
                                .where(ConvModel.id == _dag_bg_conversation_id)
                                .values(
                                    total_tokens=_sa_func.coalesce(ConvModel.total_tokens, 0)
                                    + combined_tokens,
                                    fast_llm_tokens=_sa_func.coalesce(ConvModel.fast_llm_tokens, 0)
                                    + combined_tokens,
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

            spawn_background(_dag_post_processing())
        except (
            InputGuardrailTripwireTriggered,
            OutputGuardrailTripwireTriggered,
        ) as exc:
            # Same handling as the ReAct branch; kept here so future DAG
            # integrations that wire guardrails into planner steps
            # surface a uniform SSE event.
            logger.info(
                "Guardrail tripwired (%s) — aborting DAG turn",
                type(exc).__name__,
            )
            payload = _build_guardrail_tripwired_payload(exc)
            yield _emit(sse_events, "guardrail_tripwired", payload)
            elapsed = round(time.time() - t0, 2)
            yield _sse(
                "done",
                {
                    "answer": payload["reason"],
                    "achieved": False,
                    "confidence": 0.0,
                    "elapsed": elapsed,
                    "guardrail_tripwired": True,
                },
            )
            yield _sse("end", {})
        except StructuredOutputError as exc:
            logger.warning(
                "Structured output failed for model %s: %s", getattr(llm, "model_id", "?"), exc
            )
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
            yield _emit(sse_events, "error", _run_failed_payload(exc, elapsed))
            yield _sse("end", {})
        finally:
            if _dag_confirmation_pump_task is not None:
                _dag_confirmation_pump_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await _dag_confirmation_pump_task
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
    principal, system_instructions, preferred_language, user_timezone = await _resolve_user(
        token
    )
    if principal is None:
        raise AppError("authentication_required", status_code=401)

    # The admission of the endpoint this turn is routed to, before the
    # classification below spends a model call: the conversation is the
    # caller's, plan and quota allow the turn, the agent is usable. The
    # routed endpoint is handed the result and does not admit again.
    agent_cfg = await _admit_turn(principal, body.conversation_id, body.agent_id)
    admitted = _AdmittedTurn(
        principal=principal,
        system_instructions=system_instructions,
        preferred_language=preferred_language,
        timezone=user_timezone,
        agent_cfg=agent_cfg,
    )

    # Determine execution mode
    from fim_one.core.planner.router import classify_execution_mode
    from fim_one.db import create_session as _create_session

    async with _create_session() as _llm_db:
        fast_llm = await _resolve_fast_llm(agent_cfg, _llm_db)

    decision = await classify_execution_mode(q, fast_llm)
    mode = decision.mode
    reasoning = decision.reasoning

    # Update conversation mode to the resolved value so exports show
    # the correct label instead of "auto".
    if body.conversation_id and mode != "auto":
        from sqlalchemy import update as _sa_update_mode
        from fim_one.db.models import Conversation as _ConvModelAuto

        async with _create_session() as _mode_db:
            await _mode_db.execute(
                _sa_update_mode(_ConvModelAuto)
                .where(_ConvModelAuto.id == body.conversation_id)
                .values(mode=mode)
            )
            await _mode_db.commit()

    # Domain detection is handled independently inside each endpoint.

    # Wrap the inner endpoint's StreamingResponse to prepend the routing event
    if mode == "dag":
        inner_response = await _dag_turn(request, body, admitted)
    else:
        inner_response = await _react_turn(request, body, admitted)

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
