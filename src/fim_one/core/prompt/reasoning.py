"""Provider-aware reasoning-replay policy.

Several frontier models surface chain-of-thought / reasoning tokens back
to the caller via ``reasoning_content``.  The rules for replaying that
content on subsequent turns differ sharply across providers:

* **Anthropic Claude extended-thinking** — the thinking block and its
  opaque ``signature`` MUST be sent back verbatim on multi-turn replay.
  Omitting the signature causes the Messages API to reject the request.

* **OpenAI GPT-5.x** — reasoning is not text at all but a sequence of
  opaque ``reasoning`` items with encrypted payloads.  They must be
  replayed verbatim, yet only the /v1/responses protocol has a slot for
  them; the chat-completions shape does not, so the readable summary is
  dropped there just as it is for the informational-only family.

* **DeepSeek R1 / v3, Qwen QwQ, Gemini thinking, OpenAI o-series** —
  provider documentation explicitly says the reasoning field is
  **informational only** and MUST NOT be sent back in subsequent turns.
  Sending it back:
    1. Violates the documented protocol (some versions may begin
       rejecting the field outright).
    2. Invalidates the provider-side KV / prefix cache — the history
       bytes are no longer stable, so the cache discount we rely on
       disappears and we pay full input-token cost on every turn.

* **Classic non-reasoning models** (GPT-4o, Gemini 1.5, Llama-3.x etc.)
  never emit reasoning content in the first place, so there's nothing
  to replay.  Treated identically to ``informational_only`` for the
  purposes of this policy: if anything leaks via an odd proxy, drop it.

This module centralises the policy decision so there's a single
"modelless discipline" site that callers can audit.  No ad-hoc
``if "claude" in model_id`` checks belong anywhere else in the
codebase — reach for :func:`reasoning_replay_policy` instead.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

from typing import Literal

ReasoningReplayPolicy = Literal[
    "anthropic_thinking",
    "openai_responses",
    "informational_only",
    "unsupported",
]

# Fragments that, when present in the (lower-cased) model id, indicate
# Anthropic extended-thinking semantics — reasoning_content MUST be
# replayed together with the signature.  Kept in sync with the
# Anthropic-family detection logic in :mod:`fim_one.core.prompt.caching`.
_ANTHROPIC_THINKING_FRAGMENTS: tuple[str, ...] = (
    "claude",
    "anthropic",
    "bedrock/anthropic",
    "vertex_ai/claude",
)

# Fragments that identify models carrying reasoning state as opaque
# Responses-API items rather than as a replayable text block.  GPT-5.x
# keeps its chain of thought in ``reasoning`` items whose
# ``encrypted_content`` must be sent back verbatim on the next request, but
# only on the /v1/responses protocol — the chat-completions shape has no
# slot for them, so the readable ``reasoning_content`` is still dropped
# exactly as it is for ``informational_only``.  Checked after the Anthropic
# fragments and before the informational ones, whose generic "reasoning"
# entry would otherwise swallow proxy-tagged GPT-5 ids.
_OPENAI_RESPONSES_FRAGMENTS: tuple[str, ...] = ("gpt-5",)

# Fragments that identify reasoning-capable models where the reasoning
# field is informational-only and MUST NOT be replayed to the provider.
# Ordering matters only in the sense that ``_ANTHROPIC_THINKING_FRAGMENTS``
# is checked first — a model id matching both (vanishingly unlikely)
# would resolve to "anthropic_thinking".
_INFORMATIONAL_REASONING_FRAGMENTS: tuple[str, ...] = (
    "deepseek-reasoner",
    "deepseek-r1",
    "qwq",
    "gemini-2.0-flash-thinking",
    "gemini-2.5-flash-thinking",
    "gemini-flash-thinking",
    # OpenAI o-series — all known reasoning models use the o-prefix.
    "o1",
    "o3",
    "o4",
    # Some proxies surface reasoning on generic "reasoning" tagged models.
    "reasoning",
)


def reasoning_replay_policy(model_id: str | None) -> ReasoningReplayPolicy:
    """Return the reasoning-replay policy for *model_id*.

    Args:
        model_id: The provider-qualified model identifier (e.g.
            ``"claude-sonnet-4-5"``, ``"deepseek-reasoner"``,
            ``"bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"``).
            When ``None`` or empty, returns ``"unsupported"`` — nothing
            to replay, nothing to leak.

    Returns:
        One of:

        * ``"anthropic_thinking"`` — caller MUST replay
          ``reasoning_content`` + ``signature`` on every subsequent turn
          (Claude family, Bedrock/Vertex-hosted Claude).
        * ``"openai_responses"`` — reasoning state lives in opaque
          Responses-API ``reasoning`` items (GPT-5.x).  Callers on the
          /v1/responses protocol MUST replay
          :attr:`ChatMessage.reasoning_items` verbatim; the readable
          ``reasoning_content`` is still dropped from outgoing requests,
          so on the chat-completions fallback this behaves exactly like
          ``"informational_only"``.
        * ``"informational_only"`` — caller MUST drop
          ``reasoning_content`` / ``signature`` from outgoing requests
          but MAY persist them locally for display (DeepSeek R1,
          Qwen QwQ, Gemini thinking, OpenAI o-series).
        * ``"unsupported"`` — the model doesn't emit reasoning content
          at all; drop any stray field defensively.
    """
    if not model_id:
        return "unsupported"
    lowered = model_id.lower()
    # Anthropic first: the only policy that actually replays.
    if any(fragment in lowered for fragment in _ANTHROPIC_THINKING_FRAGMENTS):
        return "anthropic_thinking"
    if any(fragment in lowered for fragment in _OPENAI_RESPONSES_FRAGMENTS):
        return "openai_responses"
    if any(fragment in lowered for fragment in _INFORMATIONAL_REASONING_FRAGMENTS):
        return "informational_only"
    return "unsupported"


__all__ = ["ReasoningReplayPolicy", "reasoning_replay_policy"]
