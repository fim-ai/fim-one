"""FeishuGateHook — pre-tool-use human-in-the-loop approval gate.

Despite the legacy name, this hook is the **unified confirmation router**
for both inline (in-portal) and channel (Feishu) delivery modes.  It
reads routing preferences off the agent ORM row (``confirmation_mode``,
``approval_channel_id``, ``require_confirmation_for_all``) and dispatches
accordingly:

* ``channel_only``  — always push a Feishu card; fail closed if no
  Feishu channel is resolvable.
* ``inline_only``   — never touch a messaging channel; always create an
  ``mode="inline"`` ``ConfirmationRequest`` and wait for the frontend
  to POST ``/api/confirmations/{id}/respond``.
* ``auto`` (default) — prefer the channel if one is resolvable, else
  fall back to inline.

The class name is retained for backward compatibility with existing
registrations (``feishu_gate``), tests, and ``model_config_json`` values
in production agent rows.  A rename would churn every downstream config
without changing behaviour.
"""

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
from .inline_confirmation import emit_inline_confirmation

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
    """Block a tool call until an operator confirms it.

    Routing (per-agent):

    1. Load agent row by ``context.agent_id``.
    2. ``requires = action.requires_confirmation OR agent.require_confirmation_for_all``.
    3. If not ``requires`` → allow.
    4. Resolve ``mode`` from ``agent.confirmation_mode``:

       * ``inline_only`` → inline
       * ``channel_only`` → channel (fail closed if no Feishu channel)
       * ``auto`` → channel if resolvable, else inline

    5. Channel path: send an interactive Feishu card, poll DB.
    6. Inline path: create ``ConfirmationRequest(mode="inline")``, fire
       the SSE-bound listener, poll DB.
    """

    name = "feishu_gate"
    description = (
        "Before running a tool flagged requires_confirmation, routes the "
        "approval request to either an in-portal inline card or the org's "
        "Feishu channel (per the agent's confirmation_mode) and blocks "
        "until a human responds."
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
        # --- Step 1: load the agent row ----------------------------------
        if not context.agent_id:
            return HookResult(
                allow=False,
                error=(
                    "Tool requires confirmation but no agent_id was provided "
                    "in the hook context — cannot resolve confirmation mode."
                ),
                side_effects=["feishu_gate: no agent context"],
            )

        async with self._session_factory() as session:
            agent_row = await self._load_agent(session, context.agent_id)
        if agent_row is None:
            return HookResult(
                allow=False,
                error=(
                    f"Tool requires confirmation but agent "
                    f"{context.agent_id!r} was not found."
                ),
                side_effects=["feishu_gate: agent not found"],
            )

        # --- Step 2: effective requirement -------------------------------
        action_requires = False
        try:
            action_requires = await self._requires_confirmation_fn(context)
        except Exception:  # pragma: no cover - defensive
            logger.exception("requires_confirmation_fn raised — skipping gate")
            return HookResult()

        require_all = bool(getattr(agent_row, "require_confirmation_for_all", False))
        requires = action_requires or require_all

        if not requires:
            return HookResult()

        # --- Step 3: resolve org_id (needed for both modes) --------------
        org_id: str | None = None
        if context.metadata:
            raw_org = context.metadata.get("org_id")
            if isinstance(raw_org, str) and raw_org:
                org_id = raw_org
        if org_id is None:
            org_id = getattr(agent_row, "org_id", None)
        # Personal agents have no org — they can still go inline.
        # Channel mode will fail closed below.

        # --- Step 4: resolve mode ----------------------------------------
        raw_mode = str(getattr(agent_row, "confirmation_mode", "auto") or "auto")
        mode = raw_mode.strip().lower() or "auto"

        channel_row: Any = None
        async with self._session_factory() as session:
            channel_row = await self._resolve_channel(session, agent_row, org_id)

        # For now we only support Feishu cards.  If the resolved channel is
        # a different type, treat the resolution as a miss (graceful
        # degradation) — channel_only will still fail closed.
        feishu_channel_row = (
            channel_row if self._is_feishu_channel(channel_row) else None
        )
        if channel_row is not None and feishu_channel_row is None:
            logger.warning(
                "feishu_gate: resolved channel %r has type=%r; only 'feishu' "
                "is supported for v1 approval cards — falling back to inline.",
                getattr(channel_row, "id", None),
                getattr(channel_row, "type", None),
            )

        if mode == "channel_only":
            if feishu_channel_row is None:
                return HookResult(
                    allow=False,
                    error=(
                        "Agent is configured for channel_only confirmation "
                        "but no Feishu channel is bound or active."
                    ),
                    side_effects=["feishu_gate: channel_only with no channel"],
                )
            return await self._run_channel_flow(
                context=context,
                agent_row=agent_row,
                org_id=org_id or "",
                channel_row=feishu_channel_row,
            )

        if mode == "inline_only":
            return await self._run_inline_flow(
                context=context,
                agent_row=agent_row,
                org_id=org_id,
            )

        # mode == "auto" (or any unknown value — default to auto semantics)
        if feishu_channel_row is not None:
            return await self._run_channel_flow(
                context=context,
                agent_row=agent_row,
                org_id=org_id or "",
                channel_row=feishu_channel_row,
            )
        return await self._run_inline_flow(
            context=context,
            agent_row=agent_row,
            org_id=org_id,
        )

    # ------------------------------------------------------------------
    # Channel path
    # ------------------------------------------------------------------

    async def _run_channel_flow(
        self,
        *,
        context: HookContext,
        agent_row: Any,
        org_id: str,
        channel_row: Any,
    ) -> HookResult:
        channel = build_channel(channel_row.type, dict(channel_row.config))
        if channel is None or not isinstance(channel, FeishuChannel):
            return HookResult(
                allow=False,
                error=f"Unsupported channel type: {channel_row.type}",
                side_effects=["feishu_gate: unknown channel type"],
            )

        chat_id = str(channel_row.config.get("chat_id") or "").strip()
        if not chat_id:
            return HookResult(
                allow=False,
                error="Feishu channel has no chat_id configured.",
                side_effects=["feishu_gate: channel chat_id missing"],
            )

        confirmation_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            await self._create_confirmation_row(
                session,
                confirmation_id=confirmation_id,
                context=context,
                agent_row=agent_row,
                org_id=org_id,
                channel_id=channel_row.id,
                mode="channel",
            )

        card = self._build_card(
            confirmation_id=confirmation_id, context=context
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

        decision = await self._await_decision(confirmation_id)
        return self._result_for_decision(confirmation_id, decision, mode="channel")

    # ------------------------------------------------------------------
    # Inline path
    # ------------------------------------------------------------------

    async def _run_inline_flow(
        self,
        *,
        context: HookContext,
        agent_row: Any,
        org_id: str | None,
    ) -> HookResult:
        confirmation_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            row = await self._create_confirmation_row(
                session,
                confirmation_id=confirmation_id,
                context=context,
                agent_row=agent_row,
                org_id=org_id,
                channel_id=None,
                mode="inline",
            )

        # Fire the SSE-bound listener (best-effort — never raises).
        await emit_inline_confirmation(row)

        decision = await self._await_decision(confirmation_id)
        return self._result_for_decision(confirmation_id, decision, mode="inline")

    def _result_for_decision(
        self, confirmation_id: str, decision: str | None, *, mode: str
    ) -> HookResult:
        if decision == "approve":
            return HookResult(
                allow=True,
                side_effects=[
                    f"feishu_gate: approved ({mode}, id={confirmation_id})"
                ],
            )
        if decision == "reject":
            return HookResult(
                allow=False,
                error="Tool call was rejected by an operator.",
                side_effects=[
                    f"feishu_gate: rejected ({mode}, id={confirmation_id})"
                ],
            )
        # expired / timeout
        # Fire-and-forget; no await-blocking needed here since caller returns.
        return HookResult(
            allow=False,
            error=(
                f"Tool call timed out waiting for confirmation "
                f"after {self._timeout_seconds}s."
            ),
            side_effects=[
                f"feishu_gate: expired ({mode}, id={confirmation_id})"
            ],
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _load_agent(
        self, session: AsyncSession, agent_id: str
    ) -> Any:
        from fim_one.web.models.agent import Agent

        stmt = select(Agent).where(Agent.id == agent_id).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_channel(
        self,
        session: AsyncSession,
        agent_row: Any,
        org_id: str | None,
    ) -> Any:
        """Resolve the channel to use for a channel-mode confirmation.

        Resolution order:

        1. ``agent.approval_channel_id`` (if active).
        2. ``agent.model_config_json.on_complete.channel_id`` (completion
           notification channel, if active) — reuses the notification
           channel when no dedicated approval channel is bound.
        3. First active channel in the same org (``ORDER BY created_at``).

        Returns ``None`` if nothing resolvable.
        """
        from fim_one.web.models.channel import Channel

        # 1. Explicit approval channel.
        approval_id = getattr(agent_row, "approval_channel_id", None)
        if approval_id:
            stmt = (
                select(Channel)
                .where(Channel.id == approval_id, Channel.is_active.is_(True))
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row

        # 2. Completion-notification channel (from model_config_json).
        completion_id = self._completion_channel_id(agent_row)
        if completion_id:
            stmt = (
                select(Channel)
                .where(Channel.id == completion_id, Channel.is_active.is_(True))
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row

        # 3. First active channel in org.
        if org_id:
            stmt = (
                select(Channel)
                .where(
                    Channel.org_id == org_id,
                    Channel.is_active.is_(True),
                )
                .order_by(Channel.created_at)
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return row

        return None

    @staticmethod
    def _completion_channel_id(agent_row: Any) -> str | None:
        """Dig the optional completion-notification channel id out of
        ``model_config_json.on_complete.channel_id``."""
        cfg = getattr(agent_row, "model_config_json", None)
        if not isinstance(cfg, dict):
            return None
        on_complete = cfg.get("on_complete")
        if not isinstance(on_complete, dict):
            return None
        if not on_complete.get("enabled"):
            return None
        cid = on_complete.get("channel_id")
        if isinstance(cid, str) and cid:
            return cid
        return None

    @staticmethod
    def _is_feishu_channel(channel_row: Any) -> bool:
        if channel_row is None:
            return False
        return str(getattr(channel_row, "type", "")).lower() == "feishu"

    async def _create_confirmation_row(
        self,
        session: AsyncSession,
        *,
        confirmation_id: str,
        context: HookContext,
        agent_row: Any,
        org_id: str | None,
        channel_id: str | None,
        mode: str,
    ) -> Any:
        from fim_one.web.models.channel import ConfirmationRequest

        payload: dict[str, Any] = {
            "tool_name": context.tool_name,
            "tool_args": context.tool_args or {},
        }
        # Approver: when the agent is scoped to "initiator" we stamp the
        # invoking user; otherwise leave NULL so the callback endpoint can
        # apply the broader eligibility rules.
        scope = str(
            getattr(agent_row, "confirmation_approver_scope", "initiator")
            or "initiator"
        ).lower()
        approver_user_id: str | None = None
        if scope == "initiator":
            approver_user_id = context.user_id
        elif scope == "agent_owner":
            approver_user_id = getattr(agent_row, "user_id", None)

        # ``ConfirmationRequest.org_id`` is NOT NULL in the schema; fall
        # back to an empty string is NOT acceptable in prod, but for
        # personal-agent inline flows the migration keeps the column
        # nullable-at-ORM level via the existing default.  If org_id is
        # truly missing, use the agent's user_id namespace as a safe
        # sentinel — callers loading the row can still dispatch by
        # ``user_id``.
        effective_org = org_id or getattr(agent_row, "org_id", None) or ""

        row = ConfirmationRequest(
            id=confirmation_id,
            tool_call_id=(context.metadata or {}).get("tool_call_id")
            if context.metadata
            else None,
            agent_id=context.agent_id,
            user_id=context.user_id,
            approver_user_id=approver_user_id,
            org_id=effective_org,
            channel_id=channel_id,
            mode=mode,
            status="pending",
            payload=payload,
        )
        session.add(row)
        await session.commit()
        # Re-fetch to ensure relationships / defaults are hydrated before
        # we hand the row to an external listener.
        await session.refresh(row)
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
                await self._mark_expired(confirmation_id)
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
