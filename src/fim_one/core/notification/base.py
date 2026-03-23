"""Base classes for the notification system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotificationMessage:
    """A notification message to be sent through a provider.

    Attributes:
        title: Message title / subject line.
        body: Message body (plain text or HTML depending on provider).
        channel: Optional target channel, group, or email address.
                 Meaning varies by provider (email: recipient address,
                 Slack/Lark/WeCom: ignored for webhook-based delivery).
        metadata: Optional provider-specific extra fields.
    """

    title: str
    body: str
    channel: str | None = None
    metadata: dict[str, Any] | None = field(default_factory=dict)


class NotificationProvider(ABC):
    """Base class for notification providers.

    Each provider wraps a specific delivery channel (SMTP, Slack webhook,
    Lark webhook, WeCom webhook).  Subclasses implement ``send()`` for
    actual delivery and ``validate_config()`` to verify that all required
    env vars / settings are present.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique short identifier for this provider (e.g. ``email``, ``slack``)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-friendly display name."""
        ...

    @property
    def description(self) -> str:
        """One-line description shown in the provider list."""
        return f"Send notifications via {self.display_name}."

    @abstractmethod
    async def send(self, message: NotificationMessage) -> dict[str, Any]:
        """Send a notification.

        Returns:
            A dict with provider-specific response data.
            Must include ``{"ok": True}`` on success or
            ``{"ok": False, "error": "..."}`` on failure.
        """
        ...

    @abstractmethod
    def validate_config(self) -> bool:
        """Return ``True`` if all required configuration is present."""
        ...
