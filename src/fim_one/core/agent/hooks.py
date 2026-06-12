"""Agent Hook System — deterministic enforcement layer outside the LLM loop.

Hooks execute automatically on tool events and cannot be bypassed by agent
instructions.  They run synchronously in priority order (lower priority
number = runs first) and must NEVER crash the agent.

Hook points:
- ``PRE_TOOL_USE``:  Runs before a tool is executed.  Can block the call
  (``allow=False``) or modify the tool arguments.
- ``POST_TOOL_USE``: Runs after a tool is executed.  Can modify the result.
- ``SESSION_START``: Runs once when the agent session starts.
"""

from __future__ import annotations

import fnmatch
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class HookPoint(Enum):
    """Points in the agent lifecycle where hooks can be attached."""

    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SESSION_START = "session_start"


@dataclass
class HookContext:
    """Context passed to hooks.

    Attributes:
        hook_point: Which lifecycle point triggered this hook.
        tool_name: Name of the tool being invoked (PRE/POST only).
        tool_args: Keyword arguments for the tool call (PRE/POST only).
        tool_result: String result from the tool (POST only).
        agent_id: The agent's database ID, if available.
        user_id: The invoking user's database ID, if available.
        conversation_id: The conversation's database ID, if available.
        metadata: Arbitrary extra data for custom hooks.
    """

    hook_point: HookPoint
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    agent_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class HookResult:
    """Result from a hook execution.

    Attributes:
        allow: When ``False``, the tool call is blocked (PRE only).
        modified_args: Replacement tool args (PRE only).
        modified_result: Replacement tool result (POST only).
        side_effects: Log of what the hook did (informational).
        error: Error message explaining why the call was blocked.
    """

    allow: bool = True
    modified_args: dict[str, Any] | None = None
    modified_result: str | None = None
    side_effects: list[str] = field(default_factory=list)
    error: str | None = None


# Type alias for hook handler functions.
HookHandler = Callable[[HookContext], Awaitable[HookResult]]


class Hook:
    """A single hook that executes at a specific lifecycle point.

    Args:
        name: Unique identifier for this hook.
        hook_point: The lifecycle point where this hook fires.
        handler: Async callable that receives a ``HookContext`` and returns
            a ``HookResult``.
        description: Human-readable description of what the hook does.
        priority: Execution order — lower values run first.  Default is 0.
        tool_filter: Optional glob pattern to match tool names.  When set,
            the hook only fires for tools whose name matches the pattern
            (e.g. ``"github__*"`` for all GitHub connector tools).
        fail_open: Behaviour when the handler raises.  When ``False``
            (the default), a crashing ``PRE_TOOL_USE`` hook blocks the tool
            call — enforcement hooks (security gates, approvals) must not be
            bypassable by their own bugs.  Set ``True`` for non-enforcement
            PRE hooks (loggers, rate limiters) that should never take the
            agent down.  Has no effect on POST/SESSION hooks, which cannot
            block execution.
    """

    def __init__(
        self,
        name: str,
        hook_point: HookPoint,
        handler: HookHandler,
        description: str = "",
        priority: int = 0,
        tool_filter: str | None = None,
        fail_open: bool = False,
    ) -> None:
        self.name = name
        self.hook_point = hook_point
        self.handler = handler
        self.description = description
        self.priority = priority
        self.tool_filter = tool_filter
        self.fail_open = fail_open

    def matches_tool(self, tool_name: str | None) -> bool:
        """Check whether this hook should fire for the given tool name.

        Returns ``True`` when no filter is set (fires for all tools) or
        when the tool name matches the glob pattern.
        """
        if self.tool_filter is None:
            return True
        if tool_name is None:
            return False
        return fnmatch.fnmatch(tool_name, self.tool_filter)

    async def execute(self, context: HookContext) -> HookResult:
        """Execute the hook handler, never crashing the agent.

        If the handler raises, the failure is logged and a ``HookResult`` is
        returned according to the hook's ``fail_open`` policy:

        - ``PRE_TOOL_USE`` with ``fail_open=False`` (the default) →
          ``allow=False``.  A crashing enforcement hook blocks the call so a
          broken security gate cannot become a silent bypass.
        - ``PRE_TOOL_USE`` with ``fail_open=True`` → ``allow=True``.  For
          non-enforcement hooks (loggers, rate limiters) that should not take
          the agent down.
        - ``POST_TOOL_USE`` / ``SESSION_START`` → ``allow=True``.  These
          cannot block execution; the result is simply left unmodified.
        """
        try:
            return await self.handler(context)
        except Exception as exc:
            fail_closed = (
                self.hook_point is HookPoint.PRE_TOOL_USE and not self.fail_open
            )
            logger.exception(
                "Hook '%s' raised an exception — %s",
                self.name,
                "blocking tool call (fail-closed)"
                if fail_closed
                else "allowing tool call to proceed",
            )
            if fail_closed:
                return HookResult(
                    allow=False,
                    error=(
                        f"Hook '{self.name}' failed and the call was blocked "
                        f"(fail-closed): {type(exc).__name__}"
                    ),
                    side_effects=[f"Hook '{self.name}' crashed: {exc}"],
                )
            return HookResult(
                allow=True,
                side_effects=[f"Hook '{self.name}' failed with exception: {exc}"],
            )

    def __repr__(self) -> str:
        return (
            f"Hook(name={self.name!r}, point={self.hook_point.value}, "
            f"priority={self.priority})"
        )


class HookRegistry:
    """Manages and executes hooks at various lifecycle points.

    Hooks are stored per ``HookPoint`` and sorted by priority (ascending)
    within each point.  They run sequentially — not concurrently — so that
    earlier hooks can influence later ones (e.g. a PRE hook modifying args
    before the next PRE hook sees them).
    """

    def __init__(self) -> None:
        self._hooks: dict[HookPoint, list[Hook]] = defaultdict(list)

    def register(self, hook: Hook) -> None:
        """Register a hook.

        The hook is inserted into the appropriate priority-sorted list for
        its ``hook_point``.

        Args:
            hook: The hook instance to register.
        """
        hooks_list = self._hooks[hook.hook_point]
        hooks_list.append(hook)
        hooks_list.sort(key=lambda h: h.priority)

    def unregister(self, name: str) -> None:
        """Remove a hook by name from all hook points.

        Args:
            name: The name of the hook to remove.
        """
        for point in HookPoint:
            self._hooks[point] = [
                h for h in self._hooks[point] if h.name != name
            ]

    def list_hooks(self, point: HookPoint | None = None) -> list[Hook]:
        """List registered hooks, optionally filtered by hook point.

        Args:
            point: When provided, only hooks for this point are returned.

        Returns:
            A list of ``Hook`` instances sorted by priority.
        """
        if point is not None:
            return list(self._hooks[point])
        result: list[Hook] = []
        for p in HookPoint:
            result.extend(self._hooks[p])
        return result

    async def run_pre_tool(self, context: HookContext) -> HookResult:
        """Run all ``PRE_TOOL_USE`` hooks in priority order.

        If any hook returns ``allow=False``, execution stops and the
        blocking result is returned immediately.  If a hook modifies args,
        the modified args are carried forward to subsequent hooks.

        Args:
            context: The hook context (must have ``tool_name`` set).

        Returns:
            A combined ``HookResult``.  If all hooks allow the call, the
            final ``modified_args`` (if any) are from the last hook that
            modified them.
        """
        combined = HookResult()

        for hook in self._hooks[HookPoint.PRE_TOOL_USE]:
            if not hook.matches_tool(context.tool_name):
                continue

            # If a previous hook modified args, update the context so the
            # next hook sees the modified version.
            if combined.modified_args is not None:
                context.tool_args = combined.modified_args

            result = await hook.execute(context)

            # Merge side effects.
            combined.side_effects.extend(result.side_effects)

            if not result.allow:
                combined.allow = False
                combined.error = result.error or f"Blocked by hook '{hook.name}'"
                return combined

            if result.modified_args is not None:
                combined.modified_args = result.modified_args

        return combined

    async def run_post_tool(self, context: HookContext) -> HookResult:
        """Run all ``POST_TOOL_USE`` hooks in priority order.

        If a hook modifies the result, the modified result is carried
        forward to subsequent hooks.

        Args:
            context: The hook context (must have ``tool_result`` set).

        Returns:
            A combined ``HookResult`` with the final ``modified_result``
            if any hooks modified it.
        """
        combined = HookResult()

        for hook in self._hooks[HookPoint.POST_TOOL_USE]:
            if not hook.matches_tool(context.tool_name):
                continue

            # Carry forward modified results.
            if combined.modified_result is not None:
                context.tool_result = combined.modified_result

            result = await hook.execute(context)
            combined.side_effects.extend(result.side_effects)

            if result.modified_result is not None:
                combined.modified_result = result.modified_result

        return combined

    async def run_session_start(self, context: HookContext) -> HookResult:
        """Run all ``SESSION_START`` hooks in priority order.

        Args:
            context: The hook context.

        Returns:
            A combined ``HookResult`` with aggregated side effects.
        """
        combined = HookResult()

        for hook in self._hooks[HookPoint.SESSION_START]:
            result = await hook.execute(context)
            combined.side_effects.extend(result.side_effects)

        return combined

    def __len__(self) -> int:
        return sum(len(hooks) for hooks in self._hooks.values())

    def __repr__(self) -> str:
        counts = {p.value: len(h) for p, h in self._hooks.items() if h}
        return f"HookRegistry({counts})"
