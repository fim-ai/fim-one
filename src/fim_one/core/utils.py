"""Shared utility helpers for fim-one core modules."""

from __future__ import annotations

import json
import re
from typing import Any


_VALID_JSON_ESCAPES = frozenset('"\\/bfnrtu')


def _repair_json_strings(candidate: str) -> str:
    """Repair invalid escape sequences inside JSON string values.

    LLMs frequently emit LaTeX (``\\frac``, ``\\cdots``) or other
    backslash sequences that are not valid JSON escapes.  This helper
    walks the candidate string, doubling any backslash inside a quoted
    region that is **not** followed by a valid JSON escape character.
    It also replaces literal newlines / tabs with their escaped form.
    """
    out: list[str] = []
    in_str = False
    i = 0
    n = len(candidate)
    while i < n:
        ch = candidate[i]
        if in_str:
            if ch == '\\' and i + 1 < n:
                nxt = candidate[i + 1]
                if nxt in _VALID_JSON_ESCAPES:
                    # Valid escape — keep as-is.
                    out.append(ch)
                    out.append(nxt)
                    i += 2
                    continue
                else:
                    # Invalid escape like \frac — double the backslash.
                    out.append('\\\\')
                    i += 1
                    continue
            elif ch == '"':
                in_str = False
            elif ch == '\n':
                out.append('\\n')
                i += 1
                continue
            elif ch == '\r':
                out.append('\\r')
                i += 1
                continue
            elif ch == '\t':
                out.append('\\t')
                i += 1
                continue
        else:
            if ch == '"':
                in_str = True
        out.append(ch)
        i += 1
    return ''.join(out)


def extract_json_value(text: str) -> Any | None:
    """Try to extract any JSON value (object, array, etc.) from *text*.

    Handles common LLM output patterns:

    1. Pure JSON string.
    2. JSON wrapped in ``\\`\\`\\`json ... \\`\\`\\``` code fences.
    3. JSON embedded in prose (first balanced ``{`` to ``}`` or ``[`` to ``]``).

    Returns:
        A parsed JSON value (``dict``, ``list``, etc.) if valid JSON was found,
        otherwise ``None``.
    """
    text = text.strip()

    # 1. Direct parse.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 1b. Direct parse with escape repair (handles LaTeX like \frac).
    try:
        return json.loads(_repair_json_strings(text))
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Strip markdown code fences.
    #    Use greedy match to handle nested ``` inside the JSON value
    #    (e.g. the answer field may contain markdown code blocks).
    #    Try both greedy (last ```) and non-greedy (first ```) patterns.
    for fence_re in (
        r"```(?:json)?\s*\n?(.*)\n?\s*```",   # greedy — last closing fence
        r"```(?:json)?\s*\n?(.*?)```",          # non-greedy — first closing fence
    ):
        fence_match = re.search(fence_re, text, re.DOTALL)
        if not fence_match:
            continue
        inner = fence_match.group(1).strip()
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            return json.loads(_repair_json_strings(inner))
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Extract first balanced { ... } or [ ... ] block.
    #    The loop is string-aware: braces/brackets inside JSON string literals
    #    are ignored so that values like  "f'{v:.2f}%'"  don't corrupt the
    #    depth counter.  After a failed candidate we continue scanning
    #    from the next opening char instead of giving up immediately.
    for open_ch, close_ch in ("{", "}"), ("[", "]"):
        start = text.find(open_ch)
        while start != -1:
            depth = 0
            in_string = False
            i = start
            while i < len(text):
                ch = text[i]
                if in_string:
                    if ch == "\\":
                        i += 2  # skip escaped character
                        continue
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == open_ch:
                        depth += 1
                    elif ch == close_ch:
                        depth -= 1
                        if depth == 0:
                            candidate = text[start : i + 1]
                            try:
                                return json.loads(candidate)
                            except (json.JSONDecodeError, TypeError):
                                pass

                            # 3b. Repair common JSON issues inside string
                            # values: literal newlines and invalid escape
                            # sequences (e.g. LaTeX like \frac, \cdots).
                            repaired = _repair_json_strings(candidate)
                            try:
                                return json.loads(repaired)
                            except (json.JSONDecodeError, TypeError):
                                pass

                            # Candidate failed — try the next opening char.
                            break
                i += 1

            # Advance to the next opening char after the current start position.
            start = text.find(open_ch, start + 1)

    return None


def get_language_directive(preferred_language: str | None) -> str | None:
    """Return a soft language preference string, or ``None`` if *auto*.

    The directive is intentionally phrased as a *preference* rather than an
    absolute override so that agent-level instructions (e.g. a translation
    agent) can take precedence when the task itself requires a specific
    language behaviour.
    """
    if not preferred_language or preferred_language == "auto":
        return None
    directives = {
        "en": (
            "Language preference: The user prefers responses in English. "
            "Follow this preference unless the agent directive or the task "
            "itself requires a different language (e.g. translation)."
        ),
        "zh": (
            "Language preference: 用户偏好使用中文回复。"
            "请遵循此偏好，除非 Agent 指令或任务本身要求使用其他语言（如翻译任务）。"
        ),
    }
    return directives.get(preferred_language)


# Tags some models (Hermes / Qwen-style) emit when they improvise a tool call
# as plain text instead of using the structured function-calling channel —
# most often when reaching for a tool that is not registered.  Both the
# invocation side (``tool_call`` / ``function_call``) and the fabricated
# result side (``tool_response`` / ``tool_result`` / ``tool_outputs``) are
# covered.  Left unstripped, these blocks — and any base64/file dumps inside a
# fake ``<tool_response>`` — leak verbatim into the user-facing answer.
_TOOL_PROTOCOL_TAGS = "tool_call|tool_response|tool_result|tool_outputs?|function_call"
_TOOL_PROTOCOL_BLOCK_RE = re.compile(
    rf"<({_TOOL_PROTOCOL_TAGS})\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
# An opening invocation/result tag that is never closed: strip from the tag to
# the end of the text.  Only applied to the protocol tags above, so a dangling
# ``<tool_call>`` at the tail (with no answer after it) cannot leak its JSON.
_TOOL_PROTOCOL_UNCLOSED_RE = re.compile(
    rf"<({_TOOL_PROTOCOL_TAGS})\b[^>]*>.*\Z",
    re.DOTALL | re.IGNORECASE,
)
# Any remaining bare/closing protocol tag (without its partner).
_TOOL_PROTOCOL_DANGLING_RE = re.compile(
    rf"</?({_TOOL_PROTOCOL_TAGS})\b[^>]*>",
    re.IGNORECASE,
)


def strip_tool_protocol(text: str) -> str:
    """Remove model-emitted tool-call pseudo-protocol blocks from *text*.

    Some models emit tool invocations as literal ``<tool_call>{...}</tool_call>``
    text (often followed by a fabricated ``<tool_response>...</tool_response>``)
    instead of using the structured function-calling channel.  This noise must
    never reach the user, so we strip well-formed blocks, an unclosed trailing
    block, and any leftover dangling tags, then collapse the blank lines left
    behind.

    Returns the cleaned text.  May return an empty string when the content was
    *entirely* protocol noise — callers that need a non-empty answer should
    guard for that and fall back appropriately rather than emit the raw noise.
    """
    if not text:
        return text
    lowered = text.lower()
    if not any(
        marker in lowered
        for marker in ("<tool_", "</tool_", "<function_call", "</function_call")
    ):
        return text
    cleaned = _TOOL_PROTOCOL_BLOCK_RE.sub("", text)
    cleaned = _TOOL_PROTOCOL_UNCLOSED_RE.sub("", cleaned)
    cleaned = _TOOL_PROTOCOL_DANGLING_RE.sub("", cleaned)
    # Collapse the blank lines left behind by removed blocks.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_json(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from *text*.

    Handles common LLM output patterns:

    1. Pure JSON string.
    2. JSON wrapped in ``\\`\\`\\`json ... \\`\\`\\``` code fences.
    3. JSON embedded in prose (first balanced ``{`` to ``}``).

    Returns:
        A parsed ``dict`` if a valid JSON object was found, otherwise ``None``.
    """
    result = extract_json_value(text)
    if isinstance(result, dict):
        return result
    return None
