"""Pydantic request/response schemas for the Channel API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=r"^(feishu|lark_webhook|slack_webhook|wecom_webhook|email)$")
    org_id: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


# Config fields considered sensitive — masked in API responses.
_SENSITIVE_KEYS = frozenset(
    # A webhook URL carries the token that authorizes posting to the chat.
    {"app_secret", "verification_token", "encrypt_key", "api_secret", "webhook_url"}
)


def _mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``config`` with sensitive values masked."""
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if key in _SENSITIVE_KEYS and value:
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str
    org_id: str
    config: dict[str, Any]
    is_active: bool
    created_by: str
    created_at: str
    updated_at: str | None = None
    callback_url: str | None = None

    @classmethod
    def from_orm_masked(
        cls,
        channel: Any,
        *,
        callback_url: str | None = None,
    ) -> ChannelResponse:
        """Build a response with ``config`` sensitive fields masked."""
        return cls(
            id=channel.id,
            name=channel.name,
            type=channel.type,
            org_id=channel.org_id,
            config=_mask_config(dict(channel.config or {})),
            is_active=channel.is_active,
            created_by=channel.created_by,
            created_at=(
                channel.created_at.isoformat() if channel.created_at else ""
            ),
            updated_at=(
                channel.updated_at.isoformat() if channel.updated_at else None
            ),
            callback_url=callback_url,
        )


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]


class ChannelTestResponse(BaseModel):
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Hook Playground — test the real approval-gate flow end-to-end
# ---------------------------------------------------------------------------


class TestApprovalRequest(BaseModel):
    """Body for ``POST /api/channels/{id}/test-approval``.

    All fields are optional — the endpoint fills sensible defaults modeled
    on a realistic "sensitive tool call" so demos look natural out of the
    box.
    """

    tool_name: str | None = Field(
        default=None,
        max_length=200,
        description="Simulated tool name shown on the approval card.",
    )
    tool_args: dict[str, Any] | None = Field(
        default=None,
        description="Simulated tool arguments (rendered as pretty JSON).",
    )
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)


class TestApprovalResponse(BaseModel):
    ok: bool
    confirmation_id: str | None = None
    error: str | None = None


class ConfirmationStatusResponse(BaseModel):
    """Current state of a ``ConfirmationRequest`` row — polled by the UI."""

    id: str
    status: str  # pending | approved | rejected | expired
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    test_mode: bool = False
    created_at: str
    responded_at: str | None = None
    responded_by_open_id: str | None = None


# ---------------------------------------------------------------------------
# Chat discovery (Feishu group picker)
# ---------------------------------------------------------------------------


class ChatDiscoveryRequest(BaseModel):
    """Request to list Feishu groups that the app/bot is a member of.

    The caller passes ``app_id`` + ``app_secret`` when creating a channel
    (no DB row exists yet).  When editing an existing channel, the caller
    may pass ``channel_id`` alone — the server will decrypt the stored
    ``app_secret`` to query Feishu.  If both ``app_secret`` and
    ``channel_id`` are present, ``app_secret`` wins (user explicitly
    re-entered it).

    ``base_url`` is the Feishu API origin to ask, for a privately deployed
    Feishu; empty means ``https://open.feishu.cn``. It follows the secret:
    with ``channel_id`` and no ``app_secret`` the stored channel's own
    ``base_url`` is used and this field is ignored, so a stored secret is
    only ever sent to the host it was saved for. Any value must be one the
    channel itself could be saved with (an official host or one listed in
    ``FEISHU_ALLOWED_BASE_URLS``).
    """

    app_id: str = Field(min_length=1)
    app_secret: str | None = None
    channel_id: str | None = None
    org_id: str | None = None
    base_url: str | None = None


class ChatInfo(BaseModel):
    chat_id: str
    name: str
    avatar: str | None = None
    description: str | None = None
    member_count: int | None = None
    external: bool = False


class ChatDiscoveryResponse(BaseModel):
    items: list[ChatInfo]


__all__ = [
    "ChannelCreate",
    "ChannelUpdate",
    "ChannelResponse",
    "ChannelListResponse",
    "ChannelTestResponse",
    "TestApprovalRequest",
    "TestApprovalResponse",
    "ConfirmationStatusResponse",
    "ChatDiscoveryRequest",
    "ChatInfo",
    "ChatDiscoveryResponse",
]
