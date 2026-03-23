"""Notification provider registry with auto-discovery from environment."""

from __future__ import annotations

import logging
from typing import Any

from .base import NotificationMessage, NotificationProvider

logger = logging.getLogger(__name__)


class NotificationRegistry:
    """Manages available notification providers.

    Providers are registered by name and can be queried or invoked
    individually.  The class method :meth:`from_env` creates a registry
    pre-populated with every provider whose configuration is valid.
    """

    def __init__(self) -> None:
        self._providers: dict[str, NotificationProvider] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, name: str, provider: NotificationProvider) -> None:
        """Register a provider under *name*."""
        self._providers[name] = provider

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, name: str) -> NotificationProvider | None:
        """Return the provider registered under *name*, or ``None``."""
        return self._providers.get(name)

    def list_available(self) -> list[str]:
        """Return the names of all registered providers."""
        return list(self._providers.keys())

    def provider_info(self) -> list[dict[str, Any]]:
        """Return metadata about each registered provider."""
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "description": p.description,
                "configured": True,
            }
            for p in self._providers.values()
        ]

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(self, provider_name: str, message: NotificationMessage) -> dict[str, Any]:
        """Send a message through the named provider.

        Returns:
            Provider response dict (always contains ``ok`` boolean).

        Raises:
            KeyError: If *provider_name* is not registered.
        """
        provider = self._providers.get(provider_name)
        if provider is None:
            raise KeyError(
                f"Notification provider '{provider_name}' is not configured. "
                f"Available: {', '.join(self._providers) or '(none)'}"
            )
        return await provider.send(message)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> NotificationRegistry:
        """Auto-discover configured providers from environment variables.

        Each provider class is instantiated and its ``validate_config()``
        is called.  Only providers with valid configuration are registered.
        """
        from .email_provider import EmailNotificationProvider
        from .lark_provider import LarkNotificationProvider
        from .slack_provider import SlackNotificationProvider
        from .wecom_provider import WeComNotificationProvider

        registry = cls()

        candidates: list[NotificationProvider] = [
            EmailNotificationProvider(),
            SlackNotificationProvider(),
            LarkNotificationProvider(),
            WeComNotificationProvider(),
        ]

        for provider in candidates:
            try:
                if provider.validate_config():
                    registry.register(provider.name, provider)
                    logger.debug(
                        "Notification provider '%s' registered", provider.name
                    )
            except Exception:
                logger.warning(
                    "Failed to validate notification provider '%s'",
                    provider.name,
                    exc_info=True,
                )

        return registry


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, thread-safe enough for async context)
# ---------------------------------------------------------------------------

_default_registry: NotificationRegistry | None = None


def get_notification_registry() -> NotificationRegistry:
    """Return (or create) the default notification registry.

    The registry is built once from env vars and cached for the process
    lifetime.  Call ``reset_notification_registry()`` if env vars change
    at runtime (e.g. during tests).
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = NotificationRegistry.from_env()
    return _default_registry


def reset_notification_registry() -> None:
    """Clear the cached default registry so it is re-built on next access."""
    global _default_registry
    _default_registry = None
