"""Per-agent task-completion notifications over the :mod:`fim_one.core.channels`
abstraction.

When an agent (ReAct or DAG) finishes a conversation turn with a
``final_answer``, if the agent has a completion-notification channel
configured in ``model_config_json.notifications.on_complete``, post a
summary to that channel.  Rendering is delegated to the channel subclass
(:meth:`BaseChannel.send_completion`) so this module stays
channel-agnostic — adding a DingTalk or Slack channel requires zero
changes here.

This module is **distinct from** the Hook System
(:mod:`fim_one.web.hooks_bootstrap`):

- Hooks are per-tool-call enforcement points (PreToolUse / PostToolUse)
  that can block a run.
- Completion notifications are per-run, one-shot, fire-and-forget side
  effects that MUST NEVER delay or fail the user-facing chat response.

The config shape on the agent is::

    {
      "notifications": {
        "on_complete": {
          "enabled": true,
          "channel_id": "<channel-uuid>"
        }
      }
    }

Usage at the trigger site::

    asyncio.create_task(
        notify_agent_completion(
            agent=agent_shim,
            conversation_id=conversation_id,
            user_message=q,
            final_answer=answer,
            tools_used=list(tools_used_in_run),
            duration_seconds=time.time() - t0,
            session_factory=create_session,
        )
    )

Every failure path logs a warning and returns — ``notify_agent_completion``
NEVER raises.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select as sa_select

from fim_one.core.channels import CompletionSummary, build_channel


__all__ = [
    "SessionFactory",
    "notify_agent_completion",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@runtime_checkable
class _AgentLike(Protocol):
    """Structural subset of :class:`fim_one.db.models.agent.Agent`.

    The real ORM row works; so does a
    :class:`types.SimpleNamespace` wrapper used in the web layer when the
    code already holds a lightweight dict.  Minimum fields we read:

    - ``org_id`` — used to prevent cross-org channel targeting
    - ``id`` — logging / correlation only (may be ``None``)
    - ``name`` — shown in the notification card header
    - ``model_config_json`` — dict (or falsy) holding
      ``notifications.on_complete``
    """

    org_id: Any  # pragma: no cover - protocol attribute
    id: Any  # pragma: no cover - protocol attribute
    name: Any  # pragma: no cover - protocol attribute
    model_config_json: Any  # pragma: no cover - protocol attribute


SessionFactory = Callable[[], Any]
"""Zero-arg callable returning a fresh ``AsyncSession`` we own.

The function opens its own short-lived session via this factory so it
doesn't piggy-back on the web request's session (which will already be
closed by the time the background notification fires).
"""


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def _parse_on_complete_config(
    model_config_json: Any,
) -> dict[str, Any] | None:
    """Return the ``on_complete`` block if notifications are enabled.

    Returns ``None`` for any no-op condition (config missing, disabled,
    malformed).  Never raises.
    """
    if not model_config_json or not isinstance(model_config_json, dict):
        return None

    notifications = model_config_json.get("notifications")
    if not isinstance(notifications, dict):
        return None

    on_complete = notifications.get("on_complete")
    if not isinstance(on_complete, dict):
        return None

    if not on_complete.get("enabled"):
        return None

    return on_complete


def _portal_conversation_url(conversation_id: str) -> str | None:
    """Build a clickable link back to the portal, if a base URL is set."""
    base = os.environ.get("FRONTEND_URL") or os.environ.get("BACKEND_URL")
    if not base:
        return None
    base = base.rstrip("/")
    return f"{base}/?c={conversation_id}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def notify_agent_completion(
    *,
    agent: _AgentLike,
    conversation_id: str | None,
    user_message: str,
    final_answer: str,
    tools_used: list[str],
    duration_seconds: float,
    session_factory: SessionFactory,
) -> None:
    """Fire-and-forget completion notification.

    If the agent is configured for completion notifications, load the
    target :class:`fim_one.db.models.channel.Channel`, instantiate its
    adapter, and hand off a :class:`CompletionSummary` to
    :meth:`BaseChannel.send_completion` — the adapter decides how to
    render it (Feishu card, Slack message, DingTalk markdown, ...).

    This function NEVER raises — every failure path logs a warning and
    returns, because a notification failure must not break the
    user-facing chat response.

    Args:
        agent: Object exposing ``org_id``, ``id``, ``name``,
            ``model_config_json``.  The ORM row or a ``SimpleNamespace``
            shim both work.
        conversation_id: ID of the conversation being notified about
            (used in the card link back to the portal).  May be ``None``.
        user_message: The triggering user message — raw, the channel
            decides truncation.
        final_answer: The agent's final answer — raw, the channel
            decides truncation.
        tools_used: Names of tools called during the run — raw, the
            channel decides dedup / truncation.
        duration_seconds: Wall-clock duration of the agent run.
        session_factory: Zero-arg callable returning a fresh
            ``AsyncSession``.  The function uses it in an ``async with``
            block — DO NOT pass the request-scoped session.
    """
    try:
        on_complete = _parse_on_complete_config(
            getattr(agent, "model_config_json", None)
        )
        if on_complete is None:
            return

        channel_id = on_complete.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id:
            logger.warning(
                "Agent %r has on_complete.enabled=true but no channel_id; "
                "skipping completion notification",
                getattr(agent, "id", None),
            )
            return

        agent_org_id = getattr(agent, "org_id", None)

        # Fetch the channel in its own short-lived session.
        from fim_one.db.models.channel import Channel

        async with session_factory() as db:
            stmt = sa_select(Channel).where(Channel.id == channel_id)
            result = await db.execute(stmt)
            channel_row = result.scalar_one_or_none()

        if channel_row is None:
            logger.warning(
                "Completion notification channel %r not found (agent=%r); "
                "skipping",
                channel_id,
                getattr(agent, "id", None),
            )
            return

        if not channel_row.is_active:
            logger.warning(
                "Completion notification channel %r is inactive (agent=%r); "
                "skipping",
                channel_id,
                getattr(agent, "id", None),
            )
            return

        if agent_org_id and channel_row.org_id != agent_org_id:
            logger.warning(
                "Channel %r org_id=%r does not match agent org_id=%r; "
                "skipping completion notification",
                channel_id,
                channel_row.org_id,
                agent_org_id,
            )
            return

        config = channel_row.config if isinstance(channel_row.config, dict) else {}
        channel = build_channel(channel_row.type, config)
        if channel is None:
            logger.warning(
                "No channel adapter registered for type=%r (channel=%r); "
                "skipping completion notification",
                channel_row.type,
                channel_id,
            )
            return

        summary = CompletionSummary(
            agent_name=getattr(agent, "name", None) or "Agent",
            duration_seconds=duration_seconds,
            tools_used=list(tools_used or []),
            user_message=user_message or "",
            final_answer=final_answer or "",
            conversation_id=conversation_id,
            conversation_url=(
                _portal_conversation_url(conversation_id)
                if conversation_id
                else None
            ),
        )

        send_result = await channel.send_completion(summary)
        if not send_result.ok:
            logger.warning(
                "Completion notification send failed for channel=%r: %s",
                channel_id,
                send_result.error,
            )
            return

        logger.info(
            "Posted completion notification to channel=%r (agent=%r, "
            "conversation=%r)",
            channel_id,
            getattr(agent, "id", None),
            conversation_id,
        )
    except Exception:  # pragma: no cover - defensive outer shield
        logger.exception(
            "notify_agent_completion crashed; swallowing to protect chat "
            "response (agent=%r, conversation=%r)",
            getattr(agent, "id", None),
            conversation_id,
        )
        return
