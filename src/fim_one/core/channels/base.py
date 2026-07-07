"""Abstract base class for outbound messaging channels."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelSendResult:
    """Standard outcome shape for a channel send/callback call."""

    ok: bool
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionSummary:
    """Channel-agnostic description of a finished agent run.

    Each channel subclass is responsible for rendering this into its
    native message / card / attachment format (Feishu v2.0 card, Slack
    Block Kit, DingTalk markdown, etc.).  Fields are raw — per-channel
    truncation and formatting happens in ``send_completion``.
    """

    agent_name: str
    duration_seconds: float
    tools_used: list[str]
    user_message: str
    final_answer: str
    conversation_id: str | None
    conversation_url: str | None


class BaseChannel(abc.ABC):
    """Platform-agnostic outbound messaging channel.

    Implementations wrap platform-specific SDKs / HTTP APIs.  The ``config``
    dict is whatever was stored in the ``channels.config`` row (decrypted
    by ``EncryptedJSON`` on read).  For Feishu this typically contains
    ``app_id``, ``app_secret``, ``chat_id``, plus optional
    ``verification_token`` / ``encrypt_key``.
    """

    type: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    # -- Outbound --

    @abc.abstractmethod
    async def send_message(self, payload: dict[str, Any]) -> ChannelSendResult:
        """Send a message payload.

        ``payload`` is platform-specific.  For Feishu::

            {"chat_id": "oc_xxx", "msg_type": "text", "content": "hello"}
            {"chat_id": "oc_xxx", "msg_type": "interactive", "card": {...}}
        """

    @abc.abstractmethod
    async def send_completion(
        self,
        summary: CompletionSummary,
    ) -> ChannelSendResult:
        """Render a task-completion notification in the channel's native
        format and send it.

        Each channel picks its own target (Feishu ``chat_id`` from
        config, Slack default channel, DingTalk webhook, etc.) — the
        abstraction stays semantic, not coupled to any one platform's
        addressing vocabulary.
        """

    # -- Inbound (callbacks) --

    def decrypt_callback(self, body: dict[str, Any]) -> dict[str, Any]:
        """Decrypt an encrypted callback envelope, if the platform uses one.

        Default: passthrough.  Platforms that wrap event pushes in an
        encrypted envelope (Feishu ``{"encrypt": ...}``) override this.
        """
        return body

    async def verify_signature(
        self,
        body: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Return True if the callback's signature headers are valid.

        Default: no verification.  Platforms that support signed callbacks
        (Feishu encrypt key, Slack HMAC) MUST override this.
        """
        return True

    @abc.abstractmethod
    async def handle_callback(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Process an incoming callback event.

        Returns the payload that should be sent back to the platform (for
        challenge/verification handshakes) merged with a normalized
        ``event`` dict describing what happened::

            {
                "response": {"challenge": "..."},  # what to echo back
                "event": {
                    "kind": "url_verification" | "card_action" | "unknown",
                    "action": "approve" | "reject" | None,
                    "confirmation_id": str | None,
                    "open_id": str | None,
                },
            }
        """
