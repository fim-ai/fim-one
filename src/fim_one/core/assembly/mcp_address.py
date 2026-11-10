"""What an MCP server's address gives away.

An MCP server is reached at a URL (SSE, streamable HTTP) or started with a
command line (stdio). Both are the owner's, and both can carry a credential
the same way ``env`` and ``headers`` do: ``https://host/mcp?token=…``,
``https://user:pass@host/…``, ``--api-key sk-…``.

:func:`address_carries_secret` is the one place that decides whether a
server's address does. The credential rule (:mod:`.credentials`) asks it
before letting someone other than the owner connect with ``allow_fallback``
off, and the MCP server API asks it to warn the owner on save.
:func:`strip_url_secrets` is what records, logs and error messages are
passed through before they quote a URL.

The classification is by shape, not by knowledge of the server:

- a URL carries a secret when it has user-info, a query string or a
  fragment. A secret written as a path segment (``/mcp/3f9c…/sse``) is not
  recognised: nothing tells it from an ordinary path;
- a launch argument carries a secret when it matches one of the shapes
  listed at :func:`launch_args_carry_secret`. A secret passed as a bare
  positional argument with no known prefix, or after a flag whose name says
  nothing (``-k``, ``--id``), is not recognised.

Both lists err towards flagging. A false positive costs the owner a
decision (switch ``allow_fallback`` on, or move the value into ``env`` or
``headers``) before others can use the server; a miss puts the owner's
credential in someone else's hands.
"""

from __future__ import annotations

import re
import traceback
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "SECRET_NAME_WORDS",
    "address_carries_secret",
    "error_without_url_secrets",
    "launch_args_carry_secret",
    "strip_url_secrets",
    "traceback_without_url_secrets",
    "url_carries_secret",
]

# A URL inside free text: scheme://… up to whitespace or a closing quote/bracket.
_URL_IN_TEXT = re.compile(r"""[A-Za-z][A-Za-z0-9+.\-]*://[^\s'"<>)\]]+""")


def strip_url_secrets(text: str | None) -> str | None:
    """*text* with the user-info, query string and fragment cut from every URL in it.

    An error message quotes the URL that failed, and a URL can carry a
    credential (``https://user:pass@host/…``, ``…?api_key=…``). A record or
    a log keeps where the call went, not what authenticated it.
    """
    if not text:
        return text

    def _bare(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if ":" in host:  # an IPv6 literal
                host = f"[{host}]"
            if parts.port is not None:
                host = f"{host}:{parts.port}"
            return urlunsplit((parts.scheme, host, parts.path, "", ""))
        except ValueError:
            # Not parseable: cut it where the secrets would start.
            scheme, _, rest = url.partition("://")
            rest = rest.split("?", 1)[0].split("#", 1)[0]
            return f"{scheme}://{rest.rsplit('@', 1)[-1]}"

    return _URL_IN_TEXT.sub(_bare, text)


def error_without_url_secrets(exc: BaseException) -> str:
    """``Type: message`` of *exc*, safe to show or store.

    A connection error quotes the URL it failed for
    (``Client error '401 Unauthorized' for url 'https://host/mcp?token=…'``).
    """
    return strip_url_secrets(f"{type(exc).__name__}: {exc}") or type(exc).__name__


def traceback_without_url_secrets(exc: BaseException) -> str:
    """The traceback of *exc*, causes included, safe to log.

    In place of ``exc_info=True`` where the exception came from opening an
    MCP server: the messages along the chain can quote the server's URL.
    """
    return strip_url_secrets("".join(traceback.format_exception(exc))) or type(exc).__name__


# ---------------------------------------------------------------------------
# Does the address carry a secret?
# ---------------------------------------------------------------------------

# A flag or a ``NAME=value`` assignment whose name contains one of these
# (case-insensitive, anywhere in the name) is taken to hold a secret.
SECRET_NAME_WORDS = (
    "token",
    "key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "credential",
    "bearer",
)

# Values that are a credential whatever they are passed as. A prefix counts
# at the start of the argument or after a character that cannot be part of
# a token (``=``, ``:``, a space, a quote), so ``task-list`` is not ``sk-``.
_NOT_AFTER_TOKEN_CHAR = r"(?<![A-Za-z0-9_\-])"
_TOKEN_SHAPES = re.compile(
    _NOT_AFTER_TOKEN_CHAR + r"(?:"
    # OpenAI-style, GitHub and Slack tokens: the prefix and at least eight more characters.
    r"(?:sk-|ghp_|gho_|github_pat_|xoxb-|xoxp-)[A-Za-z0-9_\-]{8,}"
    # An AWS access key id.
    r"|AKIA[0-9A-Z]{16}"
    # A JWT: three base64url parts, the first starting with ``{"``.
    r"|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"
    r")"
)
_BEARER = re.compile(r"(?i)(?<![A-Za-z0-9_\-])bearer(?:\s+\S|$)")
# ``NAME=value``, and ``Name: value`` as a header is written.
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*[=:]\s*(\S.*)$", re.DOTALL)


def url_carries_secret(url: str | None) -> bool:
    """Whether *url* has user-info, a query string or a fragment.

    Each of them is where a credential is put in a URL, and none of them
    can be told from one by looking, so any of them counts. A URL that
    cannot be parsed counts as well. The path is not examined.
    """
    if not url or not url.strip():
        return False
    try:
        parts = urlsplit(url.strip())
        # ``.port`` is what raises for a malformed network location.
        _ = parts.port
    except ValueError:
        return True
    return bool(parts.username or parts.password or "@" in parts.netloc) or bool(
        parts.query or parts.fragment
    )


def _names_a_secret(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in SECRET_NAME_WORDS)


def _value_is_secret(text: str) -> bool:
    """Whether *text* holds a known token shape or a URL that carries a secret."""
    if _TOKEN_SHAPES.search(text) or _BEARER.search(text):
        return True
    return any(url_carries_secret(match.group(0)) for match in _URL_IN_TEXT.finditer(text))


def launch_args_carry_secret(args: Sequence[Any] | None) -> bool:
    """Whether the arguments a stdio server is started with carry a secret.

    An argument list does when any of these holds:

    - a flag named after a secret is given a value: ``--api-key VALUE``,
      ``--api-key=VALUE``, and the single-dash spelling of a whole word
      (``-token VALUE``, ``-password=VALUE``). "Named after a secret" is
      :data:`SECRET_NAME_WORDS` anywhere in the name. A one-letter flag
      (``-k VALUE``) is not: its name says nothing. A flag with no value
      (the last argument, or followed by another flag) holds nothing;
    - an assignment to such a name: ``API_KEY=VALUE``, or ``X-Api-Key: VALUE``
      as a header is written;
    - a URL with user-info, a query string or a fragment, alone or inside
      an argument (``--endpoint=https://user:pass@host/``);
    - a value shaped like a known credential, wherever it stands:
      ``sk-…``, ``ghp_…``, ``gho_…``, ``github_pat_…``, ``xoxb-…``,
      ``xoxp-…`` (each with at least eight more characters), an AWS access
      key id (``AKIA`` and sixteen capitals or digits), a JWT (``eyJ…`` in
      three dotted parts), or ``Bearer …``.
    """
    if not args:
        return False
    items = [str(arg) for arg in args]
    for index, arg in enumerate(items):
        if _value_is_secret(arg):
            return True
        if arg.startswith("-"):
            name, has_value, value = arg.lstrip("-").partition("=")
            # ``-k`` says nothing about what follows; ``-token`` does.
            if len(name) < 2 or not _names_a_secret(name):
                continue
            if has_value:
                if value:
                    return True
                continue
            following = items[index + 1] if index + 1 < len(items) else ""
            if following and not following.startswith("-"):
                return True
            continue
        assignment = _ASSIGNMENT.match(arg)
        if assignment is not None and _names_a_secret(assignment.group(1)):
            return True
    return False


def address_carries_secret(server: Any) -> bool:
    """Whether the address of MCP server *server* carries a secret.

    The address is what the transport is opened with besides ``env`` and
    ``headers``: the launch arguments of a stdio server, the URL of an SSE
    or streamable-HTTP server. A row with any other transport cannot be
    connected; both are looked at.
    """
    transport = getattr(server, "transport", None)
    url = getattr(server, "url", None)
    args = getattr(server, "args", None)
    if transport == "stdio":
        return launch_args_carry_secret(args)
    if transport in ("sse", "streamable_http"):
        return url_carries_secret(url)
    return url_carries_secret(url) or launch_args_carry_secret(args)
