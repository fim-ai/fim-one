"""Copies of stored settings with their secret values masked.

Used wherever a settings object is shown to something other than the code
that consumes it: a model's prompt, a tool result, a log line.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "****"

# A key names a secret when one of its words is one of these.  Words are
# split on ``_``, ``-``, ``.`` and camelCase, so ``api_key``, ``apiKey`` and
# ``x-api-key`` count while ``max_tokens`` and ``keyboard`` do not.
_SECRET_WORDS = frozenset(
    {
        "apikey",
        "secret",
        "password",
        "passwd",
        "token",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "cookie",
    }
)
_WORD_SPLIT = re.compile(r"[_\-.\s]+|(?<=[a-z0-9])(?=[A-Z])")


def is_secret_key(key: str) -> bool:
    """Whether a settings key holds a secret by its name."""
    words = [w.lower() for w in _WORD_SPLIT.split(key) if w]
    if not words:
        return False
    joined = "".join(words)
    if joined.endswith("apikey") or joined in {"key", "privatekey", "accesskey", "secretkey"}:
        return True
    return any(word in _SECRET_WORDS for word in words)


def redact_secrets(value: Any) -> Any:
    """A deep copy of *value* with the values of secret keys replaced by ``****``.

    Dicts and lists are walked; an empty or ``None`` secret stays as it is,
    so a reader can still tell a missing value from a configured one.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_secret_key(key) and item not in (None, ""):
                out[key] = MASK
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value
