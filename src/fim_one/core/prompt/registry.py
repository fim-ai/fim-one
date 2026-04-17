"""Memoized prompt-section registry with a dynamic-boundary marker.

The registry is intentionally minimal: it stores an ordered list of
:class:`PromptSection` instances and renders them into a single string,
inserting :data:`DYNAMIC_BOUNDARY` at the seam between the last static
section and the first dynamic section.  Callers can then split on
:data:`DYNAMIC_BOUNDARY` to produce a **cacheable** prefix (everything
before the marker) and a **per-call** suffix (everything after).

Static sections are memoized by ``name`` — the first render evaluates
the ``content`` callable (or uses the string directly) and caches the
result for all subsequent renders in the same process.  Dynamic sections
are re-evaluated on every render (e.g. ``current_datetime`` changes each
turn).
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# Sentinel inserted between the last static section and the first dynamic
# section during rendering.  Callers split the rendered prompt on this
# marker to produce a cacheable prefix + per-call suffix.
DYNAMIC_BOUNDARY = "<!-- FIM_ONE_DYNAMIC_BOUNDARY -->"

# A section's content is either a plain string or a zero-or-more-arg
# callable that returns a string (called with ``**dynamic_kwargs`` for
# dynamic sections, with no arguments for static sections).
SectionContent = str | Callable[..., str]


@dataclass(frozen=True)
class PromptSection:
    """A named section of a system prompt.

    Args:
        name: Stable unique identifier used as the memoization key.
        content: Either a plain string or a callable returning a string.
            Static sections accept no arguments; dynamic sections receive
            ``**dynamic_kwargs`` from :meth:`PromptRegistry.render`.
        is_dynamic: When ``True``, the section is re-evaluated on every
            render and placed after :data:`DYNAMIC_BOUNDARY`.  When
            ``False`` (default), the section is rendered once and
            memoized.
    """

    name: str
    content: SectionContent
    is_dynamic: bool = False


class PromptRegistry:
    """Ordered, memoized registry of prompt sections.

    Sections are rendered in the order they were registered.  Inside
    :meth:`render`, all static sections are emitted first (separated by
    ``\n\n``), followed by :data:`DYNAMIC_BOUNDARY`, followed by all
    dynamic sections (also ``\n\n``-separated).  When there are no
    dynamic sections the boundary is omitted.

    The registry is safe to use from multiple threads — :meth:`register`
    and the memoization cache are guarded by a lock.
    """

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []
        self._static_cache: dict[str, str] = {}
        self._lock: Lock = Lock()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, section: PromptSection) -> None:
        """Append ``section`` to the registry.

        If a section with the same ``name`` is already registered it is
        replaced and its memoized render (if any) is discarded.

        Args:
            section: The prompt section to add.
        """
        with self._lock:
            # Replace-in-place to preserve ordering when re-registering.
            for idx, existing in enumerate(self._sections):
                if existing.name == section.name:
                    self._sections[idx] = section
                    self._static_cache.pop(section.name, None)
                    return
            self._sections.append(section)
            # Invalidate any stale cache entry (e.g. if this name was
            # previously registered and later removed).
            self._static_cache.pop(section.name, None)

    def clear(self) -> None:
        """Remove all registered sections and flush the memoization cache."""
        with self._lock:
            self._sections.clear()
            self._static_cache.clear()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(
        self,
        *,
        dynamic_kwargs: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        """Render all sections into a single prompt string.

        Args:
            dynamic_kwargs: Keyword arguments forwarded to every dynamic
                section's callable.  Static sections ignore this.

        Returns:
            A ``(full_prompt, boundary_char_index)`` tuple.  When the
            registry contains no dynamic sections, ``boundary_char_index``
            is ``len(full_prompt)`` (i.e. the entire prompt is cacheable
            and the suffix is empty).
        """
        kwargs = dynamic_kwargs or {}

        with self._lock:
            sections = list(self._sections)

        static_parts: list[str] = []
        dynamic_parts: list[str] = []

        for section in sections:
            if section.is_dynamic:
                dynamic_parts.append(self._render_dynamic(section, kwargs))
            else:
                static_parts.append(self._render_static(section))

        static_text = "\n\n".join(p for p in static_parts if p)
        dynamic_text = "\n\n".join(p for p in dynamic_parts if p)

        if not dynamic_text:
            # No dynamic content — the whole string is cacheable.
            return static_text, len(static_text)

        # Insert the boundary between the two halves.  ``boundary_index``
        # is the character index at which DYNAMIC_BOUNDARY starts, so
        # callers can split with ``prompt[:idx]`` / ``prompt[idx:]``.
        prefix = static_text + "\n\n" if static_text else ""
        boundary_index = len(prefix)
        full_prompt = prefix + DYNAMIC_BOUNDARY + "\n\n" + dynamic_text
        return full_prompt, boundary_index

    def render_split(
        self,
        *,
        dynamic_kwargs: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Render and split into ``(static_prefix, dynamic_suffix)``.

        The boundary marker itself is stripped — both halves are plain
        text ready to be sent as two separate messages (or joined with
        ``"\n\n"`` for providers that don't support cache breakpoints).

        Args:
            dynamic_kwargs: Forwarded to every dynamic section.

        Returns:
            ``(static_prefix, dynamic_suffix)``.  ``dynamic_suffix`` is
            the empty string when no dynamic sections are registered.
        """
        kwargs = dynamic_kwargs or {}

        with self._lock:
            sections = list(self._sections)

        static_parts: list[str] = []
        dynamic_parts: list[str] = []

        for section in sections:
            if section.is_dynamic:
                dynamic_parts.append(self._render_dynamic(section, kwargs))
            else:
                static_parts.append(self._render_static(section))

        static_text = "\n\n".join(p for p in static_parts if p)
        dynamic_text = "\n\n".join(p for p in dynamic_parts if p)
        return static_text, dynamic_text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _render_static(self, section: PromptSection) -> str:
        """Return the cached/rendered text for a static section."""
        with self._lock:
            cached = self._static_cache.get(section.name)
            if cached is not None:
                return cached

        rendered = _coerce_content(section.content)

        with self._lock:
            # Double-check after re-acquiring (another thread may have
            # populated the cache while we were rendering).
            cached = self._static_cache.get(section.name)
            if cached is not None:
                return cached
            self._static_cache[section.name] = rendered
            return rendered

    @staticmethod
    def _render_dynamic(
        section: PromptSection,
        kwargs: dict[str, Any],
    ) -> str:
        """Render a dynamic section (no caching)."""
        content = section.content
        if callable(content):
            return content(**kwargs)
        return content


def _coerce_content(content: SectionContent) -> str:
    """Resolve a section's ``content`` (string or zero-arg callable)."""
    if callable(content):
        return content()
    return content


# ----------------------------------------------------------------------
# Module-level default registry
# ----------------------------------------------------------------------

# Lazily-populated shared registry.  Callers that want isolation (tests,
# per-agent registries) should instantiate their own ``PromptRegistry``.
default_registry: PromptRegistry = PromptRegistry()


def register_section(
    name: str,
    content: SectionContent,
    *,
    is_dynamic: bool = False,
) -> None:
    """Register a section on :data:`default_registry`.

    This is a convenience wrapper around
    :meth:`PromptRegistry.register` for the common single-registry case.

    Args:
        name: Stable unique section name.
        content: Plain string or callable returning a string.
        is_dynamic: ``True`` to mark the section as per-call dynamic.
    """
    default_registry.register(
        PromptSection(name=name, content=content, is_dynamic=is_dynamic),
    )


# ``field`` is re-exported only so downstream callers can declare custom
# frozen dataclasses that extend :class:`PromptSection` — keeps the
# import surface terse.
__all__ = [
    "DYNAMIC_BOUNDARY",
    "PromptRegistry",
    "PromptSection",
    "default_registry",
    "field",
    "register_section",
]
