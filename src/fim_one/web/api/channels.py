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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.assembly.confirmation import ConfirmationService, ConfirmationSpec
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import (
    AuthMethod,
    Principal,
    resolve_external_actor,
    resolve_principal,
)
from fim_one.core.channels import ApprovalCarrier, build_channel, get_channel_class
from fim_one.core.channels.feishu import (
    BASE_URL_NOT_ALLOWED,
    FeishuBaseUrlNotAllowed,
    FeishuChannel,
    base_url_allowed,
    build_decided_card,
)
from fim_one.core.audit import actions
from fim_one.core.audit import writer as audit
from fim_one.core.audit.redact import diff_changes
from fim_one.db import get_session
from fim_one.web.audit_events import record_resource
from fim_one.web.exceptions import AppError
from fim_one.web.auth import (
    get_current_user,
    require_org_admin,
    require_org_member,
)
from fim_one.db.models.channel import Channel, ConfirmationRequest
from fim_one.db.models.user import User
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


def _require_valid_config(channel_type: str, config: dict[str, Any]) -> None:
    """Reject a config its channel type could never deliver with.

    Each type names what it needs (:meth:`BaseChannel.config_error`): a
    Feishu channel an ``encrypt_key``, a webhook channel a URL on its
    platform's host, an email channel recipients the instance may mail.
    """
    channel_class = get_channel_class(channel_type)
    error_code = (
        "channel_type_unsupported"
        if channel_class is None
        else channel_class.config_error(config)
    )
    if error_code is not None:
        # ``detail`` repeats the code for clients that read only that field.
        raise AppError(error_code, status_code=status.HTTP_400_BAD_REQUEST, detail=error_code)


def _channel_state(channel: Channel) -> dict[str, Any]:
    """What an audit record compares of a channel: its fields, config keys flattened."""
    state: dict[str, Any] = {
        "name": channel.name,
        "type": channel.type,
        "is_active": channel.is_active,
    }
    state.update({f"config.{key}": value for key, value in (channel.config or {}).items()})
    return state


def _channel_changes(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> dict[str, dict[str, Any]] | None:
    """The change to a channel, safe to store.

    A channel's config is where its credentials are (an app secret, a
    webhook URL with its token) next to values that are personal data (an
    email channel's recipients). No config value is recorded: the record
    says which keys were set or changed.
    """
    config_keys = [key for key in {**(before or {}), **after} if key.startswith("config.")]
    return diff_changes(before, after, secret_fields=config_keys)


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
    _require_valid_config(body.type, body.config or {})

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
    await record_resource(
        db,
        user,
        actions.CHANNEL_CREATE,
        "channel",
        ch,
        changes=_channel_changes(None, _channel_state(ch)),
    )
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
    before = _channel_state(channel)

    if body.name is not None:
        channel.name = body.name
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.config is not None:
        # Merge caller-provided keys on top of existing config (so a client
        # can PATCH just the chat_id without re-sending app_secret).
        merged = dict(channel.config or {})
        merged.update(body.config)
        _require_valid_config(channel.type, merged)
        channel.config = merged

    await record_resource(
        db,
        user,
        actions.CHANNEL_UPDATE,
        "channel",
        channel,
        changes=_channel_changes(before, _channel_state(channel)),
    )
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
    await record_resource(
        db, user, actions.CHANNEL_DELETE, "channel", channel, detail=f"type={channel.type}"
    )
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
    """Send a test message to the channel's own target (chat, webhook, recipients).

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
    # Read now: the session may not be asked for the row again after the send.
    recorded = {
        "org_id": channel.org_id,
        "id": channel.id,
        "name": channel.name,
        "type": channel.type,
    }

    # Send a plain notice — no interactive buttons.
    # The Approval Playground covers the rich-card round-trip; this
    # endpoint exists purely as a connectivity/credentials sanity
    # check, so users who don't want approval gates aren't confused by
    # Approve/Reject buttons on a "test" message.
    sender = user.email or user.username
    result = await adapter.send_notice(
        "FIM One test message",
        f"Sent by {sender} to check that this channel can deliver.",
    )
    # A message left the platform through the organization's channel. Nothing
    # was changed, so the record is written on its own and cannot fail the
    # test; the provider's error text is not kept (it can quote the webhook).
    await audit.record(
        actions.CHANNEL_TEST,
        actor=audit.actor_of(user),
        org_id=recorded["org_id"],
        target_type="channel",
        target_id=recorded["id"],
        target_label=recorded["name"],
        outcome="success" if result.ok else "failed",
        detail=f"type={recorded['type']}",
    )
    return ChannelTestResponse(ok=result.ok, error=result.error)


# ---------------------------------------------------------------------------
# Hook Playground — full approval-gate round-trip from the UI
# ---------------------------------------------------------------------------


# A playground card is answered within the session that sent it; the
# expiry sweep closes one that is not.
_TEST_APPROVAL_TTL_SECONDS = 24 * 3600
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
    """Simulate an approval round-trip through the real ``ConfirmationService``.

    Creates a real ``ConfirmationRequest`` row in ``pending`` state, sends
    the production approval card to the channel's chat, and returns the
    ``confirmation_id``.  The UI then polls ``GET /confirmations/{id}``
    until the status flips (operator clicked Approve/Reject in Feishu) or
    the caller gives up.

    Unlike ``/test`` which uses a sentinel ``test-*`` id that the callback
    handler ignores, this endpoint creates a genuine DB row — so the card
    buttons drive the same code path that a production hook would. The
    tester is the one person allowed to decide it.
    """
    channel = await _load_channel_or_404(db, channel_id)
    await require_org_member(channel.org_id, user, db)

    if not channel.is_active:
        return TestApprovalResponse(
            ok=False, error="Channel is disabled; enable it first."
        )

    adapter = build_channel(channel.type, dict(channel.config or {}))
    if not isinstance(adapter, ApprovalCarrier):
        return TestApprovalResponse(
            ok=False,
            error=(
                "Approval playground currently supports Feishu channels "
                "only."
            ),
        )

    tool_name = (body.tool_name or _DEFAULT_TEST_TOOL_NAME).strip()
    tool_args = body.tool_args if body.tool_args is not None else _DEFAULT_TEST_TOOL_ARGS
    title = (body.title or _DEFAULT_TEST_TITLE).strip()
    summary = (body.summary or _DEFAULT_TEST_SUMMARY).strip()

    spec = ConfirmationSpec(
        kind="confirmation",
        initiator=await resolve_principal(db, user_id=user.id, auth=AuthMethod.SESSION),
        binding=None,
        org_id=channel.org_id,
        title=title,
        description=summary,
        payload={
            "tool_name": tool_name,
            "tool_args": tool_args,
            "test_mode": True,
            "initiator": user.email or user.username,
        },
        eligible_scope="initiator",
        eligible_user_id=user.id,
        delivery="channel_only",
        channel_id=channel.id,
        timeout_seconds=_TEST_APPROVAL_TTL_SECONDS,
    )
    try:
        row = await _confirmation_service().request(spec)
    except AssemblyRefusal as refusal:
        # Send failures leave a row behind; close it so the UI stops polling.
        confirmation_id = (refusal.detail_args or {}).get("confirmation_id")
        if confirmation_id:
            await _expire_now(db, str(confirmation_id))
        return TestApprovalResponse(
            ok=False,
            confirmation_id=str(confirmation_id) if confirmation_id else None,
            error=refusal.detail,
        )

    return TestApprovalResponse(ok=True, confirmation_id=str(row.id))


def _confirmation_service() -> ConfirmationService:
    # Resolved per call: the session factory is the module attribute, which
    # tests point at their own engine.
    from fim_one.db import create_session

    return ConfirmationService(create_session)


async def _expire_now(db: AsyncSession, confirmation_id: str) -> None:
    row = (
        await db.execute(
            select(ConfirmationRequest).where(
                ConfirmationRequest.id == confirmation_id,
                ConfirmationRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = "expired"
        row.responded_at = datetime.now(UTC)
        await db.commit()


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

    The request goes to the Feishu API origin the channel itself would
    call: the stored channel's ``base_url`` in edit mode, the request's
    ``base_url`` in create mode (and in edit mode when the secret was
    re-typed and a ``base_url`` came with it), ``https://open.feishu.cn``
    when there is none. An origin the channel could not be saved with is
    refused with ``feishu_channel_base_url_not_allowed`` before anything is
    sent: the request carries the app secret.

    Returns a list of ``ChatInfo`` rows the caller can render into a
    picker UI.  Errors from Feishu (invalid credentials, network, etc.)
    surface as ``400`` with a human-readable ``detail`` message so the
    UI can show it inline.
    """
    # Resolve org + secret based on mode.
    app_id = body.app_id.strip()
    app_secret: str | None = (body.app_secret or "").strip() or None
    base_url: Any = (body.base_url or "").strip() or None

    if body.channel_id:
        channel = await _load_channel_or_404(db, body.channel_id)
        await require_org_admin(channel.org_id, user, db)
        stored_base_url = (channel.config or {}).get("base_url")
        if app_secret is None or base_url is None:
            # The stored secret only ever goes to the host it was saved for;
            # a re-typed one goes there too unless the caller names another.
            base_url = stored_base_url
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

    def _base_url_refused() -> AppError:
        return AppError(
            BASE_URL_NOT_ALLOWED,
            status_code=status.HTTP_400_BAD_REQUEST,
            # The picker shows ``detail`` as it is, so it is a sentence.
            detail=str(FeishuBaseUrlNotAllowed()),
        )

    if not base_url_allowed(base_url):
        raise _base_url_refused()
    adapter_config: dict[str, Any] = {"app_id": app_id, "app_secret": app_secret}
    if base_url:
        adapter_config["base_url"] = base_url
    adapter = FeishuChannel(adapter_config)
    try:
        raw_items = await adapter.list_chats()
    except FeishuBaseUrlNotAllowed as exc:
        raise _base_url_refused() from exc
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

    # 2. Decrypt (Feishu wraps pushes in an AES envelope when an Encrypt
    #    Key is configured), then dispatch to adapter's handle_callback().
    if not isinstance(parsed, dict):
        parsed = {}
    parsed = adapter.decrypt_callback(parsed)
    result = await adapter.handle_callback(parsed, headers)

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
    carrier_decision = (
        adapter.parse_decision(parsed) if isinstance(adapter, ApprovalCarrier) else None
    )
    if carrier_decision is not None:
        confirmation_id = carrier_decision.confirmation_id
        decision = carrier_decision.decision
        if confirmation_id and decision is not None:
            open_id = str(carrier_decision.actor) if carrier_decision.actor else None
            # The clicker must map to a FIM user who may decide this
            # request. Anyone else in the chat gets a toast, and the card
            # stays pending.
            response_body = await _decide_from_click(
                db,
                channel=channel,
                confirmation_id=confirmation_id,
                decision=decision,
                open_id=open_id,
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


async def _decide_from_click(
    db: AsyncSession,
    *,
    channel: Channel,
    confirmation_id: str,
    decision: Literal["approve", "reject"],
    open_id: str | None,
) -> dict[str, Any]:
    """Record an Approve / Reject click, or say why it does not count.

    The clicker is identified by the app-scoped Feishu ``open_id``; it maps
    to a FIM user via the ``feishu`` OAuth binding (which requires the login
    and channel integrations to share one Feishu app).  Unmappable clickers
    are denied — chat membership alone must never satisfy the rule, or any
    member of the target group could approve.  Who may decide is read off
    the row by ``ConfirmationService.may_decide``, the same rule the portal
    applies.  Only a request this channel carried can be decided here: the
    signature proves which organization's app is calling, not that the
    request was theirs.

    Returns the Feishu response body: a toast and, once the row is in a
    terminal state, the replacement card.
    """
    if not open_id:
        return _denied("Unable to identify who clicked this card.")

    try:
        actor: Principal | None = await resolve_external_actor(
            db, provider="feishu", subject=open_id
        )
    except AssemblyRefusal:
        return _denied("Your linked FIM One account is disabled.")
    if actor is None:
        return _denied(
            "Your Feishu account is not linked to FIM One — link it "
            "(or decide in the portal) to respond to approvals."
        )

    try:
        row = await _confirmation_service().decide(
            db,
            confirmation_id=confirmation_id,
            actor=actor,
            decision=decision,
            via_channel_id=channel.id,
            external_actor=open_id,
        )
    except AssemblyRefusal as refusal:
        if refusal.error_code == "confirmation_already_decided":
            # Duplicate or stale click: the card is replaced all the same
            # (the client may hold an old copy) and the toast says so.
            final_status = str((refusal.detail_args or {}).get("status") or "expired")
            return _build_card_action_response(
                final_status=final_status,
                newly_applied=False,
                confirmation_id=confirmation_id,
                tool_name=await _tool_name_of(db, confirmation_id),
                decided_by=open_id,
            )
        if refusal.error_code == "confirmation_not_found":
            # Row gone (deleted / swept). A benign toast keeps Feishu from
            # showing error 200340.
            return _denied("Confirmation request not found.")
        logger.warning(
            "channel_callback: click denied (channel=%s, confirmation=%s, open_id=%s): %s",
            channel.id,
            confirmation_id,
            open_id,
            refusal.detail,
        )
        return _denied(refusal.detail)

    payload = row.payload if isinstance(row.payload, dict) else {}
    raw_tool = payload.get("tool_name")
    return _build_card_action_response(
        final_status=str(row.status),
        newly_applied=True,
        confirmation_id=confirmation_id,
        tool_name=str(raw_tool) if raw_tool else None,
        decided_by=open_id,
    )


def _denied(reason: str) -> dict[str, Any]:
    """A toast only: the card stays as it is so an eligible approver can still decide."""
    return {"toast": {"type": "error", "content": reason}}


async def _tool_name_of(db: AsyncSession, confirmation_id: str) -> str | None:
    payload = (
        await db.execute(
            select(ConfirmationRequest.payload).where(ConfirmationRequest.id == confirmation_id)
        )
    ).scalar_one_or_none()
    raw_tool = payload.get("tool_name") if isinstance(payload, dict) else None
    return str(raw_tool) if raw_tool else None


__all__ = ["router"]
