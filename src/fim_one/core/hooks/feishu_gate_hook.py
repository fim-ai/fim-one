"""FeishuGateHook — pre-tool-use human-in-the-loop via Feishu card."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.agent.hooks import HookContext, HookResult
from fim_one.core.channels import build_channel
from fim_one.core.channels.feishu import FeishuChannel, build_confirmation_card

from .base import PreToolUseHook

logger = logging.getLogger(__name__)


# Type aliases for the injection seams.
SessionFactory = Callable[[], AsyncSession]
# A callable that takes a context and returns True if the pending tool call
# requires confirmation.  Default impl inspects ``context.metadata``.
RequiresConfirmationFn = Callable[[HookContext], Awaitable[bool]]


DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 1.5


async def _default_requires_confirmation(context: HookContext) -> bool:
    """Default predicate: honor ``context.metadata['requires_confirmation']``.

    The DAG executor / ReAct loop populates ``metadata`` from the connector
    action row's ``requires_confirmation`` flag before invoking the hook.
    """
    meta = context.metadata or {}
    return bool(meta.get("requires_confirmation"))


class FeishuGateHook(PreToolUseHook):
    """Block a tool call until an operator confirms it in Feishu.

    Workflow::

        tool call incoming
          │
          ▼
        (1) should_trigger?   ◄── requires_confirmation flag
          │ yes
          ▼
        (2) create ConfirmationRequest(status=pending) row
          │
          ▼
        (3) send interactive card to org Feishu Channel (group chat)
          │
          ▼
        (4) poll DB row every ~1.5s until approved/rejected/expired
          │
          ▼
        allow tool call (approve) or block with HookResult(allow=False)
    """

    name = "feishu_gate"
    description = (
        "Before running a tool flagged requires_confirmation, posts an "
        "interactive Approve/Reject card to the org's Feishu channel and "
        "blocks until a chat member responds."
    )
    priority = 10  # Run early — rate limiters / loggers can come after.

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        requires_confirmation_fn: RequiresConfirmationFn | None = None,
        callback_base_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._requires_confirmation_fn = (
            requires_confirmation_fn or _default_requires_confirmation
        )
        # Optional: the public URL of the FIM One backend.  Included in the
        # card summary so operators can jump back to the portal if needed.
        self._callback_base_url = (
            callback_base_url
            or os.getenv("BACKEND_URL")
            or os.getenv("FRONTEND_URL")
            or ""
        ).rstrip("/")

    # ------------------------------------------------------------------
    # Should-trigger / Execute
    # ------------------------------------------------------------------

    def should_trigger(self, context: HookContext) -> bool:
        """Sync wrapper: the async predicate is awaited in ``execute``."""
        return True  # defer the real decision to execute() for async access

    async def execute(self, context: HookContext) -> HookResult:
        requires = False
        try:
            requires = await self._requires_confirmation_fn(context)
        except Exception:  # pragma: no cover - defensive
            logger.exception("requires_confirmation_fn raised — skipping gate")
            return HookResult()

        if not requires:
            return HookResult()

        org_id = (context.metadata or {}).get("org_id") if context.metadata else None
        if not org_id:
            return HookResult(
                allow=False,
                error=(
                    "Tool requires confirmation but no org_id was provided in "
                    "the hook context — cannot locate a Feishu channel."
                ),
                side_effects=["feishu_gate: missing org_id"],
            )

        async with self._session_factory() as session:
            channel_row = await self._load_active_feishu_channel(session, org_id)
            if channel_row is None:
                return HookResult(
                    allow=False,
                    error=(
                        "Tool requires confirmation but the organization has "
                        "no active Feishu channel configured."
                    ),
                    side_effects=["feishu_gate: no active Feishu channel"],
                )

            channel = build_channel(channel_row.type, dict(channel_row.config))
            if channel is None or not isinstance(channel, FeishuChannel):
                return HookResult(
                    allow=False,
                    error=f"Unsupported channel type: {channel_row.type}",
                    side_effects=["feishu_gate: unknown channel type"],
                )

            confirmation_id = str(uuid.uuid4())
            request = await self._create_confirmation_row(
                session,
                confirmation_id=confirmation_id,
                context=context,
                org_id=str(org_id),
                channel_id=channel_row.id,
            )

            # Send the card — any group member can approve.
            chat_id = str(channel_row.config.get("chat_id") or "").strip()
            if not chat_id:
                return HookResult(
                    allow=False,
                    error="Feishu channel has no chat_id configured.",
                    side_effects=["feishu_gate: channel chat_id missing"],
                )

            card = self._build_card(
                confirmation_id=confirmation_id,
                context=context,
            )
            send_result = await channel.send_interactive_card(chat_id, card)
            if not send_result.ok:
                return HookResult(
                    allow=False,
                    error=(
                        "Failed to deliver Feishu confirmation card: "
                        f"{send_result.error}"
                    ),
                    side_effects=[
                        f"feishu_gate: send failed — {send_result.error}"
                    ],
                )

        # Poll for the decision with a fresh session (so we see commits
        # from the callback endpoint).
        decision = await self._await_decision(confirmation_id)

        if decision == "approve":
            return HookResult(
                allow=True,
                side_effects=[
                    f"feishu_gate: approved (id={confirmation_id})"
                ],
            )
        if decision == "reject":
            return HookResult(
                allow=False,
                error="Tool call was rejected by a Feishu operator.",
                side_effects=[
                    f"feishu_gate: rejected (id={confirmation_id})"
                ],
            )
        # expired / timeout
        await self._mark_expired(confirmation_id)
        return HookResult(
            allow=False,
            error=(
                f"Tool call timed out waiting for Feishu confirmation "
                f"after {self._timeout_seconds}s."
            ),
            side_effects=[f"feishu_gate: expired (id={confirmation_id})"],
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _load_active_feishu_channel(
        self, session: AsyncSession, org_id: str
    ) -> Any:
        from fim_one.web.models.channel import Channel

        stmt = (
            select(Channel)
            .where(
                Channel.org_id == org_id,
                Channel.type == "feishu",
                Channel.is_active.is_(True),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _create_confirmation_row(
        self,
        session: AsyncSession,
        *,
        confirmation_id: str,
        context: HookContext,
        org_id: str,
        channel_id: str,
    ) -> Any:
        from fim_one.web.models.channel import ConfirmationRequest

        payload: dict[str, Any] = {
            "tool_name": context.tool_name,
            "tool_args": context.tool_args or {},
        }
        row = ConfirmationRequest(
            id=confirmation_id,
            tool_call_id=(context.metadata or {}).get("tool_call_id")
            if context.metadata
            else None,
            agent_id=context.agent_id,
            user_id=context.user_id,
            org_id=org_id,
            channel_id=channel_id,
            status="pending",
            payload=payload,
        )
        session.add(row)
        await session.commit()
        return row

    async def _await_decision(self, confirmation_id: str) -> str | None:
        """Poll the ``confirmation_requests`` row until terminal."""
        from fim_one.web.models.channel import ConfirmationRequest

        deadline = asyncio.get_event_loop().time() + self._timeout_seconds
        while True:
            async with self._session_factory() as session:
                stmt = select(ConfirmationRequest).where(
                    ConfirmationRequest.id == confirmation_id
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row is not None and row.status in ("approved", "approve"):
                    return "approve"
                if row is not None and row.status in ("rejected", "reject"):
                    return "reject"

            if asyncio.get_event_loop().time() >= deadline:
                return None
            await asyncio.sleep(self._poll_interval_seconds)

    async def _mark_expired(self, confirmation_id: str) -> None:
        from fim_one.web.models.channel import ConfirmationRequest

        try:
            async with self._session_factory() as session:
                stmt = select(ConfirmationRequest).where(
                    ConfirmationRequest.id == confirmation_id
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row is not None and row.status == "pending":
                    row.status = "expired"
                    row.responded_at = datetime.utcnow()
                    await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to mark confirmation %s expired", confirmation_id
            )

    # ------------------------------------------------------------------
    # Card builder
    # ------------------------------------------------------------------

    def _build_card(
        self,
        *,
        confirmation_id: str,
        context: HookContext,
    ) -> dict[str, Any]:
        tool_name = context.tool_name or "unknown"
        args = context.tool_args or {}
        try:
            preview = json.dumps(args, ensure_ascii=False, indent=2)
        except Exception:
            preview = str(args)
        summary_lines = [
            "**FIM One is requesting approval to run a sensitive tool.**",
            "",
            "Approve only if you expect this action right now.",
        ]
        if self._callback_base_url:
            summary_lines.append(
                f"\nPortal: {self._callback_base_url}"
            )
        return build_confirmation_card(
            confirmation_id=confirmation_id,
            title="FIM One — Approval Required",
            summary="\n".join(summary_lines),
            tool_name=tool_name,
            tool_args_preview=preview,
        )


def create_feishu_gate_hook(
    *,
    session_factory: SessionFactory,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    requires_confirmation_fn: RequiresConfirmationFn | None = None,
    callback_base_url: str | None = None,
) -> FeishuGateHook:
    """Factory — returns a configured :class:`FeishuGateHook` instance."""
    return FeishuGateHook(
        session_factory=session_factory,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        requires_confirmation_fn=requires_confirmation_fn,
        callback_base_url=callback_base_url,
    )


__all__ = [
    "FeishuGateHook",
    "create_feishu_gate_hook",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "SessionFactory",
    "RequiresConfirmationFn",
    "_default_requires_confirmation",
]
