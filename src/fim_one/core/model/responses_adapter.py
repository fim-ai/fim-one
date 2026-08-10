"""Pure translation layer between FIM One's chat types and /v1/responses.

The OpenAI Responses API is not a reshuffled chat-completions payload: it
replaces the ``messages`` array with a flat ``input`` list of *items*
(messages, ``function_call``, ``function_call_output``, ``reasoning``),
flattens tool definitions, renames every usage counter, and reports
completion through a stream of typed events rather than choice deltas.

Everything in this module is a pure function over plain data.  Nothing
here imports :mod:`litellm` or touches the network, which is what makes
the whole surface unit-testable without a provider.  Provider objects are
read through :func:`_get`, so a response works the same whether LiteLLM
hands back a pydantic model or a raw dict.

Why this exists at all: LiteLLM can bridge chat-completions calls onto
/v1/responses, but the translation is lossy in exactly the place that
matters.  It discards the ``reasoning`` items, so a GPT-5.x agent
re-derives its chain of thought on every tool-call round. Talking the
protocol directly lets us carry those items forward.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from typing import Any

from .types import ChatMessage, LLMResult, StreamChunk, ToolCallRequest

logger = logging.getLogger(__name__)

# Keys stripped from a reasoning item before it is replayed.
#
# ``id`` is the item's *server-side* handle.  We send ``store=false``, so
# nothing is persisted upstream, and echoing the id back makes the API try
# to resolve a record that does not exist:
#
#     Item with id 'rs_0cbcb...' not found. Items are not persisted when
#     `store` is set to false.
#
# The ``encrypted_content`` blob is self-contained, which is what makes
# stateless replay work at all, so dropping the id costs nothing.
#
# ``status`` is per-response bookkeeping ("in_progress" / "completed");
# echoing a stale value back is at best noise.
_VOLATILE_ITEM_KEYS: frozenset[str] = frozenset({"id", "status"})


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a provider object that may be a dict or a model.

    LiteLLM returns pydantic models for some providers and plain dicts for
    others (and raw dicts when a proxy passes through an unmodelled
    field).  Every read of provider data goes through here so the adapter
    never has to care which it got.
    """
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _as_dict(source: Any) -> dict[str, Any]:
    """Best-effort conversion of a provider object into a plain dict.

    Used for reasoning items, which we store and replay verbatim. Pydantic
    models expose ``model_dump``; anything else is returned as-is when it
    is already a mapping, or ``{}`` when it is something we cannot read.
    """
    if isinstance(source, dict):
        return dict(source)
    dump = getattr(source, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
        except Exception:  # pragma: no cover - defensive, model_dump is total
            return {}
        if isinstance(dumped, dict):
            return dumped
    return {}


def sanitize_reasoning_item(item: Any) -> dict[str, Any]:
    """Return *item* as a plain dict safe to replay on a later request."""
    return {k: v for k, v in _as_dict(item).items() if k not in _VOLATILE_ITEM_KEYS}


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------


def _content_parts(content: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert chat-shaped content into Responses ``input_*`` parts.

    Chat completions name these parts ``text`` / ``image_url`` and nest the
    URL under a sub-object; Responses names them ``input_text`` /
    ``input_image`` and hoists the URL to the top level.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}] if content else []
    parts: list[dict[str, Any]] = []
    for part in content:
        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "input_text", "text": part.get("text", "")})
        elif part_type == "image_url":
            url = part.get("image_url", {})
            url_value = url.get("url") if isinstance(url, dict) else url
            if url_value:
                parts.append({"type": "input_image", "image_url": url_value})
    return parts


def build_responses_input(messages: Iterable[ChatMessage]) -> list[dict[str, Any]]:
    """Flatten chat messages into the Responses ``input`` item list.

    The ordering inside an assistant turn is load-bearing: reasoning items
    come first, then any visible text, then the function calls they led to.
    Replaying them out of order, or replaying a call without the reasoning
    that produced it, is what the API rejects.

    Assistant messages carrying neither content nor tool calls are dropped
    even when they hold reasoning items. An orphan reasoning item, one that
    never resolved into an action, is a 400 on the next request.
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            # A tool result is its own item type keyed by call_id, not a
            # message with a role.
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": _stringify_tool_output(msg.content),
                }
            )
            continue

        if msg.role == "assistant":
            has_content = bool(msg.content)
            has_calls = bool(msg.tool_calls)
            if not has_content and not has_calls:
                continue
            for item in msg.reasoning_items or []:
                sanitized = sanitize_reasoning_item(item)
                if sanitized:
                    items.append(sanitized)
            if has_content:
                items.append(
                    {
                        "role": "assistant",
                        "content": _assistant_content_parts(msg.content),
                    }
                )
            for call in msg.tool_calls or []:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                )
            continue

        # system / user
        parts = _content_parts(msg.content)
        if parts:
            items.append({"role": msg.role, "content": parts})
    return items


def _assistant_content_parts(
    content: str | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Assistant text uses ``output_text`` parts, not ``input_text``."""
    if isinstance(content, str):
        return [{"type": "output_text", "text": content}]
    text = " ".join(
        part.get("text", "")
        for part in (content or [])
        if part.get("type") in ("text", "output_text")
    ).strip()
    return [{"type": "output_text", "text": text}]


def _stringify_tool_output(content: str | list[dict[str, Any]] | None) -> str:
    """Tool results travel as a single string on the Responses protocol."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return " ".join(
        part.get("text", "") for part in content if part.get("type") == "text"
    )


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Flatten chat-shaped tool definitions into Responses tool definitions.

    Chat completions nest the schema under ``function``; Responses hoists
    ``name`` / ``description`` / ``parameters`` to the top level and wants
    an explicit ``strict`` flag.
    """
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", tool)
        converted.append(
            {
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
                "strict": bool(fn.get("strict", False)),
            }
        )
    return converted


def convert_tool_choice(
    tool_choice: str | dict[str, Any] | None,
) -> str | dict[str, Any] | None:
    """Translate a chat ``tool_choice`` into its Responses equivalent.

    The three string states pass through unchanged.  A named choice loses
    the ``function`` wrapper: ``{"type": "function", "function": {"name":
    "x"}}`` becomes ``{"type": "function", "name": "x"}``.
    """
    if tool_choice is None or isinstance(tool_choice, str):
        return tool_choice
    if tool_choice.get("type") == "function":
        fn = tool_choice.get("function")
        name = fn.get("name") if isinstance(fn, dict) else tool_choice.get("name")
        if name:
            return {"type": "function", "name": name}
    return tool_choice


def convert_response_format(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Map ``response_format`` onto the Responses ``text`` parameter."""
    if not response_format:
        return None
    return {"format": response_format}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def map_usage(raw_usage: Any) -> dict[str, int]:
    """Normalise Responses usage counters onto the chat-completions names.

    Responses reports ``input_tokens`` / ``output_tokens``; the rest of the
    codebase counts ``prompt_tokens`` / ``completion_tokens``.  Reasoning
    and cached-token counters are surfaced too so cost accounting can see
    what the thinking actually cost.  Never raises: a provider that omits
    usage entirely yields zeros, because this runs on the hot path.
    """
    if raw_usage is None:
        return {}
    prompt = _int_or_zero(_get(raw_usage, "input_tokens"))
    completion = _int_or_zero(_get(raw_usage, "output_tokens"))
    total = _int_or_zero(_get(raw_usage, "total_tokens")) or (prompt + completion)
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    details = _get(raw_usage, "output_tokens_details")
    reasoning_tokens = _int_or_zero(_get(details, "reasoning_tokens")) if details else 0
    usage["reasoning_tokens"] = reasoning_tokens
    input_details = _get(raw_usage, "input_tokens_details")
    cached = _int_or_zero(_get(input_details, "cached_tokens")) if input_details else 0
    # Mirror the chat-completions cache counters so UsageTracker and the
    # SSE payload read the same keys regardless of protocol.
    usage["cache_read_input_tokens"] = cached
    usage["cache_creation_input_tokens"] = 0
    return usage


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _finish_reason(response: Any, has_tool_calls: bool) -> str:
    """Derive a chat-completions finish reason from a Responses status.

    An ``incomplete`` response whose reason is ``max_output_tokens`` maps to
    ``"length"``, which is what the existing truncation guards key on.
    """
    status = _get(response, "status")
    if status == "incomplete":
        details = _get(response, "incomplete_details")
        if _get(details, "reason") == "max_output_tokens":
            return "length"
    return "tool_calls" if has_tool_calls else "stop"


def parse_response(response: Any) -> LLMResult:
    """Convert a completed Responses payload into an :class:`LLMResult`.

    Tool-call ids come from ``call_id``, not ``id``: ``id`` identifies the
    output item within the response, while ``call_id`` is the handle the
    matching ``function_call_output`` must quote on the next request.
    """
    content_parts: list[str] = []
    summary_parts: list[str] = []
    reasoning_items: list[dict[str, Any]] = []
    tool_calls: list[ToolCallRequest] = []

    for item in _get(response, "output") or []:
        item_type = _get(item, "type")
        if item_type == "reasoning":
            reasoning_items.append(sanitize_reasoning_item(item))
            summary_parts.extend(_summary_text(item))
        elif item_type == "message":
            for part in _get(item, "content") or []:
                if _get(part, "type") == "output_text":
                    text = _get(part, "text")
                    if text:
                        content_parts.append(text)
        elif item_type == "function_call":
            tool_calls.append(
                ToolCallRequest(
                    id=_get(item, "call_id") or _get(item, "id") or "",
                    name=_get(item, "name") or "",
                    arguments=_parse_arguments(_get(item, "arguments")),
                )
            )

    message = ChatMessage(
        role="assistant",
        content="".join(content_parts) or None,
        tool_calls=tool_calls or None,
        reasoning_content="\n".join(p for p in summary_parts if p) or None,
        reasoning_items=reasoning_items or None,
    )
    return LLMResult(
        message=message,
        usage=map_usage(_get(response, "usage")),
        finish_reason=_finish_reason(response, bool(tool_calls)),
    )


def _summary_text(item: Any) -> list[str]:
    """Pull the human-readable summary strings out of a reasoning item."""
    return [
        text
        for entry in (_get(item, "summary") or [])
        if (text := _get(entry, "text"))
    ]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Decode a function call's JSON arguments, tolerating malformed input."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse Responses tool-call arguments, using raw string")
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def stream_to_chunks(stream: AsyncIterator[Any]) -> AsyncIterator[StreamChunk]:
    """Translate a Responses event stream into :class:`StreamChunk` values.

    The protocol is event-typed rather than delta-shaped, so this is a
    dispatch table over ``event.type``.  Unknown event types are counted
    and ignored: OpenAI adds them regularly, and an unrecognised event must
    never break a live turn.  The terminal ``response.completed`` event
    carries the authoritative output list, so tool calls and usage are
    taken from there rather than reassembled from deltas.
    """
    unknown: Counter[str] = Counter()
    pending_args: dict[str, str] = {}

    async for event in stream:
        # LiteLLM hands back a str-subclassed enum here, so the comparisons
        # below already work; unwrapping to the plain value keeps them
        # working if that ever becomes a bare Enum, and keeps the
        # unknown-event log readable.
        raw_type = _get(event, "type") or ""
        event_type = getattr(raw_type, "value", raw_type)

        if event_type == "response.output_text.delta":
            delta = _get(event, "delta")
            if delta:
                yield StreamChunk(delta_content=delta)

        elif event_type == "response.reasoning_summary_text.delta":
            delta = _get(event, "delta")
            if delta:
                yield StreamChunk(delta_reasoning=delta)

        elif event_type == "response.output_item.done":
            item = _get(event, "item")
            if _get(item, "type") == "reasoning":
                yield StreamChunk(reasoning_item=sanitize_reasoning_item(item))

        elif event_type == "response.function_call_arguments.delta":
            # Accumulated only as a fallback; the completed event is
            # authoritative.  Tracked per item id so parallel calls don't
            # interleave their argument fragments.
            item_id = _get(event, "item_id") or ""
            pending_args[item_id] = pending_args.get(item_id, "") + (
                _get(event, "delta") or ""
            )

        elif event_type in ("response.completed", "response.incomplete"):
            response = _get(event, "response")
            result = parse_response(response)
            yield StreamChunk(
                finish_reason=result.finish_reason,
                tool_calls=result.message.tool_calls,
                usage=result.usage or None,
            )

        elif event_type == "response.failed":
            response = _get(event, "response")
            error = _get(response, "error")
            raise ResponsesStreamError(
                str(_get(error, "message") or "Responses stream failed")
            )

        elif event_type:
            unknown[event_type] += 1

    if unknown:
        logger.debug(
            "Ignored %d unrecognised Responses event(s): %s",
            sum(unknown.values()),
            dict(unknown),
        )


class ResponsesStreamError(RuntimeError):
    """The provider terminated a Responses stream with an error event."""


__all__ = [
    "ResponsesStreamError",
    "build_responses_input",
    "convert_response_format",
    "convert_tool_choice",
    "convert_tools",
    "map_usage",
    "parse_response",
    "sanitize_reasoning_item",
    "stream_to_chunks",
]
