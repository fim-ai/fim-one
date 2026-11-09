"""Asking a human before something runs.

One service creates every request a person has to answer (a tool call that
requires confirmation, a question the agent asks its user), delivers it
either inline in the portal or to a messaging channel, and waits for the
answer. :class:`ApprovalGateHook` is the pre-tool-use hook that calls it for
tool calls.

Delivery, per request (:attr:`ConfirmationSpec.delivery`):

* ``inline_only``: never touches a messaging channel. The request is handed
  to the inline listener the web layer registers
  (``core/hooks/inline_confirmation.py``), which surfaces it in the chat.
* ``channel_only``: posted to the bound channel; refused when no usable
  channel is bound.
* ``auto``: the channel when one is bound and usable, otherwise inline.

A channel is usable when it implements
:class:`~fim_one.core.channels.ApprovalCarrier`; the service never asks
which platform it is. Only the explicitly bound channel is considered: an
approval never borrows the completion-notification channel or some other
channel of the organization.

Who may decide is fixed when the request is created and written on the row
(``eligible_scope``, ``eligible_user_id``, ``org_id``); changing the agent's
settings afterwards does not move a request in flight. :meth:`may_decide`
reads the row and nothing else, for the portal and for a channel callback
alike. Rows written before the columns existed have no scope and fall back
to the agent's current setting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fim_one.core.agent.hooks import HookContext, HookResult
from fim_one.core.channels import ApprovalCarrier, ApprovalRequestSummary, build_channel
from fim_one.core.channels.resolve import resolve_org_channel
from fim_one.core.hooks.base import PreToolUseHook
from fim_one.core.hooks.inline_confirmation import emit_inline_confirmation

from .errors import (
    CONFIRMATION_ALREADY_DECIDED,
    CONFIRMATION_FORBIDDEN,
    CONFIRMATION_NOT_FOUND,
    CONFIRMATION_UNDELIVERABLE,
    AssemblyRefusal,
)
from .principal import Binding, Principal, provisional_principal

__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ApprovalGateHook",
    "ConfirmationService",
    "ConfirmationSpec",
    "Decision",
    "RequiresConfirmationFn",
    "SessionFactory",
    "confirmation_required",
    "create_approval_gate_hook",
    "record_decision",
]

logger = logging.getLogger(__name__)

# A zero-argument callable returning a fresh session usable as an async
# context manager (``fim_one.db.create_session``). A request is created and
# polled outside any HTTP request's session, which closes when it returns.
SessionFactory = Callable[[], Any]
# Whether the pending tool call is flagged as requiring confirmation.
RequiresConfirmationFn = Callable[[HookContext], Awaitable[bool]]

ConfirmationKind = Literal["confirmation", "user_question", "workflow_approval"]
EligibleScope = Literal["initiator", "agent_owner", "org_members", "assignee"]
Delivery = Literal["auto", "inline_only", "channel_only"]
WaitOutcome = Literal["approve", "reject", "expired"]
Decision = Literal["approve", "reject"]

# Scopes that name one eligible user.
_SINGLE_USER_SCOPES = ("initiator", "agent_owner", "assignee")

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 1.5

_SCOPE_HINTS: dict[str, str] = {
    "initiator": "Approvable by: the user who started this run.",
    "agent_owner": "Approvable by: the agent owner only.",
    "org_members": "Approvable by: any member of this organization.",
}


@dataclass(frozen=True)
class ConfirmationSpec:
    """Everything needed to put one request before a human."""

    kind: ConfirmationKind
    # Who caused the request: the user chatting, or the user a run belongs to.
    initiator: Principal
    # The agent or workflow the request is raised through, if any.
    binding: Binding | None
    org_id: str | None
    title: str
    description: str | None
    # The tool name and arguments, or the questions asked.
    payload: dict[str, Any]
    # Who may decide. ``initiator``, ``agent_owner`` and ``assignee`` name
    # one user (``eligible_user_id``); ``org_members`` is any member of
    # ``org_id``.
    eligible_scope: EligibleScope
    eligible_user_id: str | None
    delivery: Delivery
    # The channel bound for approvals; considered only when it lies inside
    # the binding's organization.
    channel_id: str | None
    timeout_seconds: float
    conversation_id: str | None = None
    workflow_run_id: str | None = None
    node_id: str | None = None
    # The agent the request is raised under, when there is no full binding
    # to take it from.
    agent_id: str | None = None
    tool_call_id: str | None = None


def confirmation_required(*, tool_flag: bool, agent_require_all: bool) -> bool:
    """Whether a tool call needs a human's go-ahead.

    Either the tool (a connector action) is flagged as requiring
    confirmation, or the agent asks for confirmation on every tool call.
    """
    return tool_flag or agent_require_all


class ConfirmationService:
    """Creates requests, delivers them and waits for their answer."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------

    async def request(self, spec: ConfirmationSpec) -> Any:
        """Record the request and deliver it; returns the ``ConfirmationRequest`` row.

        Raises :class:`AssemblyRefusal` ``confirmation_undeliverable`` when
        the request cannot be put before anyone: ``channel_only`` with no
        usable channel, a channel with no target chat, or a failed send.
        """
        channel_row: Any = None
        carrier: ApprovalCarrier | None = None
        if spec.delivery != "inline_only":
            channel_row, carrier = await self._resolve_carrier(spec)

        if carrier is None:
            if spec.delivery == "channel_only":
                raise AssemblyRefusal(
                    CONFIRMATION_UNDELIVERABLE,
                    409,
                    "Agent is configured for channel_only confirmation "
                    "but no Feishu channel is bound or active.",
                )
            row = await self._create_row(spec, mode="inline", channel_row=None)
            # Best-effort: the listener never raises, and the row is polled
            # whether or not anyone was listening.
            await emit_inline_confirmation(row)
            return row

        target = carrier.approval_target()
        if not target:
            raise AssemblyRefusal(
                CONFIRMATION_UNDELIVERABLE, 409, "Feishu channel has no chat_id configured."
            )

        row = await self._create_row(spec, mode="channel", channel_row=channel_row)
        # The portal shows a read-only card ("sent to <channel>") while the
        # decision is expected in the channel.
        await emit_inline_confirmation(row)

        send_result = await carrier.send_approval(target, _summary_for(spec, row))
        if not send_result.ok:
            raise AssemblyRefusal(
                CONFIRMATION_UNDELIVERABLE,
                502,
                f"Failed to deliver Feishu confirmation card: {send_result.error}",
                {"confirmation_id": row.id},
            )
        return row

    async def _resolve_carrier(self, spec: ConfirmationSpec) -> tuple[Any, ApprovalCarrier | None]:
        """The bound channel row and its adapter, if it can carry approvals."""
        binding = spec.binding
        async with self._session_factory() as session:
            channel_row = await resolve_org_channel(
                session,
                channel_id=spec.channel_id,
                org_id=binding.org_id if binding is not None else None,
                owner_id=binding.owner_id if binding is not None else spec.initiator.user_id,
            )
        if channel_row is None:
            return None, None
        channel = build_channel(channel_row.type, dict(channel_row.config))
        if not isinstance(channel, ApprovalCarrier):
            logger.warning(
                "approval channel %r (type=%r) cannot carry approvals; treating it as unbound",
                getattr(channel_row, "id", None),
                getattr(channel_row, "type", None),
            )
            return None, None
        return channel_row, channel

    async def _create_row(self, spec: ConfirmationSpec, *, mode: str, channel_row: Any) -> Any:
        from fim_one.db.models.channel import ConfirmationRequest

        payload: dict[str, Any] = dict(spec.payload)
        if spec.kind == "confirmation":
            payload.update(
                mode=mode,
                channel_label=str(getattr(channel_row, "name", "") or ""),
                approver_scope=spec.eligible_scope,
            )

        agent_id = spec.agent_id
        if agent_id is None and spec.binding is not None and spec.binding.kind == "agent":
            agent_id = spec.binding.id

        row = ConfirmationRequest(
            id=str(uuid.uuid4()),
            tool_call_id=spec.tool_call_id,
            agent_id=agent_id,
            user_id=spec.initiator.user_id or None,
            # One named approver, or NULL when the scope is a group.
            approver_user_id=spec.eligible_user_id,
            eligible_scope=spec.eligible_scope,
            eligible_user_id=spec.eligible_user_id,
            # NULL, never an empty string: the column is a foreign key.
            org_id=spec.org_id or None,
            channel_id=channel_row.id if channel_row is not None else None,
            mode=mode,
            kind=spec.kind,
            status="pending",
            payload=payload,
            expires_at=datetime.now(UTC) + timedelta(seconds=spec.timeout_seconds),
            conversation_id=spec.conversation_id,
            workflow_run_id=spec.workflow_run_id,
            node_id=spec.node_id,
            title=spec.title[:500],
            description=spec.description,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            # Defaults (``created_at``) are read back before the row is
            # handed to a listener outside this session.
            await session.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    async def wait(
        self, confirmation_id: str, *, poll_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    ) -> WaitOutcome:
        """Poll the request until it is decided or its time is up.

        A request still pending at its ``expires_at`` is marked ``expired``
        here, so a late click finds it closed.
        """
        from sqlalchemy import select

        from fim_one.db.models.channel import ConfirmationRequest

        while True:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(ConfirmationRequest).where(ConfirmationRequest.id == confirmation_id)
                    )
                ).scalar_one_or_none()
            if row is None:
                return "expired"
            if row.status in ("approved", "approve"):
                return "approve"
            if row.status in ("rejected", "reject"):
                return "reject"
            if row.status != "pending":
                return "expired"
            if datetime.now(UTC) >= _aware(row.expires_at or row.created_at):
                await self._mark_expired(confirmation_id)
                return "expired"
            await asyncio.sleep(poll_seconds)

    async def _mark_expired(self, confirmation_id: str) -> None:
        from sqlalchemy import update

        from fim_one.db.models.channel import ConfirmationRequest

        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(ConfirmationRequest)
                    .where(
                        ConfirmationRequest.id == confirmation_id,
                        ConfirmationRequest.status == "pending",
                    )
                    .values(status="expired", responded_at=datetime.now(UTC))
                )
                await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to mark confirmation %s expired", confirmation_id)

    # ------------------------------------------------------------------
    # Deciding
    # ------------------------------------------------------------------

    async def may_decide(self, db: Any, row: Any, actor: Principal) -> bool:
        """Whether *actor* may decide *row*, by what the row says.

        ``initiator``, ``agent_owner`` and ``assignee`` name one user;
        ``org_members`` is any member of the row's organization. A platform
        administrator may always decide. A row with no scope (written
        before the scope was recorded) is judged by the agent's current
        setting, the rule that applied when it was written.
        """
        if actor.is_admin:
            return True
        scope = row.eligible_scope
        if scope is None:
            return await self._may_decide_by_agent_setting(db, row, actor)
        if scope in _SINGLE_USER_SCOPES:
            return bool(row.eligible_user_id) and row.eligible_user_id == actor.user_id
        if scope == "org_members":
            return bool(row.org_id) and row.org_id in actor.org_ids
        logger.warning("confirmation %s has unknown eligible_scope %r", row.id, scope)
        return False

    async def _may_decide_by_agent_setting(self, db: Any, row: Any, actor: Principal) -> bool:
        from sqlalchemy import select

        from fim_one.db.models.agent import Agent

        if row.kind == "user_question":
            return bool(row.user_id) and row.user_id == actor.user_id
        if not row.agent_id:
            return False
        agent = (
            await db.execute(select(Agent).where(Agent.id == row.agent_id))
        ).scalar_one_or_none()
        if agent is None:
            return False
        scope = str(getattr(agent, "confirmation_approver_scope", "initiator") or "initiator")
        scope = scope.lower()
        if scope == "initiator":
            return bool(row.user_id) and row.user_id == actor.user_id
        if scope == "agent_owner":
            return bool(agent.user_id) and agent.user_id == actor.user_id
        if scope == "org_members":
            return bool(agent.org_id) and agent.org_id in actor.org_ids
        logger.warning("agent %s has unknown confirmation_approver_scope %r", agent.id, scope)
        return False

    async def decide(
        self,
        db: Any,
        *,
        confirmation_id: str,
        actor: Principal,
        decision: Decision,
        note: str | None = None,
        via_channel_id: str | None = None,
        external_actor: str | None = None,
    ) -> Any:
        """Record *actor*'s decision on a pending request; returns the row.

        A decision arriving through a channel (``via_channel_id``) counts
        only for a request that channel carried: a callback's signature
        proves which organization's app sent it, not that the request was
        theirs, so a request sent elsewhere is "not found" to it.

        Raises :class:`AssemblyRefusal`: ``confirmation_not_found`` (404;
        also for a question, which is answered, not decided),
        ``confirmation_already_decided`` (409, ``detail_args["status"]``
        says how) or ``confirmation_forbidden`` (403). Two decisions racing
        for one row: exactly one wins, the other sees "already decided".
        """
        from sqlalchemy import select

        from fim_one.db.models.channel import ConfirmationRequest

        row = (
            await db.execute(
                select(ConfirmationRequest).where(ConfirmationRequest.id == confirmation_id)
            )
        ).scalar_one_or_none()
        if row is None or row.kind == "user_question":
            raise AssemblyRefusal(CONFIRMATION_NOT_FOUND, 404, "Confirmation request not found.")
        if via_channel_id is not None and row.channel_id != via_channel_id:
            raise AssemblyRefusal(CONFIRMATION_NOT_FOUND, 404, "Confirmation request not found.")
        if row.status != "pending":
            raise _already_decided(row.status)
        if not await self.may_decide(db, row, actor):
            raise AssemblyRefusal(
                CONFIRMATION_FORBIDDEN,
                403,
                "You are not authorised to respond to this confirmation.",
            )

        final_status, newly_applied, _payload = await record_decision(
            db,
            confirmation_id=confirmation_id,
            decision=decision,
            decided_by=actor.user_id,
            external_actor=external_actor,
            note=note,
        )
        if final_status is None:
            raise AssemblyRefusal(CONFIRMATION_NOT_FOUND, 404, "Confirmation request not found.")
        if not newly_applied:
            raise _already_decided(final_status)
        await db.refresh(row)
        return row

    async def pending_for(self, db: Any, actor: Principal, *, kind: str | None = None) -> list[Any]:
        """Pending requests *actor* may decide, newest first.

        By the row only: an administrator sees those naming them like
        anyone else, not everything.
        """
        from sqlalchemy import and_, or_, select

        from fim_one.db.models.channel import ConfirmationRequest

        eligible = or_(
            and_(
                ConfirmationRequest.eligible_scope.in_(_SINGLE_USER_SCOPES),
                ConfirmationRequest.eligible_user_id == actor.user_id,
            ),
            and_(
                ConfirmationRequest.eligible_scope == "org_members",
                ConfirmationRequest.org_id.in_(sorted(actor.org_ids)),
            ),
        )
        stmt = select(ConfirmationRequest).where(ConfirmationRequest.status == "pending", eligible)
        if kind is not None:
            stmt = stmt.where(ConfirmationRequest.kind == kind)
        result = await db.execute(stmt.order_by(ConfirmationRequest.created_at.desc()))
        return list(result.scalars().all())


def _already_decided(status: str) -> AssemblyRefusal:
    return AssemblyRefusal(
        CONFIRMATION_ALREADY_DECIDED,
        409,
        f"Confirmation is already {status}.",
        {"status": status},
    )


async def record_decision(
    db: Any,
    *,
    confirmation_id: str,
    decision: Decision,
    decided_by: str | None = None,
    external_actor: str | None = None,
    note: str | None = None,
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Flip a pending row to ``approved`` / ``rejected`` and say whether this call did it.

    Authorisation is the caller's business (:meth:`ConfirmationService.decide`);
    this is the write. A conditional ``UPDATE ... WHERE status='pending'``
    lets concurrent deciders race cleanly on SQLite and PostgreSQL alike:
    one update changes the row, the rest see a rowcount of zero.

    Returns ``(final_status, newly_applied, payload)``: ``(None, False,
    None)`` when there is no such row.
    """
    from sqlalchemy import CursorResult, select, update

    from fim_one.db.models.channel import ConfirmationRequest

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "status": "approved" if decision == "approve" else "rejected",
        "responded_at": now,
        "decided_by_user_id": decided_by,
        "decision_note": note,
    }
    if decided_by is not None:
        # The column that said who may decide now says who did; readers of
        # the API response rely on it.
        values["approver_user_id"] = decided_by
    if external_actor is not None:
        values["responded_by_open_id"] = external_actor

    result = cast(
        CursorResult[Any],
        await db.execute(
            update(ConfirmationRequest)
            .where(
                ConfirmationRequest.id == confirmation_id,
                ConfirmationRequest.status == "pending",
            )
            .values(**values)
        ),
    )
    await db.commit()
    newly_applied = (result.rowcount or 0) == 1

    row = (
        await db.execute(
            select(ConfirmationRequest).where(ConfirmationRequest.id == confirmation_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return (None, False, None)
    payload = dict(row.payload) if isinstance(row.payload, dict) else None
    return (str(row.status), newly_applied, payload)


def _aware(moment: datetime) -> datetime:
    """SQLite hands timestamps back without a zone; they are stored in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _summary_for(spec: ConfirmationSpec, row: Any) -> ApprovalRequestSummary:
    """What a channel is asked to show for *row*."""
    args = spec.payload.get("tool_args") or {}
    try:
        preview = json.dumps(args, ensure_ascii=False, indent=2)
    except Exception:
        preview = str(args)
    return ApprovalRequestSummary(
        confirmation_id=str(row.id),
        title=spec.title,
        summary=spec.description or "",
        tool_name=str(spec.payload.get("tool_name") or "unknown"),
        tool_args_preview=preview,
        approver_hint=_SCOPE_HINTS.get(spec.eligible_scope),
    )


# ---------------------------------------------------------------------------
# The pre-tool-use gate
# ---------------------------------------------------------------------------


async def _default_requires_confirmation(context: HookContext) -> bool:
    """Honor ``context.metadata['requires_confirmation']``.

    The agent loop fills it from the connector action's flag before it runs
    the hooks.
    """
    meta = context.metadata or {}
    return bool(meta.get("requires_confirmation"))


class ApprovalGateHook(PreToolUseHook):
    """Block a tool call until someone allowed to has approved it.

    Per call:

    1. Load the agent named by ``context.agent_id``.
    2. Nothing to do unless the action is flagged as requiring confirmation
       or the agent requires it for every call (:func:`confirmation_required`).
    3. Ask through :class:`ConfirmationService`, with the agent's
       ``confirmation_mode`` as the delivery, its ``approval_channel_id`` as
       the channel and its ``confirmation_approver_scope`` as who may decide.
    4. Wait. Approved: the tool runs. Rejected, expired or undeliverable:
       the call is blocked and the reason is returned to the model.
    """

    name = "approval_gate"
    description = (
        "Before running a tool flagged requires_confirmation, asks for "
        "approval inline in the portal or in the agent's approval channel "
        "(per the agent's confirmation_mode) and blocks until a human responds."
    )
    priority = 10  # Run early: rate limiters and loggers can come after.

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        requires_confirmation_fn: RequiresConfirmationFn | None = None,
        callback_base_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._service = ConfirmationService(session_factory)
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._requires_confirmation_fn = requires_confirmation_fn or _default_requires_confirmation
        # The public URL of this instance, shown on the card so an approver
        # can get back to the portal.
        self._callback_base_url = (
            callback_base_url or os.getenv("BACKEND_URL") or os.getenv("FRONTEND_URL") or ""
        ).rstrip("/")

    def should_trigger(self, context: HookContext) -> bool:
        """Always: the real decision needs the database and is made in ``execute``."""
        return True

    async def execute(self, context: HookContext) -> HookResult:
        # No agent means a chat with no agent bound: there is no per-agent
        # confirmation policy to apply, and the gate steps aside.
        if not context.agent_id:
            return HookResult(
                allow=True,
                side_effects=["approval_gate: no agent context, skipping gate"],
            )

        async with self._session_factory() as session:
            agent_row = await self._load_agent(session, context.agent_id)
        if agent_row is None:
            return HookResult(
                allow=False,
                error=(f"Tool requires confirmation but agent {context.agent_id!r} was not found."),
                side_effects=["approval_gate: agent not found"],
            )

        try:
            action_requires = await self._requires_confirmation_fn(context)
        except Exception:  # pragma: no cover - defensive
            logger.exception("requires_confirmation_fn raised; skipping gate")
            return HookResult()

        if not confirmation_required(
            tool_flag=action_requires,
            agent_require_all=bool(getattr(agent_row, "require_confirmation_for_all", False)),
        ):
            return HookResult()

        try:
            row = await self._service.request(self._spec_for(context, agent_row))
        except AssemblyRefusal as refusal:
            return HookResult(
                allow=False,
                error=refusal.detail,
                side_effects=[f"approval_gate: {refusal.error_code}"],
            )

        outcome = await self._service.wait(row.id, poll_seconds=self._poll_interval_seconds)
        return self._result_for(str(row.id), outcome, mode=str(row.mode))

    def _spec_for(self, context: HookContext, agent_row: Any) -> ConfirmationSpec:
        metadata = context.metadata or {}
        agent_org: str | None = getattr(agent_row, "org_id", None)
        raw_org = metadata.get("org_id")
        org_id = raw_org if isinstance(raw_org, str) and raw_org else agent_org

        scope = str(
            getattr(agent_row, "confirmation_approver_scope", "initiator") or "initiator"
        ).lower()
        owner_id: str | None = getattr(agent_row, "user_id", None)
        eligible_user_id: str | None = None
        if scope == "initiator":
            eligible_user_id = context.user_id
        elif scope == "agent_owner":
            eligible_user_id = owner_id

        mode = str(getattr(agent_row, "confirmation_mode", "auto") or "auto").strip().lower()
        delivery: Delivery = (
            cast(Delivery, mode) if mode in ("inline_only", "channel_only") else "auto"
        )

        summary_lines = [
            "**FIM One is requesting approval to run a sensitive tool.**",
            "",
            "Approve only if you expect this action right now.",
        ]
        if self._callback_base_url:
            summary_lines.append(f"\nPortal: {self._callback_base_url}")

        tool_call_id = metadata.get("tool_call_id")
        return ConfirmationSpec(
            kind="confirmation",
            initiator=provisional_principal(context.user_id or ""),
            binding=Binding(
                kind="agent",
                id=str(context.agent_id),
                owner_id=owner_id or "",
                org_id=agent_org,
            ),
            org_id=org_id,
            title="FIM One — Approval Required",
            description="\n".join(summary_lines),
            payload={
                "tool_name": context.tool_name,
                "tool_args": context.tool_args or {},
            },
            # A value outside the known scopes is recorded as it stands, and
            # nobody is eligible under it.
            eligible_scope=cast(EligibleScope, scope),
            eligible_user_id=eligible_user_id,
            delivery=delivery,
            channel_id=getattr(agent_row, "approval_channel_id", None),
            timeout_seconds=self._timeout_seconds,
            conversation_id=context.conversation_id,
            agent_id=context.agent_id,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
        )

    def _result_for(self, confirmation_id: str, outcome: WaitOutcome, *, mode: str) -> HookResult:
        if outcome == "approve":
            return HookResult(
                allow=True,
                side_effects=[f"approval_gate: approved ({mode}, id={confirmation_id})"],
            )
        if outcome == "reject":
            return HookResult(
                allow=False,
                error="Tool call was rejected by an operator.",
                side_effects=[f"approval_gate: rejected ({mode}, id={confirmation_id})"],
            )
        return HookResult(
            allow=False,
            error=(f"Tool call timed out waiting for confirmation after {self._timeout_seconds}s."),
            side_effects=[f"approval_gate: expired ({mode}, id={confirmation_id})"],
        )

    async def _load_agent(self, session: Any, agent_id: str) -> Any:
        from sqlalchemy import select

        from fim_one.db.models.agent import Agent

        result = await session.execute(select(Agent).where(Agent.id == agent_id).limit(1))
        return result.scalar_one_or_none()


def create_approval_gate_hook(
    *,
    session_factory: SessionFactory,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    requires_confirmation_fn: RequiresConfirmationFn | None = None,
    callback_base_url: str | None = None,
) -> ApprovalGateHook:
    """A configured :class:`ApprovalGateHook`."""
    return ApprovalGateHook(
        session_factory=session_factory,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        requires_confirmation_fn=requires_confirmation_fn,
        callback_base_url=callback_base_url,
    )
