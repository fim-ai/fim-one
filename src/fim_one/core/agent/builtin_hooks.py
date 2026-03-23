"""Built-in hooks for the Agent Hook System.

These hooks provide common enforcement behaviours that can be enabled
per-agent via the ``model_config_json.hooks.builtin`` list.

Available hooks:
- ``connector_call_logger``: POST — logs every connector call.
- ``result_truncator``:      POST — truncates oversized tool results.
- ``rate_limiter``:          PRE  — rate-limits per-connector call frequency.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from .hooks import Hook, HookContext, HookPoint, HookRegistry, HookResult

logger = logging.getLogger(__name__)

# ---- Result Truncation Hook ------------------------------------------------

MAX_RESULT_LENGTH = 8000


async def _result_truncator_handler(ctx: HookContext) -> HookResult:
    """Truncate tool results that exceed ``MAX_RESULT_LENGTH`` characters.

    This prevents extremely large tool outputs from consuming excessive
    context window budget in subsequent LLM calls.
    """
    result = ctx.tool_result
    if result is None:
        return HookResult()

    if len(result) <= MAX_RESULT_LENGTH:
        return HookResult()

    original_len = len(result)
    truncated = result[:MAX_RESULT_LENGTH] + (
        f"\n\n[truncated -- full output: {original_len} chars]"
    )
    return HookResult(
        modified_result=truncated,
        side_effects=[
            f"Truncated result from {original_len} to {MAX_RESULT_LENGTH} chars"
        ],
    )


def create_result_truncator(
    *,
    priority: int = 100,
    tool_filter: str | None = None,
) -> Hook:
    """Create a result truncation hook.

    Args:
        priority: Execution priority (default 100 — runs late so other
            POST hooks see the full result).
        tool_filter: Optional glob pattern to limit which tools are
            truncated.

    Returns:
        A configured ``Hook`` instance.
    """
    return Hook(
        name="result_truncator",
        hook_point=HookPoint.POST_TOOL_USE,
        handler=_result_truncator_handler,
        description=(
            "Auto-truncates tool results exceeding "
            f"{MAX_RESULT_LENGTH} characters to conserve context budget."
        ),
        priority=priority,
        tool_filter=tool_filter,
    )


# ---- Connector Call Logger Hook -------------------------------------------


async def _connector_call_logger_handler(ctx: HookContext) -> HookResult:
    """Log every connector tool call.

    This hook fires on POST_TOOL_USE for connector tools (those whose
    names contain ``"__"``).  It emits a structured log entry and records
    the call in the side_effects list so the caller can persist it if
    desired.
    """
    tool_name = ctx.tool_name or ""

    # Only log connector tools (format: connector__action).
    if "__" not in tool_name:
        return HookResult()

    connector_name, _, action_name = tool_name.partition("__")
    log_entry = {
        "connector": connector_name,
        "action": action_name,
        "tool_name": tool_name,
        "agent_id": ctx.agent_id,
        "user_id": ctx.user_id,
        "conversation_id": ctx.conversation_id,
        "has_result": ctx.tool_result is not None,
        "result_length": len(ctx.tool_result) if ctx.tool_result else 0,
    }

    logger.info("Connector call logged: %s", log_entry)

    return HookResult(
        side_effects=[
            f"Logged connector call: {connector_name}.{action_name}"
        ],
    )


def create_connector_call_logger(
    *,
    priority: int = 0,
) -> Hook:
    """Create a connector call logger hook.

    Args:
        priority: Execution priority (default 0 — runs first among POST
            hooks so the log captures the original result).

    Returns:
        A configured ``Hook`` instance.
    """
    return Hook(
        name="connector_call_logger",
        hook_point=HookPoint.POST_TOOL_USE,
        handler=_connector_call_logger_handler,
        description="Logs every connector tool call with metadata for auditing.",
        priority=priority,
        tool_filter="*__*",  # Only connector tools (format: connector__action).
    )


# ---- Rate Limiter Hook ----------------------------------------------------

# Per-connector call counters: tool_name -> list of timestamps.
_rate_limit_calls: dict[str, list[float]] = defaultdict(list)

# Rate limit: max calls per connector per minute.
RATE_LIMIT_MAX_CALLS = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0


def _cleanup_old_calls(tool_name: str, now: float) -> None:
    """Remove call timestamps older than the rate limit window."""
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    calls = _rate_limit_calls[tool_name]
    _rate_limit_calls[tool_name] = [t for t in calls if t > cutoff]


async def _rate_limiter_handler(ctx: HookContext) -> HookResult:
    """Rate-limit connector tool calls to prevent abuse.

    Enforces a maximum of ``RATE_LIMIT_MAX_CALLS`` calls per connector
    tool per ``RATE_LIMIT_WINDOW_SECONDS`` window.
    """
    tool_name = ctx.tool_name or ""

    # Only rate-limit connector tools.
    if "__" not in tool_name:
        return HookResult()

    now = time.monotonic()
    _cleanup_old_calls(tool_name, now)

    calls = _rate_limit_calls[tool_name]
    if len(calls) >= RATE_LIMIT_MAX_CALLS:
        remaining_seconds = int(
            RATE_LIMIT_WINDOW_SECONDS - (now - calls[0])
        )
        return HookResult(
            allow=False,
            error=(
                f"Rate limit exceeded for '{tool_name}': "
                f"max {RATE_LIMIT_MAX_CALLS} calls per "
                f"{int(RATE_LIMIT_WINDOW_SECONDS)}s. "
                f"Try again in {remaining_seconds}s."
            ),
            side_effects=[
                f"Rate limit blocked call to {tool_name} "
                f"({len(calls)}/{RATE_LIMIT_MAX_CALLS} in window)"
            ],
        )

    # Record this call.
    calls.append(now)

    return HookResult(
        side_effects=[
            f"Rate limiter: {len(calls)}/{RATE_LIMIT_MAX_CALLS} calls "
            f"in window for {tool_name}"
        ],
    )


def reset_rate_limits() -> None:
    """Clear all rate limit counters (useful for testing)."""
    _rate_limit_calls.clear()


def create_rate_limiter(
    *,
    priority: int = 0,
) -> Hook:
    """Create a rate limiter hook.

    Args:
        priority: Execution priority (default 0 — runs first among PRE
            hooks to block calls before any other processing).

    Returns:
        A configured ``Hook`` instance.
    """
    return Hook(
        name="rate_limiter",
        hook_point=HookPoint.PRE_TOOL_USE,
        handler=_rate_limiter_handler,
        description=(
            f"Rate-limits connector calls to max {RATE_LIMIT_MAX_CALLS} "
            f"per {int(RATE_LIMIT_WINDOW_SECONDS)}s per tool."
        ),
        priority=priority,
        tool_filter="*__*",  # Only connector tools.
    )


# ---- Hook Factory ---------------------------------------------------------

# Registry of all built-in hook factory functions.
BUILTIN_HOOKS: dict[str, dict[str, Any]] = {
    "connector_call_logger": {
        "factory": create_connector_call_logger,
        "description": "Logs every connector tool call with metadata for auditing.",
        "hook_point": HookPoint.POST_TOOL_USE.value,
    },
    "result_truncator": {
        "factory": create_result_truncator,
        "description": (
            f"Auto-truncates tool results exceeding "
            f"{MAX_RESULT_LENGTH} characters to conserve context budget."
        ),
        "hook_point": HookPoint.POST_TOOL_USE.value,
    },
    "rate_limiter": {
        "factory": create_rate_limiter,
        "description": (
            f"Rate-limits connector calls to max {RATE_LIMIT_MAX_CALLS} "
            f"per {int(RATE_LIMIT_WINDOW_SECONDS)}s per tool."
        ),
        "hook_point": HookPoint.PRE_TOOL_USE.value,
    },
}


def create_builtin_hook(name: str) -> Hook | None:
    """Create a built-in hook by name.

    Args:
        name: One of the keys in ``BUILTIN_HOOKS``.

    Returns:
        A configured ``Hook`` instance, or ``None`` if the name is unknown.
    """
    entry = BUILTIN_HOOKS.get(name)
    if entry is None:
        return None
    factory = entry["factory"]
    result: Hook = factory()
    return result


def build_hook_registry_from_config(
    hook_config: dict[str, Any] | None,
) -> HookRegistry | None:
    """Build a ``HookRegistry`` from an agent's hook configuration.

    The ``hook_config`` dict is expected to have the shape::

        {
            "builtin": ["connector_call_logger", "result_truncator", ...],
            "custom": []  // future
        }

    Args:
        hook_config: The ``hooks`` dict from ``model_config_json``, or
            ``None`` if no hooks are configured.

    Returns:
        A populated ``HookRegistry``, or ``None`` if no valid hooks were
        found.
    """
    from .hooks import HookRegistry

    if not hook_config:
        return None

    builtin_names: list[str] = hook_config.get("builtin", [])
    if not isinstance(builtin_names, list):
        return None

    registry = HookRegistry()
    registered_any = False

    for name in builtin_names:
        if not isinstance(name, str):
            continue
        hook = create_builtin_hook(name)
        if hook is not None:
            registry.register(hook)
            registered_any = True
        else:
            logger.warning("Unknown builtin hook name: %r — skipping", name)

    return registry if registered_any else None
