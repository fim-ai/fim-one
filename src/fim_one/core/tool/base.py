"""Tool protocol and base class definitions."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """Protocol that all tools must implement."""

    @property
    def name(self) -> str:
        """Unique tool name."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description for LLM."""
        ...

    @property
    def category(self) -> str:
        """Tool category for grouping and filtering."""
        ...

    @property
    def display_name(self) -> str:
        """Human-friendly display name."""
        ...

    @property
    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...

    @property
    def cacheable(self) -> bool:
        """Whether results can be cached across DAG steps.

        Whitelist approach: only tools that explicitly opt in (True) are
        cached.  Default is False — safe for side-effectful or
        non-deterministic tools.
        """
        ...

    async def run(self, **kwargs: Any) -> str:
        """Execute the tool and return string result."""
        ...


class BaseTool:
    """Convenience base class implementing the Tool protocol."""

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def category(self) -> str:
        return "general"

    @property
    def display_name(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def cacheable(self) -> bool:
        """Whether results can be cached across DAG steps.

        Whitelist approach: defaults to False (not cached).  Override to
        True in read-only / idempotent tool subclasses.
        """
        return False

    @property
    def supports_background(self) -> bool:
        """Whether this tool may be dispatched as a background task.

        Whitelist approach: defaults to False.  Override to True in
        potentially slow tools (sandboxed code execution, long-running
        external calls) so the ReAct loop can offer a ``run_in_background``
        option — the model gets a task id immediately and a notification
        with the result when the tool finishes.
        """
        return False

    def availability(self) -> tuple[bool, str | None]:
        """Return (is_available, reason_if_not).

        Override in tools that require external configuration (API keys, etc.)
        to surface a meaningful message in the tool catalog UI.
        """
        return True, None

    @abstractmethod
    async def run(self, **kwargs: Any) -> str:
        ...


@dataclass
class Artifact:
    """A file produced by a tool execution."""

    name: str       # e.g. "report.html"
    path: str       # server-relative path under uploads root
    mime_type: str   # e.g. "text/html"
    size: int        # bytes


@dataclass
class ToolResult:
    """Rich result from a tool execution.

    Tools can return either a plain ``str`` (backward-compatible) or a
    ``ToolResult`` for rich content with artifacts.
    """

    content: str                                # text output (what LLM sees)
    content_type: str = "text"                  # "text" | "html" | "markdown" | "json"
    artifacts: list[Artifact] = field(default_factory=list)
