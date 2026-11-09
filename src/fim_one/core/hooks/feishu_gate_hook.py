"""Former home of the approval gate. Kept for one release as a re-export.

The gate is :class:`fim_one.core.assembly.confirmation.ApprovalGateHook`;
it never was specific to one messaging platform. Import it from there.
"""

from __future__ import annotations

from fim_one.core.assembly.confirmation import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    RequiresConfirmationFn,
    SessionFactory,
    _default_requires_confirmation,
)
from fim_one.core.assembly.confirmation import ApprovalGateHook as FeishuGateHook
from fim_one.core.assembly.confirmation import (
    create_approval_gate_hook as create_feishu_gate_hook,
)

__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "FeishuGateHook",
    "RequiresConfirmationFn",
    "SessionFactory",
    "_default_requires_confirmation",
    "create_feishu_gate_hook",
]
