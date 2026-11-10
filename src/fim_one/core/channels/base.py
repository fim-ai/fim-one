"""Abstract base class for outbound messaging channels."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


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


@dataclass
class ApprovalRequestSummary:
    """Channel-agnostic description of something a human is asked to approve.

    A carrier renders it in its native interactive format (for Feishu, a
    card with Approve / Reject buttons) and makes sure the click comes back
    carrying ``confirmation_id``.
    """

    confirmation_id: str
    title: str
    summary: str
    tool_name: str
    tool_args_preview: str
    # One line telling readers of the message who is allowed to decide.
    approver_hint: str | None = None


@dataclass(frozen=True)
class CarrierDecision:
    """An approve / reject click as the carrier's platform reported it.

    ``decision`` is ``None`` when the click carried no recognisable choice,
    ``confirmation_id`` when it named no request. ``actor`` is the clicker's
    id on the platform (a Feishu ``open_id``); mapping it to a local user is
    not the carrier's job.
    """

    confirmation_id: str | None
    decision: Literal["approve", "reject"] | None
    actor: str | None


@runtime_checkable
class ApprovalCarrier(Protocol):
    """A channel that can put an approval before people and report their answer.

    The approval gate asks for this capability, not for a channel type: a
    channel that implements these three methods can carry approvals.
    """

    def approval_target(self) -> str | None:
        """Where approval requests go (a chat id); ``None`` when not configured."""

    async def send_approval(
        self, chat_target: str, request: ApprovalRequestSummary
    ) -> ChannelSendResult:
        """Post *request* to *chat_target* with a way to approve or reject it."""

    def parse_decision(self, body: dict[str, Any]) -> CarrierDecision | None:
        """The decision a (decrypted) callback body carries; ``None`` if it is not one."""


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

    @classmethod
    def config_error(cls, config: dict[str, Any]) -> str | None:
        """The error code of what is wrong with *config*; ``None`` when it is usable.

        Checked before a channel row is saved, so a channel that could never
        deliver is refused up front.
        """
        return None

    # -- Outbound --

    @abc.abstractmethod
    async def send_message(self, payload: dict[str, Any]) -> ChannelSendResult:
        """Send a message payload.

        ``payload`` is platform-specific.  For Feishu::

            {"chat_id": "oc_xxx", "msg_type": "text", "content": "hello"}
            {"chat_id": "oc_xxx", "msg_type": "interactive", "card": {...}}
        """

    @abc.abstractmethod
    async def send_notice(self, title: str, body: str) -> ChannelSendResult:
        """Send a short titled message to the channel's own target.

        The channel renders *title* and *body* in its native format (a
        card, a markdown message, an email with a subject).
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


# ---------------------------------------------------------------------------
# Rendering helpers shared by the channel types
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """Human-friendly duration string for a completion notice.

    Examples:
        ``0.4`` -> ``"0.4s"``
        ``2.345`` -> ``"2.3s"``
        ``107`` -> ``"1m 47s"``
        ``3725`` -> ``"1h 2m 5s"``
    """
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, appending an ellipsis on overflow."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    # Chop on a line boundary if one is nearby — keeps the preview readable
    # for answers that begin with a header / first paragraph.
    head = text[:limit]
    last_nl = head.rfind("\n")
    if last_nl >= limit - 80:
        head = head[:last_nl]
    return head.rstrip() + "\n\n…"


def format_tools(tools: list[str]) -> str:
    """Join ``tools`` with commas, truncating to 6 with an ellipsis."""
    if not tools:
        return "—"
    seen: set[str] = set()
    unique: list[str] = []
    for t in tools:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    if len(unique) <= 6:
        return ", ".join(unique)
    return ", ".join(unique[:6]) + ", …"


def completion_notice(summary: CompletionSummary) -> tuple[str, str]:
    """``(title, markdown body)`` of a finished run, for channels without cards.

    Same content and limits as the Feishu completion card: the user message
    is cut at 200 characters and the final answer at 600.
    """
    title = f"{summary.agent_name or 'Agent'} — Task complete"[:100]
    lines = [
        f"**Duration**: {format_duration(summary.duration_seconds)}",
        f"**Tools**: {format_tools(summary.tools_used)}",
    ]
    if summary.conversation_url:
        lines.append(f"**Conversation**: {summary.conversation_url}")
    elif summary.conversation_id:
        lines.append(f"**Conversation**: {summary.conversation_id[:8]}…")
    lines.append("")
    lines.append(f"**User message**\n{truncate(summary.user_message or '', 200)}")
    lines.append("")
    lines.append(f"**Final answer**\n{truncate(summary.final_answer or '', 600)}")
    return title, "\n".join(lines)
