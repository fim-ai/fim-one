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

    def to_openai_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format.

        Returns:
            A dictionary conforming to the OpenAI chat completion message schema.
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
        # that emit extended-thinking accept the history.  LiteLLM passes
        # ``reasoning_content`` + ``signature`` through to the provider;
        # providers that don't understand the fields ignore them (global
        # ``litellm.drop_params=True`` handles the rest).
        if self.reasoning_content:
            d["reasoning_content"] = self.reasoning_content
        if self.signature:
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


@dataclass
class LLMResult:
    """Result from a non-streaming LLM call."""

    message: ChatMessage
    usage: dict[str, int] = field(default_factory=dict)
