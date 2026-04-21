"""Bootstrap :class:`HookRegistry` instances for live agent requests.

This module is the glue between the agent ORM row's ``model_config_json``
and the runtime :class:`fim_one.core.agent.hooks.HookRegistry`.  The web
layer (``chat.py``, ``eval_runs.py``, DAG start-up paths, ...) calls
:func:`build_hook_registry_for_agent` immediately after loading an agent
ORM row, then passes the returned registry into :class:`ReActAgent` via
``hook_registry=``.

Contract recap (from ``docs/advanced/hook-system.mdx`` &
commit ``f76d1e38``): an agent may declare hooks in its
``model_config_json`` like::

    {
      "hooks": {
        "class_hooks": ["feishu_gate"]
      }
    }

Each name is resolved against :data:`_HOOK_FACTORIES`.  Unknown names are
skipped with a warning — a single stale entry must NEVER break chat.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from fim_one.core.agent.hooks import Hook, HookRegistry
from fim_one.core.hooks import FeishuGateHook, create_feishu_gate_hook

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@runtime_checkable
class _AgentLike(Protocol):
    """Structural subset of :class:`fim_one.web.models.agent.Agent`.

    We accept anything with ``model_config_json`` so callers that already
    hold a lightweight dict (e.g. :func:`_resolve_agent_config` in
    ``chat.py``) can adapt without loading the full ORM row.
    """

    model_config_json: Any  # pragma: no cover - protocol attribute


# ``session_factory`` must be a zero-arg callable that returns a *fresh*
# ``AsyncSession`` whose lifetime the hook owns (it wraps ``async with``
# around each poll).  Use :func:`fim_one.db.create_session` at call sites
# — NOT the per-request dependency-injected session, which closes when
# the request returns.
SessionFactory = Callable[[], Any]  # AsyncSession (avoid import cycle at type time)

# A factory converts a session_factory + hook-specific kwargs into a
# registered :class:`Hook`.  All factories share the same signature so
# the bootstrap logic can call them uniformly.
HookFactoryFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Built-in hook factories
# ---------------------------------------------------------------------------


def _build_feishu_gate(session_factory: SessionFactory, **_: Any) -> Hook:
    """Wrap :func:`create_feishu_gate_hook` into a core ``Hook`` instance."""
    class_hook: FeishuGateHook = create_feishu_gate_hook(
        session_factory=session_factory
    )
    return class_hook.as_hook()


#: Registry of supported hook names → factories.  Extend for v0.9 by
#: appending new entries (``"allow_list": _build_allow_list``, ...).
#: Keys are user-facing — don't rename without a migration story.
_HOOK_FACTORIES: dict[str, Callable[..., Hook]] = {
    "feishu_gate": _build_feishu_gate,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_hook_registry_for_agent(
    agent: _AgentLike,
    session_factory: SessionFactory,
) -> HookRegistry:
    """Instantiate hooks declared on *agent* and return a ready registry.

    The agent's ``model_config_json`` may hold::

        {"hooks": {"class_hooks": ["feishu_gate", ...]}}

    Any missing or malformed key is tolerated — this function NEVER
    raises on bad config; it degrades to an empty registry and logs a
    warning.  A ``HookRegistry`` is ALWAYS returned, never ``None``.

    Args:
        agent: An object with a ``model_config_json`` attribute.  The
            canonical caller is :class:`fim_one.web.models.agent.Agent`.
        session_factory: Zero-arg callable yielding a fresh
            :class:`AsyncSession`.  Each hook uses this to run DB
            operations in its own transaction — critically, independent
            of the web request's short-lived session.

    Returns:
        A fully-populated :class:`HookRegistry` ready to be handed to
        ``ReActAgent(hook_registry=...)``.
    """
    registry = HookRegistry()

    raw_config: Any = getattr(agent, "model_config_json", None)
    if not raw_config:
        return registry

    if not isinstance(raw_config, dict):
        logger.warning(
            "Agent model_config_json is not a dict (%s); skipping hooks",
            type(raw_config).__name__,
        )
        return registry

    hooks_block: Any = raw_config.get("hooks")
    if hooks_block is None:
        return registry

    if not isinstance(hooks_block, dict):
        logger.warning(
            "Agent model_config_json.hooks is not a dict (%s); skipping hooks",
            type(hooks_block).__name__,
        )
        return registry

    class_hooks: Any = hooks_block.get("class_hooks")
    if class_hooks is None:
        return registry

    if not isinstance(class_hooks, list):
        logger.warning(
            "Agent model_config_json.hooks.class_hooks is not a list (%s); "
            "skipping hooks",
            type(class_hooks).__name__,
        )
        return registry

    for raw_name in class_hooks:
        if not isinstance(raw_name, str):
            logger.warning(
                "Skipping non-string hook entry: %r (type=%s)",
                raw_name,
                type(raw_name).__name__,
            )
            continue

        name = raw_name.strip()
        if not name:
            continue

        factory = _HOOK_FACTORIES.get(name)
        if factory is None:
            logger.warning(
                "Unknown hook name %r declared on agent — skipping. "
                "Known hooks: %s",
                name,
                sorted(_HOOK_FACTORIES),
            )
            continue

        try:
            hook = factory(session_factory=session_factory)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to instantiate hook %r — skipping", name
            )
            continue

        registry.register(hook)
        logger.info("Registered hook %r for agent", name)

    return registry


# Re-export the internal registry under a documented name so power users /
# Python-level tests can introspect or monkey-patch it.  Not a stable API —
# treat as semi-public.
HOOK_FACTORIES: dict[str, Callable[..., Hook]] = _HOOK_FACTORIES


__all__ = [
    "HOOK_FACTORIES",
    "HookFactoryFn",
    "SessionFactory",
    "build_hook_registry_for_agent",
]
