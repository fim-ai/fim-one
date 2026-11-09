"""Channel abstraction layer for outbound messaging integrations.

A Channel is a platform-specific adapter that can ``send_message()`` to an
IM system (Feishu / WeCom / Slack / ...) and ``handle_callback()`` for
interactive events (button clicks, form submissions).

Channels are registered in :mod:`registry` by their ``type`` string (e.g.
``"feishu"``) so the API layer can look up the right class from a row in
the ``channels`` table.
"""

from __future__ import annotations

from .base import (
    ApprovalCarrier,
    ApprovalRequestSummary,
    BaseChannel,
    CarrierDecision,
    ChannelSendResult,
    CompletionSummary,
)
from .feishu import FeishuChannel
from .registry import CHANNEL_TYPES, get_channel_class, build_channel

__all__ = [
    "ApprovalCarrier",
    "ApprovalRequestSummary",
    "BaseChannel",
    "CarrierDecision",
    "ChannelSendResult",
    "CompletionSummary",
    "FeishuChannel",
    "CHANNEL_TYPES",
    "get_channel_class",
    "build_channel",
]
