"""Deterministic enforcement hooks that run outside the LLM loop.

This package complements :mod:`fim_one.core.agent.hooks` (which defines
the core ``HookPoint`` / ``Hook`` / ``HookRegistry`` machinery) with the
class-based hook bases and the inline-confirmation listener port.

The approval gate, the hook that turns a tool flagged
``requires_confirmation=True`` into a human-in-the-loop approval, lives in
:mod:`fim_one.core.assembly.confirmation` as ``ApprovalGateHook``. Its
former names are still importable from here (see ``feishu_gate_hook.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import PostToolUseHook, PreToolUseHook
from .inline_confirmation import (
    InlineConfirmationListener,
    emit_inline_confirmation,
    get_inline_confirmation_listener,
    set_inline_confirmation_listener,
)

if TYPE_CHECKING:
    from .feishu_gate_hook import FeishuGateHook, create_feishu_gate_hook

# Resolved on first access: the gate module imports this package for the
# hook base class, so importing it here at module level would be a cycle.
_FORMER_GATE_NAMES = ("FeishuGateHook", "create_feishu_gate_hook")


def __getattr__(name: str) -> Any:
    if name in _FORMER_GATE_NAMES:
        from . import feishu_gate_hook

        return getattr(feishu_gate_hook, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
