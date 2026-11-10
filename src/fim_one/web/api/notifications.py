"""Notification API endpoints for the instance-wide channels.

Provides routes for listing configured providers, sending test notifications,
and sending real notifications. Sending (test or real) is admin only.

Deprecated together with the environment variables behind them
(:mod:`fim_one.core.channels.instance`): channels belong to an organization
and are managed under ``/api/channels``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fim_one.core.audit import actions
from fim_one.core.audit import writer as audit
from fim_one.core.channels import ChannelSendResult
from fim_one.core.channels.instance import (
    available_instance_providers,
    instance_channel,
    instance_providers,
)
from fim_one.web.auth import get_current_admin, get_current_user
from fim_one.db.models.user import User
from fim_one.web.schemas.notification import (
    NotificationProviderInfo,
    NotificationSendRequest,
    NotificationTestRequest,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"], deprecated=True)


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


async def _record_send(admin: User, action: str, provider: str, ok: bool) -> None:
    """An administrator sent a message through an instance-wide webhook.

    Which provider and whether it went out. The message and the provider's
    answer are not recorded. Written on its own, after the send.
    """
    await audit.record(
        action,
        actor=audit.actor_of(admin),
        outcome="success" if ok else "failed",
        detail=f"provider={provider}",
    )


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
    await _record_send(user, actions.NOTIFICATIONS_INSTANCE_TEST, body.provider, result.ok)
    return _outcome(body.provider, result)


@router.post("/send")
async def send_notification(
    body: NotificationSendRequest,
    admin: User = Depends(get_current_admin),  # noqa: B008
) -> dict[str, object]:
    """Send a notification (admin only)."""
    channel = instance_channel(body.provider, recipient=body.channel)
    if channel is None:
        return {
            "ok": False,
            "error": (
                f"Notification provider '{body.provider}' is not configured. "
                f"Available: {', '.join(available_instance_providers()) or '(none)'}"
            ),
        }
    result = await channel.send_notice(body.title, body.body)
    await _record_send(admin, actions.NOTIFICATIONS_INSTANCE_SEND, body.provider, result.ok)
    return _outcome(body.provider, result)
