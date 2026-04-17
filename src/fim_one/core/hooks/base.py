"""Thin OO wrappers over :mod:`fim_one.core.agent.hooks` primitives.

The core ``HookRegistry`` treats hooks as plain callables + metadata.  For
more complex, stateful integration hooks (e.g. ``FeishuGateHook`` which
holds a DB session factory and a channel lookup) we prefer a class-based
interface with :meth:`should_trigger` and :meth:`execute` — this module
provides the abstract bases and the adapters that let a class instance
register itself as a core ``Hook``.
"""

from __future__ import annotations

import abc

from fim_one.core.agent.hooks import (
    Hook,
    HookContext,
    HookPoint,
    HookResult,
)


class _BaseClassHook(abc.ABC):
    """Shared implementation for Pre/Post class hooks."""

    #: Unique hook name used by the registry.
    name: str = "unnamed_hook"
    #: Human-readable description — surfaced to the admin API.
    description: str = ""
    #: Priority (lower runs first).  See ``Hook`` for semantics.
    priority: int = 50
    #: Optional glob pattern to filter tool names.
    tool_filter: str | None = None

    #: Subclasses MUST set this to the concrete hook point.
    hook_point: HookPoint = HookPoint.PRE_TOOL_USE

    def should_trigger(self, context: HookContext) -> bool:
        """Return True if the hook should run for this context.

        Default: always run (the outer registry already filters by
        ``tool_filter``).  Override to add per-call logic — e.g. gating
        only on tools whose action row has ``requires_confirmation=True``.
        """
        return True

    @abc.abstractmethod
    async def execute(self, context: HookContext) -> HookResult:
        """Run the hook logic and return a ``HookResult``."""

    def as_hook(self) -> Hook:
        """Wrap this instance as a core ``Hook`` ready for registration."""

        async def _handler(ctx: HookContext) -> HookResult:
            if not self.should_trigger(ctx):
                return HookResult()
            return await self.execute(ctx)

        return Hook(
            name=self.name,
            hook_point=self.hook_point,
            handler=_handler,
            description=self.description,
            priority=self.priority,
            tool_filter=self.tool_filter,
        )


class PreToolUseHook(_BaseClassHook):
    """Base class for ``PRE_TOOL_USE`` hooks."""

    hook_point: HookPoint = HookPoint.PRE_TOOL_USE

    @abc.abstractmethod
    async def execute(self, context: HookContext) -> HookResult:  # pragma: no cover
        ...


class PostToolUseHook(_BaseClassHook):
    """Base class for ``POST_TOOL_USE`` hooks."""

    hook_point: HookPoint = HookPoint.POST_TOOL_USE

    @abc.abstractmethod
    async def execute(self, context: HookContext) -> HookResult:  # pragma: no cover
        ...


__all__ = [
    "PreToolUseHook",
    "PostToolUseHook",
    "HookContext",
    "HookPoint",
    "HookResult",
]
