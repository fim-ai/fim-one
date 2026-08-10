"""Type definitions for the Model layer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCallRequest:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    """A single message in a chat conversation.

    ``content`` may be a plain string **or** an OpenAI-style content array
    (list of dicts) for multi-modal messages (e.g. text + images).
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    name: str | None = None
    pinned: bool = False
    reasoning_content: str | None = None
    # Opaque signature string that Anthropic attaches to extended-thinking
    # blocks.  When a subsequent turn replays a previous assistant message
    # that used thinking, the signature MUST be returned unchanged — the
    # Anthropic API rejects thinking blocks whose signature is missing or
    # mutated.  Captured from the LiteLLM response and persisted to DB so
    # multi-turn conversations remain valid.
    signature: str | None = None
    # Anthropic prompt-caching breakpoint.  When set (typically to
    # ``{"type": "ephemeral"}``), LiteLLM forwards the field to Anthropic
    # so every token **up to and including** this message becomes part of
    # the cached prefix — subsequent requests that repeat the same prefix
    # pay ~10% of the normal input-token cost for cached tokens.  Only
    # meaningful on Anthropic-family endpoints; non-Anthropic providers
    # should never receive this field (see
    # :func:`fim_one.core.prompt.is_cache_capable`).
    cache_control: dict[str, str] | None = None
    # Raw ``reasoning`` items from the OpenAI Responses API, kept verbatim
    # as the provider emitted them (minus volatile bookkeeping keys).  Each
    # carries an ``encrypted_content`` blob that is opaque to us: replaying
    # the item unchanged on the next request is what lets a GPT-5.x model
    # keep its chain of thought across tool-call rounds instead of
    # re-deriving it every round.  Deliberately untyped dicts — the blob has
    # no schema we can validate, and verbatim round-tripping is the only
    # safe contract.  Independent of ``reasoning_content``, which holds the
    # human-readable summary for the UI and the DB.  Never serialised into
    # the chat-completions shape (see :meth:`to_openai_dict`), so it cannot
    # leak onto a provider that would reject it.
    reasoning_items: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Vision helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_vision_content(
        text: str,
        image_urls: list[str],
    ) -> list[dict[str, Any]]:
        """Build OpenAI Vision API content array with text and images.

        Args:
            text: The text prompt.
            image_urls: List of base64 data URLs
                (e.g. ``"data:image/png;base64,..."``) or HTTP URLs.

        Returns:
            A content array suitable for the OpenAI chat completion API.
        """
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for url in image_urls:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                }
            )
        return parts

    def to_openai_dict(
        self,
        *,
        replay_policy: Literal[
            "anthropic_thinking",
            "openai_responses",
            "informational_only",
            "unsupported",
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Convert to OpenAI API format.

        Args:
            replay_policy: Provider-aware reasoning-replay policy.  When
                ``None`` (default), the legacy permissive behaviour is
                preserved: ``reasoning_content`` and ``signature`` are
                serialised unconditionally if set.  Callers that know
                their target provider (see
                :func:`fim_one.core.prompt.reasoning_replay_policy`)
                should pass the resolved policy so that non-Anthropic
                models don't receive replayed thinking fields, which
                would break provider-side KV/prefix caches.

        Returns:
            A dictionary conforming to the OpenAI chat completion
            message schema.
        """
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        # Replay thinking block on subsequent turns so Anthropic models
        # that emit extended-thinking accept the history.  For
        # non-Anthropic providers (DeepSeek R1, Qwen QwQ, Gemini
        # thinking, OpenAI o-series) the reasoning field is
        # informational-only and MUST NOT be replayed — sending it back
        # invalidates the provider-side KV / prefix cache and violates
        # documented protocol.  Legacy callers that don't pass a
        # policy keep the old permissive behaviour for backward
        # compatibility; centralised request builders (see
        # ``OpenAICompatibleLLM._build_request_kwargs``) always pass
        # a resolved policy.
        include_reasoning = replay_policy in (None, "anthropic_thinking")
        if include_reasoning and self.reasoning_content:
            d["reasoning_content"] = self.reasoning_content
        if include_reasoning and self.signature:
            d["signature"] = self.signature
        # Anthropic prompt-caching breakpoint — LiteLLM forwards this
        # field at the message level for system messages and at the
        # content-block level for user/assistant messages.  Only include
        # when explicitly set; LiteLLM drops unknown params via
        # ``litellm.drop_params=True`` for providers that don't
        # understand ``cache_control``, but explicit caller-side gating
        # (see ``is_cache_capable``) prevents vendor-proxy edge cases.
        if self.cache_control is not None:
            d["cache_control"] = self.cache_control
        return d


@dataclass
class StreamChunk:
    """A single chunk from a streaming LLM response."""

    delta_content: str | None = None
    delta_reasoning: str | None = None
    finish_reason: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    usage: dict[str, int] | None = None
    # Opaque signature emitted by Anthropic extended-thinking blocks.
    # Streamed once (typically when the thinking block closes) — callers
    # must forward it onto the reconstructed assistant ChatMessage so
    # multi-turn replay stays valid.
    signature: str | None = None
    # One complete ``reasoning`` item from the OpenAI Responses API,
    # emitted when the item closes.  Callers accumulate these in order onto
    # the reconstructed assistant ChatMessage (``reasoning_items``) so the
    # next request can replay them.  The readable summary text arrives
    # separately as ``delta_reasoning``, so UI code needs no changes.
    reasoning_item: dict[str, Any] | None = None
    # The output limit cut the model off mid tool-call, so its arguments
    # never arrived complete.  The call is dropped rather than dispatched
    # with half a JSON payload; callers see this flag instead.
    truncated_tool_call: bool = False


@dataclass
class LLMResult:
    """Result from a non-streaming LLM call."""

    message: ChatMessage
    usage: dict[str, int] = field(default_factory=dict)
    # Provider finish reason ("stop", "length", "tool_calls", ...).
    # "length" means the output hit the max-token limit and is truncated —
    # callers may issue a continuation request.
    finish_reason: str | None = None
    # A tool call was cut off mid-arguments by the output limit and dropped.
    truncated_tool_call: bool = False
