"""MCP transport security policies.

A stdio MCP server is a command the application starts as a child process,
in its own container and with its own privileges.  The rules:

- ``ALLOW_STDIO_MCP`` switches the transport on for the instance (off by
  default).
- With it on, only a system administrator may create or edit a stdio
  server, and only a stdio server whose owner is (still) an active system
  administrator is ever started.  Everyone else may *use* one that an
  administrator shared; they cannot change what it runs.
- ``ALLOWED_STDIO_COMMANDS`` narrows the commands an administrator may
  configure.  It is a guard against typos, **not a security boundary**:
  ``python -c``, ``node -e`` and ``npx <any package>`` all run arbitrary
  code.  Enabling stdio means administrators can run arbitrary commands in
  the application container.
- A non-administrator who brings their own credential to a shared stdio
  server may only set the environment variables the administrator declared
  on it, and never one that changes what the process loads
  (:func:`stdio_env_violations`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

# Error codes (shared with the API's ``AppError`` namespace).
STDIO_MCP_DISABLED = "stdio_mcp_disabled"
STDIO_MCP_ADMIN_ONLY = "stdio_mcp_admin_only"
STDIO_MCP_OWNER_NOT_ADMIN = "stdio_mcp_owner_not_admin"
STDIO_MCP_ENV_NOT_ALLOWED = "stdio_mcp_env_not_allowed"

STDIO_ADMIN_ONLY_DETAIL = (
    "Stdio MCP servers run a command inside the application container; "
    "only a system administrator can create, edit, copy or publish one."
)

# Environment variables that decide what a process loads or runs, whatever
# its command line says.  A non-administrator may never set these on a stdio
# server, even when the administrator listed the name on the server.
_LOADER_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "ENV",
        "BASH_ENV",
        "IFS",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_EXTRA_CA_CERTS",
        "PERL5LIB",
        "PERL5OPT",
        "RUBYLIB",
        "RUBYOPT",
        "CLASSPATH",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "GCONV_PATH",
        "HOSTALIASES",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        # Where the process fetches from and whom it trusts: a proxy plus a
        # certificate store (or no verification) replaces the package that
        # `npx` / `uvx` downloads.
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "FTP_PROXY",
        # Where it writes scratch files, and programs it starts by name.
        "TMPDIR",
        "TMP",
        "TEMP",
        "EDITOR",
        "VISUAL",
        "PAGER",
        "BROWSER",
    }
)
_LOADER_ENV_PREFIXES = (
    "LD_",
    "DYLD_",
    "PYTHON",
    "NPM_CONFIG_",
    "NPX_",
    "YARN_",
    "PNPM_",
    "BUN_",
    "DENO_",
    "UV_",
    "PIP_",
    "VIRTUAL_ENV",
    "CONDA_",
    "GIT_",
    "DOCKER_",
)


def is_stdio_allowed() -> bool:
    """Check whether stdio MCP transport is permitted on this instance.

    Default: **False** (secure by default).  Set ``ALLOW_STDIO_MCP=true``
    to enable stdio servers.  Enabled means *administrators* may configure
    them; see the module docstring.
    """
    return os.getenv("ALLOW_STDIO_MCP", "").lower() in ("1", "true", "yes")


def is_loader_env_name(name: str) -> bool:
    """Whether the variable *name* changes what a child process loads or runs."""
    upper = name.strip().upper()
    return upper in _LOADER_ENV_NAMES or upper.startswith(_LOADER_ENV_PREFIXES)


def stdio_env_violations(
    declared: Iterable[str] | Mapping[str, Any] | None,
    supplied: Mapping[str, Any] | None,
) -> list[str]:
    """The names in *supplied* a non-administrator may not set on a stdio server.

    *declared* are the variable names the server's owner configured on it
    (the template a user's own credential fills in).  A supplied name is
    refused when the owner did not declare it, or when it is a loader
    variable (``PATH``, ``NODE_OPTIONS``, ``PYTHONPATH``, ``LD_PRELOAD``, …):
    with those, a credential would decide what the command executes.
    """
    allowed = set(declared or ())
    return sorted(
        name for name in (supplied or {}) if name not in allowed or is_loader_env_name(name)
    )


def get_allowed_stdio_commands() -> set[str]:
    """Return the set of allowed base command names for stdio MCP servers.

    Configured via ``ALLOWED_STDIO_COMMANDS`` (comma-separated).
    """
    raw = os.getenv(
        "ALLOWED_STDIO_COMMANDS",
        "npx,uvx,node,python,python3,deno,bun",
    )
    return {cmd.strip() for cmd in raw.split(",") if cmd.strip()}


def validate_stdio_command(command: str) -> None:
    """Validate that *command* is in the allowed list.

    Extracts the base name (e.g. ``/usr/bin/npx`` -> ``npx``) and checks
    against :func:`get_allowed_stdio_commands`.

    Raises:
        ValueError: If the command is not allowed.
    """
    if not command or not command.strip():
        raise ValueError("MCP stdio command must not be empty")

    base = PurePosixPath(command.strip()).name
    allowed = get_allowed_stdio_commands()
    if base not in allowed:
        raise ValueError(
            f"Command '{base}' is not in the allowed stdio command list: "
            f"{sorted(allowed)}. Configure via ALLOWED_STDIO_COMMANDS env var."
        )
