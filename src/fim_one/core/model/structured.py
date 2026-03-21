"""Unified structured-output LLM call with native-first degradation.

Provides ``structured_llm_call()`` — a single entry point for any call site
that needs the LLM to return data conforming to a JSON schema.  Three
extraction levels are attempted in order:

1. **Native Function Calling** — uses the LLM's built-in tool-call support.
2. **JSON Mode** — requests ``response_format={"type": "json_object"}``.
3. **Plain text** — parses JSON from free-form content, with optional regex
   fallback.

Each text-based level retries once with a reformat prompt before falling to
the next.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Literal, TypeVar

from fim_one.core.model.base import BaseLLM
from fim_one.core.model.types import ChatMessage
from fim_one.core.utils import extract_json

logger = logging.getLogger(__name__)

T = TypeVar("T")
_SENTINEL = object()

_REFORMAT_PROMPT = """\
Your previous response could not be parsed as valid JSON. \
Please respond with ONLY a JSON object matching the requested schema — \
no markdown, no explanation, no code fences."""


class StructuredOutputError(ValueError):
    """All extraction levels failed and no ``default_value`` was provided.

    Inherits from ``ValueError`` for backward compatibility with callers
    that already catch ``ValueError`` (e.g. the DAG planner).
    """


@dataclass
class StructuredCallResult(Generic[T]):
    """Result from :func:`structured_llm_call`.

    Attributes:
        value: The parsed (and optionally transformed) result.
        raw_data: The raw ``dict`` extracted from the LLM response.
        level_used: Which extraction level succeeded.
        llm_calls: Total number of LLM calls made (including retries).
        total_usage: Accumulated token usage across all calls.
    """

    value: T
    raw_data: dict[str, Any]
    level_used: Literal["native_fc", "json_mode", "plain_text"]
    llm_calls: int = 1
    total_usage: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _accumulate_usage(
    acc: dict[str, int],
    new: dict[str, int] | None,
) -> dict[str, int]:
    """Merge two usage dicts by summing matching keys."""
    if not new:
        return acc
    if not acc:
        return dict(new)
    return {
        k: acc.get(k, 0) + new.get(k, 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _build_tool_def(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI-style function tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Return structured data matching the schema.",
            "parameters": schema,
        },
    }


def _transform(
    data: dict[str, Any],
    parse_fn: Callable[[dict], T] | None,
) -> T | None:
    """Apply *parse_fn* if provided.

    All exceptions from *parse_fn* are caught so the degradation chain
    in ``structured_llm_call`` can continue to the next level.  A
    ``parse_fn`` that cannot handle the data should return ``None``
    (preferred) or raise any exception — either way the next extraction
    level will be attempted.
    """
    if parse_fn is None:
        return data  # type: ignore[return-value]
    try:
        return parse_fn(data)
    except ValueError:
        logger.warning("parse_fn raised ValueError on data: %.300s", data, exc_info=True)
        return None
    except Exception:
        logger.warning("parse_fn raised on data: %.200s", data, exc_info=True)
        return None


async def _call_llm(
    llm: BaseLLM,
    messages: list[ChatMessage],
    schema: dict[str, Any],
    fn_name: str,
    level: str,
    *,
    regex_fallback: Callable[[str], dict | None] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, int] | None]:
    """One LLM call at the specified level.

    Returns:
        ``(extracted_data, raw_content, usage)``
    """
    try:
        if level == "native_fc":
            # Forced tool_choice is incompatible with Anthropic's thinking
            # mode (returns 400).  Explicitly disable reasoning so native FC
            # always has a chance to succeed.
            result = await llm.chat(
                messages,
                tools=[_build_tool_def(fn_name, schema)],
                tool_choice={
                    "type": "function",
                    "function": {"name": fn_name},
                },
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=None,
            )
            # Primary: extract from tool_calls
            if result.message.tool_calls:
                args = result.message.tool_calls[0].arguments
                if isinstance(args, dict):
                    return args, json.dumps(args), result.usage
            # Fallback: some models put JSON in content even with tool_choice
            content = result.message.content or ""
            return extract_json(content), content, result.usage

        # json_mode / plain_text share the same call pattern
        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if level == "json_mode":
            kwargs["response_format"] = {"type": "json_object"}

        result = await llm.chat(messages, **kwargs)
        content = result.message.content or ""
        data = extract_json(content)
        if data is None and regex_fallback:
            data = regex_fallback(content)
        return data, content, result.usage

    except Exception:
        log_fn = logger.warning if level == "native_fc" else logger.debug
        log_fn(
            "structured_llm_call: %s call raised",
            level,
            exc_info=True,
        )
        return None, "", None


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


async def structured_llm_call(
    llm: BaseLLM,
    messages: list[ChatMessage],
    schema: dict[str, Any],
    function_name: str,
    *,
    parse_fn: Callable[[dict], T] | None = None,
    regex_fallback: Callable[[str], dict | None] | None = None,
    default_value: T | Any = _SENTINEL,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> StructuredCallResult[T]:
    """Structured LLM call with 3-level degradation.

    Tries Native FC → JSON Mode → Plain text, based on ``llm.abilities``.
    Each text-based level retries once with a reformat prompt before
    falling to the next.

    Args:
        llm: The LLM instance to call.
        messages: The conversation messages.
        schema: JSON Schema for the expected response structure.
        function_name: Name for the virtual function (used in native FC).
        parse_fn: Optional transform from raw dict to domain object ``T``.
            If ``None``, the raw dict is returned as ``value``.
            Failures in *parse_fn* (including ``ValueError``) are caught
            so the degradation chain can continue.
        regex_fallback: Optional extractor invoked at the plain-text level
            when ``extract_json`` fails.
        default_value: Returned when **all** levels fail.  If not provided,
            :class:`StructuredOutputError` is raised on total failure.
        temperature: Optional temperature override.
        max_tokens: Optional max_tokens override.

    Returns:
        A :class:`StructuredCallResult` with the parsed value and metadata.

    Raises:
        StructuredOutputError: When all levels fail and no *default_value*.
    """
    abilities = llm.abilities
    usage: dict[str, int] = {}
    calls = 0
    last_content = ""

    # Build the level list based on LLM capabilities.
    levels: list[Literal["native_fc", "json_mode", "plain_text"]] = []
    if abilities.get("tool_call", False):
        levels.append("native_fc")
    if abilities.get("json_mode", False):
        levels.append("json_mode")
    levels.append("plain_text")

    for level in levels:
        # Only plain_text uses regex_fallback.
        rfb = regex_fallback if level == "plain_text" else None

        # --- First attempt ---
        data, content, call_usage = await _call_llm(
            llm,
            messages,
            schema,
            function_name,
            level,
            regex_fallback=rfb,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        calls += 1
        usage = _accumulate_usage(usage, call_usage)
        if content:
            last_content = content

        if data is not None:
            logger.info(
                "structured_llm_call: level=%s extracted data keys=%s",
                level,
                list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )
            value = _transform(data, parse_fn)
            if value is not None:
                return StructuredCallResult(
                    value=value,
                    raw_data=data,
                    level_used=level,
                    llm_calls=calls,
                    total_usage=usage,
                )

        # --- Retry with reformat prompt (skip for native FC) ---
        if level != "native_fc" and content:
            retry_msgs = messages + [
                ChatMessage(role="assistant", content=content),
                ChatMessage(role="user", content=_REFORMAT_PROMPT),
            ]
            data2, _, retry_usage = await _call_llm(
                llm,
                retry_msgs,
                schema,
                function_name,
                level,
                regex_fallback=rfb,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            calls += 1
            usage = _accumulate_usage(usage, retry_usage)

            if data2 is not None:
                value = _transform(data2, parse_fn)
                if value is not None:
                    return StructuredCallResult(
                        value=value,
                        raw_data=data2,
                        level_used=level,
                        llm_calls=calls,
                        total_usage=usage,
                    )

        logger.debug("structured_llm_call: level '%s' exhausted", level)

    # --- All levels failed ---
    if default_value is not _SENTINEL:
        return StructuredCallResult(
            value=default_value,
            raw_data={},
            level_used="plain_text",
            llm_calls=calls,
            total_usage=usage,
        )

    preview = last_content[:200] + "..." if len(last_content) > 200 else last_content
    raise StructuredOutputError(
        f"All structured extraction levels failed. Last content: {preview}"
    )
