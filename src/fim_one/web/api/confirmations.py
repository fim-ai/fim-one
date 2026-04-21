"""Inline confirmation respond endpoint.

When an agent hits a tool flagged ``requires_confirmation=True``, the
``FeishuGateHook`` creates a ``ConfirmationRequest(mode='inline')`` row
(or ``mode='channel'`` if routed to Feishu) and blocks its poll until
someone flips the status.

This router exposes the frontend-facing counterpart to Feishu's
``/api/channels/{id}/callback``: an authenticated user clicks Approve /
Reject in the portal → POST ``/api/confirmations/{id}/respond`` → the row
flips, the hook wakes up, the tool proceeds or aborts.

Scope enforcement follows the agent's ``confirmation_approver_scope``
column (initiator / agent_owner / org_members).  The request body also
accepts an optional ``reason`` string — currently only logged, but kept
so the frontend can wire a textarea without a second API bump later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import CursorResult, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import cast as _cast

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.models.agent import Agent
from fim_one.web.models.channel import ConfirmationRequest
from fim_one.web.models.organization import OrgMembership
from fim_one.web.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


Decision = Literal["approve", "reject"]


class ConfirmationRespondRequest(BaseModel):
    decision: Decision
    reason: str | None = Field(default=None, max_length=2000)


class ConfirmationRespondResponse(BaseModel):
    status: str
    confirmation_id: str
    decided_at: str


# ---------------------------------------------------------------------------
# Shared update helper — also imported by the Feishu callback path so both
# surfaces stamp ``approver_user_id`` / ``decided_at`` / ``responded_at``
# consistently.
# ---------------------------------------------------------------------------


async def apply_confirmation_decision(
    db: AsyncSession,
    *,
    confirmation_id: str,
    decision: Decision,
    approver_user_id: str | None = None,
    responded_by_open_id: str | None = None,
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Atomically flip a ``ConfirmationRequest`` to ``approved`` / ``rejected``.

    Uses a conditional ``UPDATE ... WHERE status='pending'`` so concurrent
    responders race cleanly — exactly one UPDATE changes a row, the rest
    see rowcount=0 and fall into the "already decided" branch.  Works on
    both SQLite (serialised writes) and Postgres (MVCC row locks).

    Returns ``(final_status, newly_applied, payload)``:

    * ``(None, False, None)`` — row not found.
    * ``(status, True, payload)`` — this call flipped the row.
    * ``(status, False, payload)`` — already terminal on arrival.
    """
    new_status = "approved" if decision == "approve" else "rejected"
    now = datetime.now(timezone.utc)

    values: dict[str, Any] = {
        "status": new_status,
        "responded_at": now,
    }
    if approver_user_id is not None:
        values["approver_user_id"] = approver_user_id
    if responded_by_open_id is not None:
        values["responded_by_open_id"] = responded_by_open_id

    stmt = (
        sa_update(ConfirmationRequest)
        .where(
            ConfirmationRequest.id == confirmation_id,
            ConfirmationRequest.status == "pending",
        )
        .values(**values)
    )
    result = _cast(CursorResult[Any], await db.execute(stmt))
    await db.commit()
    newly_applied = (result.rowcount or 0) == 1

    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return (None, False, None)

    payload_snapshot: dict[str, Any] | None = (
        dict(row.payload) if isinstance(row.payload, dict) else None
    )
    return (row.status, newly_applied, payload_snapshot)


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


async def _user_in_org(
    db: AsyncSession, *, user_id: str, org_id: str
) -> bool:
    if not org_id:
        return False
    stmt = (
        select(OrgMembership.id)
        .where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _check_scope_or_403(
    db: AsyncSession,
    *,
    request: ConfirmationRequest,
    agent: Agent,
    current_user: User,
) -> None:
    """Raise 403 if *current_user* may not decide this confirmation.

    Rules driven by ``agent.confirmation_approver_scope``:

    * ``initiator``    — only ``request.user_id``.
    * ``agent_owner``  — only ``agent.user_id``.
    * ``org_members``  — any member of ``agent.org_id``.

    Platform admins (``User.is_admin``) bypass the scope check; they can
    always approve (helps on-call break glass).
    """
    if getattr(current_user, "is_admin", False):
        return

    scope = str(
        getattr(agent, "confirmation_approver_scope", "initiator")
        or "initiator"
    ).lower()

    if scope == "initiator":
        if current_user.id == request.user_id:
            return
    elif scope == "agent_owner":
        if current_user.id == agent.user_id:
            return
    elif scope == "org_members":
        if agent.org_id and await _user_in_org(
            db, user_id=current_user.id, org_id=agent.org_id
        ):
            return
    else:
        # Unknown scope — log and deny.  A strict default is safer than
        # silently granting access when config drifts.
        logger.warning(
            "confirmations.respond: unknown approver scope %r on agent %s",
            scope,
            agent.id,
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not authorised to respond to this confirmation.",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{confirmation_id}/respond",
    response_model=ConfirmationRespondResponse,
)
async def respond_to_confirmation(
    confirmation_id: str,
    body: ConfirmationRespondRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConfirmationRespondResponse:
    """Approve or reject a pending inline confirmation.

    * ``404`` — confirmation not found.
    * ``403`` — caller not in scope (per agent.confirmation_approver_scope).
    * ``409`` — already decided / expired.
    * ``200`` — flipped successfully.
    """
    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confirmation request not found.",
        )

    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Confirmation is already {row.status}.",
        )

    if not row.agent_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Confirmation has no agent binding — cannot resolve "
                "approver scope."
            ),
        )

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == row.agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent referenced by confirmation no longer exists.",
        )

    await _check_scope_or_403(
        db, request=row, agent=agent, current_user=current_user
    )

    if body.reason:
        logger.info(
            "confirmations.respond: user=%s decision=%s reason=%r",
            current_user.id,
            body.decision,
            body.reason[:200],
        )

    final_status, newly_applied, _payload = await apply_confirmation_decision(
        db,
        confirmation_id=confirmation_id,
        decision=body.decision,
        approver_user_id=current_user.id,
    )

    if final_status is None:
        # Deleted between our SELECT and UPDATE — treat as not-found.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confirmation request not found.",
        )

    if not newly_applied:
        # Raced with another approver — surface the actual state.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Confirmation is already {final_status}.",
        )

    # Reload to get the canonical timestamp stamped by the UPDATE.
    refreshed = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id
            )
        )
    ).scalar_one_or_none()
    decided_at_dt = (
        refreshed.responded_at if refreshed else None
    ) or datetime.now(timezone.utc)
    if decided_at_dt.tzinfo is None:
        decided_at_dt = decided_at_dt.replace(tzinfo=timezone.utc)

    return ConfirmationRespondResponse(
        status=final_status,
        confirmation_id=confirmation_id,
        decided_at=decided_at_dt.astimezone(timezone.utc).isoformat(),
    )


__all__ = ["router", "apply_confirmation_decision"]
