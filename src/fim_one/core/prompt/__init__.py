"""System prompt section registry with cache-breakpoint support.

This module provides a memoized registry of prompt sections that lets
callers compose system prompts from a fixed set of stable "static"
sections plus a small number of per-call "dynamic" sections.  Static
section content is memoized so the formatted string is built once per
process and re-used on every LLM call, which is critical for LLM
providers that support prompt caching (Anthropic ``cache_control``
breakpoints, Bedrock / Anthropic-proxy models).

See :mod:`fim_one.core.prompt.registry` for the public API.
"""

from .caching import is_cache_capable
from .registry import (
    DYNAMIC_BOUNDARY,
    PromptRegistry,
    PromptSection,
    default_registry,
    register_section,
)

__all__ = [
    "DYNAMIC_BOUNDARY",
    "PromptRegistry",
    "PromptSection",
    "default_registry",
    "is_cache_capable",
    "register_section",
]
