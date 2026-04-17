"""Deterministic enforcement hooks that run outside the LLM loop.

This package complements :mod:`fim_one.core.agent.hooks` (which defines
the core ``HookPoint`` / ``Hook`` / ``HookRegistry`` machinery) with
higher-level, integration-aware hook implementations.

The initial hook shipped here is :class:`FeishuGateHook` — a PRE_TOOL_USE
hook that turns any tool flagged ``requires_confirmation=True`` into a
Feishu-mediated human-in-the-loop approval.  The hook:

1. Creates a ``ConfirmationRequest`` DB row in ``pending`` state.
2. Sends an interactive card to the org's active Feishu channel (group
   chat).  Any chat member can press Approve / Reject.
3. Blocks (polls the DB row) until the status flips or the timeout
   elapses.  Approved → tool runs; rejected / expired → blocked.
"""

from __future__ import annotations

from .base import PostToolUseHook, PreToolUseHook
from .feishu_gate_hook import FeishuGateHook, create_feishu_gate_hook

__all__ = [
    "PreToolUseHook",
    "PostToolUseHook",
    "FeishuGateHook",
    "create_feishu_gate_hook",
]
