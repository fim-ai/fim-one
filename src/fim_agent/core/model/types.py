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
            parts.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
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
        return d


@dataclass
class StreamChunk:
    """A single chunk from a streaming LLM response."""

    delta_content: str | None = None
    finish_reason: str | None = None
    tool_calls: list[ToolCallRequest] | None = None


@dataclass
class LLMResult:
    """Result from a non-streaming LLM call."""

    message: ChatMessage
    usage: dict[str, int] = field(default_factory=dict)
