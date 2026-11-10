"""Notification API endpoints.

Provides routes for listing configured providers, sending test notifications,
and sending real notifications. Sending (test or real) is admin only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fim_one.core.channels import ChannelSendResult
from fim_one.core.channels.instance import instance_channel, instance_providers
from fim_one.web.auth import get_current_admin, get_current_user
from fim_one.db.models.user import User
from fim_one.web.schemas.notification import (
    NotificationProviderInfo,
    NotificationSendRequest,
    NotificationTestRequest,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _outcome(provider: str, result: ChannelSendResult) -> dict[str, object]:
    """The response body of a send: ``ok`` plus the provider or the error."""
    if not result.ok:
        return {"ok": False, "error": result.error or "Unknown error"}
    return {"ok": True, "provider": provider, **result.raw}


@router.get("/providers")
async def list_providers(
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[NotificationProviderInfo]:
    """List all notification providers with their configuration status."""
    return [
        NotificationProviderInfo(
            name=provider.name,
            display_name=provider.display_name,
            description=provider.description,
            configured=provider.configured,
        )
        for provider in instance_providers()
    ]


@router.post("/test")
async def test_notification(
    body: NotificationTestRequest,
    user: User = Depends(get_current_admin),  # noqa: B008
) -> dict[str, object]:
    """Send a test notification through the specified provider (admin only).

    The providers post to instance-wide channels, so a regular user must not
    be able to trigger them.
    """
    known = {provider.name: provider for provider in instance_providers()}
    channel = instance_channel(body.provider, recipient=body.channel)
    if channel is None:
        # Check if provider name is valid but unconfigured
        if body.provider in known:
            return {
                "ok": False,
                "error": f"Provider '{body.provider}' is not configured. "
                         "Set the required environment variables and restart.",
            }
        return {"ok": False, "error": f"Unknown provider: '{body.provider}'."}

    result = await channel.send_notice(
        "FIM One Test Notification",
        (
            f"This is a test notification from FIM One.\n\n"
            f"Sent by: {user.email or user.username}\n"
            f"Provider: {known[body.provider].display_name}"
        ),
    )
    return _outcome(body.provider, result)


@router.post("/send")
async def send_notification(
    body: NotificationSendRequest,
    _admin: User = Depends(get_current_admin),  # noqa: B008
) -> dict[str, object]:
    """Send a notification (admin only)."""
    channel = instance_channel(body.provider, recipient=body.channel)
    if channel is None:
        available = [p.name for p in instance_providers() if p.configured]
        return {
            "ok": False,
            "error": (
                f"Notification provider '{body.provider}' is not configured. "
                f"Available: {', '.join(available) or '(none)'}"
            ),
        }
    return _outcome(body.provider, await channel.send_notice(body.title, body.body))
