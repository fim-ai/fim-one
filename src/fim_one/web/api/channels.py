"""Channel CRUD + Feishu callback endpoints.

Channels are org-scoped outbound messaging integrations (Feishu, WeCom,
Slack, ...).  Authenticated, org-admin users can create / update /
delete / test them; org members can list / read.

The ``/callback`` endpoint receives unauthenticated HTTP requests from
the external platform (e.g. Feishu).  Authenticity is enforced by the
channel's signature-verification logic.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.channels import build_channel
from fim_one.core.channels.feishu import FeishuChannel, build_confirmation_card
from fim_one.db import get_session
from fim_one.web.auth import (
    get_current_user,
    require_org_admin,
    require_org_member,
)
from fim_one.web.models.channel import Channel, ConfirmationRequest
from fim_one.web.models.user import User
from fim_one.web.schemas.channel import (
    ChannelCreate,
    ChannelListResponse,
    ChannelResponse,
    ChannelTestResponse,
    ChannelUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _callback_base_url() -> str:
    """Return the public backend base URL for building callback URLs."""
    base = (
        os.getenv("BACKEND_URL")
        or os.getenv("PUBLIC_BACKEND_URL")
        or os.getenv("FRONTEND_URL")
        or ""
    ).rstrip("/")
    return base


def _build_callback_url(channel_id: str) -> str:
    base = _callback_base_url()
    path = f"/api/channels/{channel_id}/callback"
    return f"{base}{path}" if base else path


async def _load_channel_or_404(
    db: AsyncSession, channel_id: str
) -> Channel:
    row = (
        await db.execute(select(Channel).where(Channel.id == channel_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )
    return row


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ChannelResponse, status_code=201)
async def create_channel(
    body: ChannelCreate,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChannelResponse:
    """Create a new channel.  Requires org admin (or org owner)."""
    await require_org_admin(body.org_id, user, db)

    ch = Channel(
        id=str(uuid.uuid4()),
        name=body.name,
        type=body.type,
        org_id=body.org_id,
        config=body.config or {},
        is_active=body.is_active,
        created_by=user.id,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)

    return ChannelResponse.from_orm_masked(
        ch, callback_url=_build_callback_url(ch.id)
    )


@router.get("", response_model=ChannelListResponse)
async def list_channels(
    org_id: str,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChannelListResponse:
    """List channels in an organization.  Requires org membership."""
    await require_org_member(org_id, user, db)

    rows = (
        await db.execute(
            select(Channel)
            .where(Channel.org_id == org_id)
            .order_by(Channel.created_at.desc())
        )
    ).scalars().all()

    return ChannelListResponse(
        items=[
            ChannelResponse.from_orm_masked(
                row, callback_url=_build_callback_url(row.id)
            )
            for row in rows
        ]
    )


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(
    channel_id: str,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChannelResponse:
    """Get one channel.  Requires org membership."""
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_member(channel.org_id, user, db)
    return ChannelResponse.from_orm_masked(
        channel, callback_url=_build_callback_url(channel.id)
    )


@router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: str,
    body: ChannelUpdate,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChannelResponse:
    """Update channel fields.  Requires org admin."""
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_admin(channel.org_id, user, db)

    if body.name is not None:
        channel.name = body.name
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.config is not None:
        # Merge caller-provided keys on top of existing config (so a client
        # can PATCH just the chat_id without re-sending app_secret).
        merged = dict(channel.config or {})
        merged.update(body.config)
        channel.config = merged

    await db.commit()
    await db.refresh(channel)
    return ChannelResponse.from_orm_masked(
        channel, callback_url=_build_callback_url(channel.id)
    )


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete a channel.  Requires org admin.  Cascades to
    ``confirmation_requests``.
    """
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_admin(channel.org_id, user, db)
    await db.delete(channel)
    await db.commit()


# ---------------------------------------------------------------------------
# Test send
# ---------------------------------------------------------------------------


@router.post("/{channel_id}/test", response_model=ChannelTestResponse)
async def test_channel(
    channel_id: str,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChannelTestResponse:
    """Send a test message to the channel's configured chat.

    Requires org membership.
    """
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_member(channel.org_id, user, db)

    adapter = build_channel(channel.type, dict(channel.config or {}))
    if adapter is None:
        return ChannelTestResponse(
            ok=False,
            error=f"Unsupported channel type: {channel.type}",
        )

    chat_id = str((channel.config or {}).get("chat_id") or "").strip()
    if not chat_id:
        return ChannelTestResponse(
            ok=False, error="channel has no chat_id configured"
        )

    # FeishuChannel: send a preview of the actual confirmation card, so
    # operators can visually verify what users will see when a real
    # tool-confirmation gate fires.  Buttons carry a sentinel
    # confirmation_id; clicks are safely no-ops (the callback handler
    # gracefully ignores confirmation_ids that don't match a DB row).
    if isinstance(adapter, FeishuChannel):
        sender = user.email or user.username
        card = build_confirmation_card(
            confirmation_id=f"test-{uuid.uuid4()}",
            title="FIM One - Test Confirmation Card",
            summary=(
                f"**This is a preview.** It shows exactly how a real "
                f"confirmation gate will appear when an agent requests "
                f"approval for a sensitive tool call.\n\n"
                f"Sent by **{sender}** from the FIM One portal."
            ),
            tool_name="(preview) submit_approval",
            tool_args_preview='{\n  "contract_id": "PO-2024-0892",\n  "amount": 386000\n}',
        )
        result = await adapter.send_interactive_card(chat_id, card)
    else:
        result = await adapter.send_message(
            {
                "chat_id": chat_id,
                "msg_type": "text",
                "content": f"FIM One test message from {user.email or user.username}",
            }
        )
    return ChannelTestResponse(ok=result.ok, error=result.error)


# ---------------------------------------------------------------------------
# Public callback endpoint (no auth — platform-signed)
# ---------------------------------------------------------------------------


@router.post("/{channel_id}/callback")
async def channel_callback(
    channel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Handle a callback from the external messaging platform.

    For Feishu, this endpoint:
    1. Verifies the request signature (if ``encrypt_key`` is configured).
    2. Handles the one-time ``url_verification`` handshake by echoing the
       ``challenge`` string.
    3. Parses card-action clicks (Approve / Reject) and updates the
       associated ``ConfirmationRequest`` row.

    Returns the raw response body the platform expects (e.g. the challenge
    echo for verification, ``{}`` otherwise).
    """
    channel = await _load_channel_or_404(db, channel_id)

    adapter = build_channel(channel.type, dict(channel.config or {}))
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported channel type: {channel.type}",
        )

    raw_body = await request.body()
    headers = dict(request.headers)

    # Parse JSON body once.  Empty body -> {}
    try:
        parsed = await request.json() if raw_body else {}
    except Exception:
        parsed = {}

    # 1. Signature verification (only binds if encrypt_key is set).
    valid = await adapter.verify_signature(raw_body, headers)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid callback signature",
        )

    # 2. Dispatch to adapter's handle_callback().
    if not isinstance(parsed, dict):
        parsed = {}
    result = await adapter.handle_callback(parsed, headers)

    event = result.get("event") if isinstance(result, dict) else None
    response_body = (
        result.get("response") if isinstance(result, dict) else None
    ) or {}

    # 3. Side-effect: update ConfirmationRequest on card actions.
    if isinstance(event, dict) and event.get("kind") == "card_action":
        confirmation_id = event.get("confirmation_id")
        decision = event.get("action")
        if confirmation_id and decision in ("approve", "reject"):
            await _record_decision(
                db,
                confirmation_id=str(confirmation_id),
                decision=str(decision),
                open_id=(str(event.get("open_id")) if event.get("open_id") else None),
            )

    return response_body


async def _record_decision(
    db: AsyncSession,
    *,
    confirmation_id: str,
    decision: str,
    open_id: str | None,
) -> None:
    """Flip the ConfirmationRequest status if still pending."""
    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    if row.status != "pending":
        # Already decided — ignore duplicate clicks.
        return
    row.status = "approved" if decision == "approve" else "rejected"
    row.responded_at = datetime.now(UTC)
    row.responded_by_open_id = open_id
    await db.commit()


__all__ = ["router"]
