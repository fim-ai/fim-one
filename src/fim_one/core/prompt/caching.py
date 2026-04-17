"""Prompt-cache capability detection for the supported providers.

Anthropic-family models (Claude direct, Bedrock, Vertex, and most
Anthropic-proxy deployments) accept a ``cache_control: {"type":
"ephemeral"}`` field on system messages and content blocks — the
provider caches every token up to the breakpoint and only re-processes
the suffix on subsequent requests.  OpenAI (pre-2024-10) and most other
providers do NOT understand ``cache_control`` and may reject or mis-
handle the field.  LiteLLM transparently forwards unknown fields to
providers that silently drop them, but we defensively detect capability
via the model identifier so non-Anthropic requests never see the field.

The detection is a lightweight substring match: it runs once per LLM
call and must avoid false positives (sending ``cache_control`` to an
OpenAI model breaks some vendor proxies).  Add new provider identifiers
here as support expands.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

# Substrings that, when present in the (lower-cased) model id, indicate
# an Anthropic-compatible prompt-caching endpoint.  Keep this list tight
# — a false positive means we send ``cache_control`` to a provider that
# doesn't understand it.
_CACHE_CAPABLE_MODEL_FRAGMENTS: tuple[str, ...] = (
    "claude",
    "anthropic",
    # AWS Bedrock hosts Claude under identifiers like
    # ``bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0``.
    "bedrock/anthropic",
    # Vertex hosts Claude under ``vertex_ai/claude-3-5-sonnet@20240620``.
    "vertex_ai/claude",
)


def is_cache_capable(model_id: str | None) -> bool:
    """Return ``True`` when ``model_id`` advertises Anthropic-style caching.

    Args:
        model_id: The provider-qualified model identifier (e.g.
            ``"claude-3-5-sonnet-20240620"``, ``"anthropic/claude-opus-4"``,
            ``"bedrock/anthropic.claude-3-haiku-20240307-v1:0"``).  When
            ``None`` or empty, caching is assumed unsupported.

    Returns:
        ``True`` when the model accepts ``cache_control`` breakpoints.
    """
    if not model_id:
        return False
    lowered = model_id.lower()
    return any(fragment in lowered for fragment in _CACHE_CAPABLE_MODEL_FRAGMENTS)


__all__ = ["is_cache_capable"]
