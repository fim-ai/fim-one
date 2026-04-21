"""Deterministic enforcement hooks that run outside the LLM loop.

This package complements :mod:`fim_one.core.agent.hooks` (which defines
the core ``HookPoint`` / ``Hook`` / ``HookRegistry`` machinery) with
higher-level, integration-aware hook implementations.

The first hook shipped here is :class:`FeishuGateHook` — a PRE_TOOL_USE
hook that turns any tool flagged ``requires_confirmation=True`` into a
human-in-the-loop approval.  Since v0.9 it routes per-agent between two
delivery modes:

1. **channel** — creates a ``ConfirmationRequest(mode="channel")`` row
   and posts an interactive Approve / Reject card to the org's active
   Feishu channel (group chat).
2. **inline** — creates a ``ConfirmationRequest(mode="inline")`` row and
   fires an SSE-bound listener so the frontend can surface the request
   without touching any messaging channel.

Either path blocks (polls the DB row) until the status flips or the
timeout elapses.  Approved → tool runs; rejected / expired → blocked.
"""

from __future__ import annotations

from .base import PostToolUseHook, PreToolUseHook
from .feishu_gate_hook import FeishuGateHook, create_feishu_gate_hook
from .inline_confirmation import (
    InlineConfirmationListener,
    emit_inline_confirmation,
    get_inline_confirmation_listener,
    set_inline_confirmation_listener,
)

__all__ = [
    "PreToolUseHook",
    "PostToolUseHook",
    "FeishuGateHook",
    "create_feishu_gate_hook",
    "InlineConfirmationListener",
    "emit_inline_confirmation",
    "get_inline_confirmation_listener",
    "set_inline_confirmation_listener",
]
