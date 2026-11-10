"""Built-in tool for sending a notice to one of the caller's organization channels."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..base import BaseTool

logger = logging.getLogger(__name__)

# Prefix of the instance-wide channels the operator configured through
# environment variables (deprecated, see ``core.channels.instance``).
INSTANCE_PREFIX = "instance:"

# The list of channels is shown to the model; it is capped.
_MAX_LISTED = 50
_MAX_NAME_CHARS = 80


@dataclass(frozen=True)
class _Target:
    """One place the caller may send to."""

    key: str  # channel id, or ``instance:<provider>``
    name: str
    kind: str
    channel: Any  # a BaseChannel

    def describe(self) -> str:
        if self.key.startswith(INSTANCE_PREFIX):
            return f"- {self.key} ({self.kind}, instance-wide, deprecated)"
        return f"- {self.name[:_MAX_NAME_CHARS]} ({self.kind}, id {self.key})"


class SendNotificationTool(BaseTool):
    """Send a titled message to a channel of the caller's organization.

    The targets are the enabled channels of the organizations the acting
    user belongs to (Settings → Channels): Feishu apps, Lark / Slack / WeCom
    webhooks and email lists. A channel is always named explicitly; nothing
    is sent to "the first channel".

    For one more release the instance-wide webhooks the operator set in
    environment variables are also offered, as ``instance:slack`` /
    ``instance:lark`` / ``instance:wecom``.

    The tool is registered only when the operator sets
    ``NOTIFICATION_TOOL_ENABLED``, and is bound to the acting user when a
    run's tools are assembled; an instance bound to no user has only the
    instance-wide channels.
    """

    def __init__(
        self,
        *,
        user_id: str | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._user_id = user_id
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "send_notification"

    @property
    def display_name(self) -> str:
        return "Send Notification"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "Send a notification to one of your organization's messaging channels "
            "(a Feishu chat, a Lark / Slack / WeCom group webhook, or an email list). "
            "Parameters: channel (name or id of the channel; omit it to get the list "
            "of channels you can send to), title (notification title), "
            "body (notification body)."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": (
                        "Name or id of the channel to send to. Omit to list the "
                        "available channels without sending anything."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Notification title / subject.",
                },
                "body": {
                    "type": "string",
                    "description": "Notification body (plain text or markdown).",
                },
            },
            "required": ["title", "body"],
        }

    async def _targets(self) -> list[_Target]:
        """Every channel the acting user may send to, organization channels first."""
        from fim_one.core.channels import build_channel
        from fim_one.core.channels.instance import instance_webhooks
        from fim_one.core.channels.resolve import list_member_channels

        targets: list[_Target] = []
        if self._user_id:
            session_factory = self._session_factory
            if session_factory is None:
                from fim_one.db import create_session

                session_factory = create_session
            async with session_factory() as db:
                rows = await list_member_channels(db, user_id=self._user_id)
            for row in rows:
                channel = build_channel(row.type, dict(row.config or {}))
                if channel is not None:
                    targets.append(
                        _Target(key=row.id, name=row.name, kind=row.type, channel=channel)
                    )
        for provider, channel in instance_webhooks().items():
            key = f"{INSTANCE_PREFIX}{provider}"
            targets.append(_Target(key=key, name=key, kind=channel.type, channel=channel))
        return targets

    @staticmethod
    def _listing(targets: list[_Target]) -> str:
        if not targets:
            return (
                "No channels are available. An organization admin can add one "
                "under Settings → Channels."
            )
        lines = [target.describe() for target in targets[:_MAX_LISTED]]
        if len(targets) > _MAX_LISTED:
            lines.append(f"... and {len(targets) - _MAX_LISTED} more")
        return "Available channels:\n" + "\n".join(lines)

    async def run(self, **kwargs: Any) -> str:
        title = str(kwargs.get("title") or "").strip()
        body = str(kwargs.get("body") or "").strip()
        wanted = str(kwargs.get("channel") or "").strip()
        # The interface before channels were per organization named a
        # provider; its webhooks are the instance-wide channels now.
        legacy_provider = str(kwargs.get("provider") or "").strip().lower()
        if not wanted and legacy_provider in ("slack", "lark", "wecom"):
            wanted = f"{INSTANCE_PREFIX}{legacy_provider}"
        elif not wanted and legacy_provider == "email":
            return (
                "[Error] send_notification no longer takes an email address. "
                "Send to an email channel of your organization by its name, "
                "or use the email_send tool for an ad-hoc recipient."
            )

        targets = await self._targets()
        if not wanted:
            return self._listing(targets)
        if not title:
            return "[Error] 'title' is required."
        if not body:
            return "[Error] 'body' is required."

        matches = [t for t in targets if t.key == wanted]
        if not matches:
            matches = [t for t in targets if t.name.casefold() == wanted.casefold()]
        if not matches:
            return f"[Error] No channel named '{wanted[:_MAX_NAME_CHARS]}'. {self._listing(targets)}"
        if len(matches) > 1:
            return (
                f"[Error] {len(matches)} channels are named '{wanted[:_MAX_NAME_CHARS]}'; "
                f"pass the id of the one you mean. {self._listing(matches)}"
            )

        target = matches[0]
        if target.key.startswith(INSTANCE_PREFIX):
            logger.warning(
                "send_notification used the instance-wide channel %s, configured through "
                "a deprecated environment variable; recreate it as an organization channel",
                target.key,
            )
        result = await target.channel.send_notice(title, body)
        if result.ok:
            return f"Notification sent to channel '{target.name[:_MAX_NAME_CHARS]}'."
        return f"[Error] {result.error or 'Unknown error'}"
