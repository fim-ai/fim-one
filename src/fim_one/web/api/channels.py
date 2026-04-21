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
from typing import cast as _cast

from sqlalchemy import CursorResult, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.channels import build_channel
from fim_one.core.channels.feishu import (
    FeishuChannel,
    build_confirmation_card,
    build_decided_card,
)
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
    ChatDiscoveryRequest,
    ChatDiscoveryResponse,
    ChatInfo,
    ConfirmationStatusResponse,
    TestApprovalRequest,
    TestApprovalResponse,
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
# Hook Playground — full approval-gate round-trip from the UI
# ---------------------------------------------------------------------------


_DEFAULT_TEST_TOOL_NAME = "delete_customer_records"
_DEFAULT_TEST_TOOL_ARGS: dict[str, Any] = {
    "customer_id": "C-20240892",
    "reason": "GDPR erasure request",
    "confirmed_by": "ops@acme.com",
}
_DEFAULT_TEST_TITLE = "FIM One — Approval Required (Hook Playground)"
_DEFAULT_TEST_SUMMARY = (
    "**Playground test.** This card is identical to what a real agent "
    "gate would show. Press **Approve** or **Reject** to drive the "
    "round-trip; the portal is polling for your decision."
)


@router.post(
    "/{channel_id}/test-approval", response_model=TestApprovalResponse
)
async def test_approval(
    channel_id: str,
    body: TestApprovalRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TestApprovalResponse:
    """Simulate a ``FeishuGateHook`` round-trip.

    Creates a real ``ConfirmationRequest`` row in ``pending`` state, sends
    the production approval card to the channel's chat, and returns the
    ``confirmation_id``.  The UI then polls ``GET /confirmations/{id}``
    until the status flips (operator clicked Approve/Reject in Feishu) or
    the caller gives up.

    Unlike ``/test`` which uses a sentinel ``test-*`` id that the callback
    handler ignores, this endpoint creates a genuine DB row — so the card
    buttons drive the same code path that a production hook would.
    """
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_member(channel.org_id, user, db)

    if not channel.is_active:
        return TestApprovalResponse(
            ok=False, error="Channel is disabled; enable it first."
        )

    adapter = build_channel(channel.type, dict(channel.config or {}))
    if adapter is None or not isinstance(adapter, FeishuChannel):
        return TestApprovalResponse(
            ok=False,
            error=(
                "Approval playground currently supports Feishu channels "
                "only."
            ),
        )

    chat_id = str((channel.config or {}).get("chat_id") or "").strip()
    if not chat_id:
        return TestApprovalResponse(
            ok=False, error="channel has no chat_id configured"
        )

    tool_name = (body.tool_name or _DEFAULT_TEST_TOOL_NAME).strip()
    tool_args = body.tool_args if body.tool_args is not None else _DEFAULT_TEST_TOOL_ARGS
    title = (body.title or _DEFAULT_TEST_TITLE).strip()
    summary = (body.summary or _DEFAULT_TEST_SUMMARY).strip()

    # Pretty-render args for the card body.
    try:
        import json as _json  # local alias to avoid the outer json shadow
        preview = _json.dumps(tool_args, ensure_ascii=False, indent=2)
    except Exception:
        preview = str(tool_args)

    confirmation_id = str(uuid.uuid4())
    req = ConfirmationRequest(
        id=confirmation_id,
        tool_call_id=None,
        agent_id=None,
        user_id=user.id,
        org_id=channel.org_id,
        channel_id=channel.id,
        status="pending",
        payload={
            "tool_name": tool_name,
            "tool_args": tool_args,
            "test_mode": True,
            "initiator": user.email or user.username,
        },
    )
    db.add(req)
    await db.commit()

    card = build_confirmation_card(
        confirmation_id=confirmation_id,
        title=title,
        summary=summary,
        tool_name=tool_name,
        tool_args_preview=preview,
    )
    result = await adapter.send_interactive_card(chat_id, card)

    if not result.ok:
        # Roll the pending row into a terminal failure so the UI can stop
        # polling immediately.
        req.status = "expired"
        req.responded_at = datetime.now(UTC)
        await db.commit()
        return TestApprovalResponse(
            ok=False,
            confirmation_id=confirmation_id,
            error=result.error or "Failed to deliver approval card.",
        )

    return TestApprovalResponse(ok=True, confirmation_id=confirmation_id)


@router.get(
    "/{channel_id}/confirmations/{confirmation_id}",
    response_model=ConfirmationStatusResponse,
)
async def get_confirmation_status(
    channel_id: str,
    confirmation_id: str,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConfirmationStatusResponse:
    """Return the current status of a ``ConfirmationRequest`` row.

    The frontend polls this every ~2s while a Hook Playground session is
    running.  Requires org membership (the same scope that can create the
    request in the first place).
    """
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_member(channel.org_id, user, db)

    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id,
                ConfirmationRequest.channel_id == channel.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Confirmation request not found.",
        )

    payload: dict[str, Any] = dict(row.payload or {})
    tool_name_raw = payload.get("tool_name")
    tool_args_raw = payload.get("tool_args")
    tool_args_out: dict[str, Any] | None = (
        tool_args_raw if isinstance(tool_args_raw, dict) else None
    )

    return ConfirmationStatusResponse(
        id=row.id,
        status=row.status,
        tool_name=(
            str(tool_name_raw) if isinstance(tool_name_raw, str) else None
        ),
        tool_args=tool_args_out,
        test_mode=bool(payload.get("test_mode", False)),
        created_at=row.created_at.isoformat() if row.created_at else "",
        responded_at=(
            row.responded_at.isoformat() if row.responded_at else None
        ),
        responded_by_open_id=row.responded_by_open_id,
    )


# ---------------------------------------------------------------------------
# Chat discovery (Feishu group picker)
# ---------------------------------------------------------------------------


@router.post("/discover-chats", response_model=ChatDiscoveryResponse)
async def discover_chats(
    body: ChatDiscoveryRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ChatDiscoveryResponse:
    """List Feishu groups the given app/bot is a member of.

    Caller flow:

    - **Create mode** (no channel row yet) — send ``app_id`` + ``app_secret``
      + ``org_id``.  The server authenticates the user as an admin of
      ``org_id`` and queries Feishu directly.
    - **Edit mode** — send ``channel_id`` (+ optional ``app_secret`` if
      the user re-typed it).  The server loads the channel, checks org
      admin, and uses the decrypted stored secret when no fresh secret
      was provided.

    Returns a list of ``ChatInfo`` rows the caller can render into a
    picker UI.  Errors from Feishu (invalid credentials, network, etc.)
    surface as ``400`` with a human-readable ``detail`` message so the
    UI can show it inline.
    """
    # Resolve org + secret based on mode.
    app_id = body.app_id.strip()
    app_secret: str | None = (body.app_secret or "").strip() or None

    if body.channel_id:
        channel = await _load_channel_or_404(db, body.channel_id)
        await require_org_admin(channel.org_id, user, db)
        if app_secret is None:
            stored_secret = str((channel.config or {}).get("app_secret") or "")
            if not stored_secret:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Channel has no stored app_secret; re-enter it to "
                        "fetch the group list."
                    ),
                )
            app_secret = stored_secret
        # If caller also provided a new app_id, prefer it, but keep stored
        # when missing.
        if not app_id:
            app_id = str((channel.config or {}).get("app_id") or "")
    else:
        # Create mode — org_id required, user must be an admin there.
        if not body.org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="org_id is required when channel_id is not provided.",
            )
        await require_org_admin(body.org_id, user, db)
        if not app_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="app_secret is required to fetch the group list.",
            )

    if not app_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="app_id is required to fetch the group list.",
        )

    adapter = FeishuChannel({"app_id": app_id, "app_secret": app_secret})
    try:
        raw_items = await adapter.list_chats()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pragma: no cover - network edge cases
        logger.exception("Feishu list_chats failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to reach Feishu: {type(exc).__name__}: {exc}",
        ) from exc

    items: list[ChatInfo] = []
    for raw in raw_items:
        chat_id = raw.get("chat_id")
        name = raw.get("name")
        if not isinstance(chat_id, str) or not chat_id:
            continue
        member_count_raw = raw.get("member_count")
        member_count: int | None
        if isinstance(member_count_raw, int):
            member_count = member_count_raw
        elif isinstance(member_count_raw, str) and member_count_raw.isdigit():
            member_count = int(member_count_raw)
        else:
            member_count = None

        items.append(
            ChatInfo(
                chat_id=chat_id,
                name=str(name) if isinstance(name, str) else chat_id,
                avatar=(
                    str(raw["avatar"])
                    if isinstance(raw.get("avatar"), str) and raw["avatar"]
                    else None
                ),
                description=(
                    str(raw["description"])
                    if isinstance(raw.get("description"), str)
                    and raw["description"]
                    else None
                ),
                member_count=member_count,
                external=bool(raw.get("external", False)),
            )
        )
    return ChatDiscoveryResponse(items=items)


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

    # Log every incoming callback at INFO so operators can diagnose card
    # flows without a debugger.  Strips the raw body to <2KB to avoid
    # flooding logs with huge cards.
    try:
        preview = (raw_body or b"").decode("utf-8", errors="replace")[:2000]
    except Exception:
        preview = ""
    # Log header keys that matter for diagnosing signature / schema issues.
    relevant_headers = {
        k: v
        for k, v in headers.items()
        if k.lower().startswith(("x-lark", "content-type", "user-agent"))
    }
    logger.info(
        "channel_callback: channel=%s type=%s headers=%s body_preview=%s",
        channel_id,
        channel.type,
        relevant_headers,
        preview,
    )

    # 1. Signature verification (only binds if encrypt_key is set).
    valid = await adapter.verify_signature(raw_body, headers)
    if not valid:
        logger.warning(
            "channel_callback: signature verification failed (channel=%s)",
            channel_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid callback signature",
        )

    # 2. Dispatch to adapter's handle_callback().
    if not isinstance(parsed, dict):
        parsed = {}
    result = await adapter.handle_callback(parsed, headers)

    event = result.get("event") if isinstance(result, dict) else None
    response_body: dict[str, Any] = (
        result.get("response") if isinstance(result, dict) else None
    ) or {}

    # 3. Side-effect: update ConfirmationRequest on card actions.
    #    Feishu shows error code 200340 on the clicker's client when the
    #    webhook response schema isn't recognised as a valid card-action
    #    ACK.  A bare ``{}`` technically works for *some* interaction
    #    modes but fails for ``card.action.trigger`` events sent under
    #    the schema=2.0 envelope — so we always return a ``toast`` and a
    #    card update, which satisfies both paths.
    if isinstance(event, dict) and event.get("kind") == "card_action":
        confirmation_id = event.get("confirmation_id")
        decision = event.get("action")
        if confirmation_id and decision in ("approve", "reject"):
            open_id_raw = event.get("open_id")
            open_id = str(open_id_raw) if open_id_raw else None
            final_status, newly_applied, payload = await _record_decision(
                db,
                confirmation_id=str(confirmation_id),
                decision=str(decision),
                open_id=open_id,
            )
            if final_status is None:
                # Confirmation row gone (deleted / expired sweep).  Return
                # a benign toast so Feishu doesn't show 200340.
                response_body = {
                    "toast": {
                        "type": "error",
                        "content": "Confirmation request not found.",
                    }
                }
            else:
                tool_name: str | None = None
                if isinstance(payload, dict):
                    raw_tool = payload.get("tool_name")
                    if raw_tool:
                        tool_name = str(raw_tool)
                response_body = _build_card_action_response(
                    final_status=final_status,
                    newly_applied=newly_applied,
                    confirmation_id=str(confirmation_id),
                    tool_name=tool_name,
                    decided_by=open_id,
                )
        else:
            # Unknown / malformed payload — still return a well-formed
            # toast so Feishu doesn't flash 200340.
            response_body = {
                "toast": {
                    "type": "error",
                    "content": "Unrecognised action payload.",
                }
            }

    logger.info(
        "channel_callback: responding channel=%s body=%s",
        channel_id,
        response_body,
    )
    return response_body


def _build_card_action_response(
    *,
    final_status: str,
    newly_applied: bool,
    confirmation_id: str,
    tool_name: str | None,
    decided_by: str | None,
) -> dict[str, Any]:
    """Build the Feishu-compliant response for an Approve/Reject click.

    Returns a ``toast`` (user-visible banner) *plus* a ``card`` stanza
    that replaces the original confirmation card with a stripped-down
    "decided" card — that's how we prevent further clicks on the same
    card.  Returning just ``{}`` triggers error 200340 on the clicker's
    client.

    ``newly_applied=False`` means the DB row was already in a terminal
    state when this click arrived (duplicate/stale click).  In that
    case we still update the card (defense in depth — the user's client
    may have cached an old copy) but the toast wording reflects that
    nothing changed.
    """
    if final_status == "approved":
        toast_type = "success"
        toast_content = (
            "Approval recorded."
            if newly_applied
            else "This request was already approved."
        )
    elif final_status == "rejected":
        toast_type = "info"
        toast_content = (
            "Rejection recorded."
            if newly_applied
            else "This request was already rejected."
        )
    else:
        toast_type = "warning"
        toast_content = "This request is no longer active."

    decided_card = build_decided_card(
        confirmation_id=confirmation_id,
        decision=final_status,
        tool_name=tool_name,
        decided_by=decided_by,
        was_already_decided=not newly_applied,
    )
    return {
        "toast": {"type": toast_type, "content": toast_content},
        "card": {
            "type": "raw",
            "data": decided_card,
        },
    }


async def _record_decision(
    db: AsyncSession,
    *,
    confirmation_id: str,
    decision: str,
    open_id: str | None,
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Atomically flip the ConfirmationRequest status if still pending.

    Uses a conditional ``UPDATE ... WHERE status='pending'`` so that two
    concurrent callbacks (e.g. same user double-taps and both clicks
    race through the webhook) can't both succeed — only the UPDATE
    whose WHERE predicate matches ``pending`` at commit time changes a
    row.  The other caller sees rowcount=0 and falls into the
    "already-decided" branch.

    This works on both SQLite and Postgres without needing
    ``SELECT ... FOR UPDATE``: SQLite serializes writes on a single
    connection, Postgres uses MVCC with row-level locks on UPDATE.

    Returns ``(final_status, newly_applied, payload)``:

    * ``(None, False, None)`` — confirmation row not found.
    * ``(status, True, payload)`` — this call won the race and flipped
      the row.
    * ``(status, False, payload)`` — the row was already terminal when
      the UPDATE ran (duplicate click or lost race); no state change.
    """
    new_status = "approved" if decision == "approve" else "rejected"
    stmt = (
        sa_update(ConfirmationRequest)
        .where(
            ConfirmationRequest.id == confirmation_id,
            ConfirmationRequest.status == "pending",
        )
        .values(
            status=new_status,
            responded_at=datetime.now(UTC),
            responded_by_open_id=open_id,
        )
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


__all__ = ["router"]
