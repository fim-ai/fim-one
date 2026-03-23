"""Notification API endpoints.

Provides routes for listing configured providers, sending test notifications,
and sending real notifications (admin only).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fim_one.core.notification import (
    NotificationMessage,
    get_notification_registry,
)
from fim_one.core.notification.email_provider import EmailNotificationProvider
from fim_one.core.notification.lark_provider import LarkNotificationProvider
from fim_one.core.notification.slack_provider import SlackNotificationProvider
from fim_one.core.notification.wecom_provider import WeComNotificationProvider
from fim_one.web.auth import get_current_admin, get_current_user
from fim_one.web.models.user import User
from fim_one.web.schemas.notification import (
    NotificationProviderInfo,
    NotificationSendRequest,
    NotificationTestRequest,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# All known provider classes (for showing unconfigured ones too)
_ALL_PROVIDERS = [
    EmailNotificationProvider(),
    SlackNotificationProvider(),
    LarkNotificationProvider(),
    WeComNotificationProvider(),
]


@router.get("/providers")
async def list_providers(
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[NotificationProviderInfo]:
    """List all notification providers with their configuration status."""
    result: list[NotificationProviderInfo] = []
    for provider in _ALL_PROVIDERS:
        result.append(
            NotificationProviderInfo(
                name=provider.name,
                display_name=provider.display_name,
                description=provider.description,
                configured=provider.validate_config(),
            )
        )
    return result


@router.post("/test")
async def test_notification(
    body: NotificationTestRequest,
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, object]:
    """Send a test notification through the specified provider."""
    registry = get_notification_registry()
    provider = registry.get(body.provider)
    if provider is None:
        # Check if provider name is valid but unconfigured
        known_names = {p.name for p in _ALL_PROVIDERS}
        if body.provider in known_names:
            return {
                "ok": False,
                "error": f"Provider '{body.provider}' is not configured. "
                         "Set the required environment variables and restart.",
            }
        return {"ok": False, "error": f"Unknown provider: '{body.provider}'."}

    test_message = NotificationMessage(
        title="FIM One Test Notification",
        body=(
            f"This is a test notification from FIM One.\n\n"
            f"Sent by: {user.email or user.username}\n"
            f"Provider: {provider.display_name}"
        ),
        channel=body.channel,
    )
    return await provider.send(test_message)


@router.post("/send")
async def send_notification(
    body: NotificationSendRequest,
    _admin: User = Depends(get_current_admin),  # noqa: B008
) -> dict[str, object]:
    """Send a notification (admin only)."""
    registry = get_notification_registry()
    message = NotificationMessage(
        title=body.title,
        body=body.body,
        channel=body.channel,
    )
    try:
        return await registry.send(body.provider, message)
    except KeyError as exc:
        return {"ok": False, "error": str(exc)}
