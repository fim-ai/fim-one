"""Shared env-scrubbing utilities for shell and Python sandboxes."""
from __future__ import annotations

import os
import re

_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

_SENSITIVE_ENV_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^AWS_", re.IGNORECASE),
    re.compile(r"^OPENAI_", re.IGNORECASE),
    re.compile(r"^ANTHROPIC_", re.IGNORECASE),
    re.compile(r"^AZURE_", re.IGNORECASE),
    re.compile(r"^GOOGLE_", re.IGNORECASE),
    re.compile(r"^JINA_", re.IGNORECASE),
    re.compile(r"_KEY$", re.IGNORECASE),
    re.compile(r"_SECRET$", re.IGNORECASE),
    re.compile(r"_TOKEN$", re.IGNORECASE),
    re.compile(r"_PASSWORD$", re.IGNORECASE),
    re.compile(r"^DATABASE_URL$", re.IGNORECASE),
    re.compile(r"^REDIS_URL$", re.IGNORECASE),
    re.compile(r"^MONGO_URL$", re.IGNORECASE),
    re.compile(r"^DSN$", re.IGNORECASE),
    re.compile(r"^SHELL$", re.IGNORECASE),
    # Where the Docker daemon (or the sandbox proxy) is: nothing a sandboxed
    # command has any use for.
    re.compile(r"^DOCKER_", re.IGNORECASE),
)

_EXPLICIT_REMOVE = frozenset({
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL",
})


def build_safe_env(sandbox_dir: str) -> dict[str, str]:
    """Return a sanitised copy of os.environ for sandbox child processes."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if not any(pat.search(key) for pat in _SENSITIVE_ENV_PATTERNS):
            env[key] = value

    # Explicit belt-and-suspenders removal for highest-risk keys
    for key in _EXPLICIT_REMOVE:
        env.pop(key, None)

    env["HOME"] = sandbox_dir
    env["PATH"] = _SAFE_PATH
    env["TMPDIR"] = sandbox_dir
    return env
