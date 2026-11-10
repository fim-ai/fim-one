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
  fragment, a path segment shaped like a credential (a UUID, a long hex or
  mixed string, a known token prefix) or a ``;name=value`` path parameter.
  The path part is a heuristic: a short or worded secret segment
  (``/mcp/hunter2/sse``) is not recognised, and an opaque id that is no
  secret is;
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
from urllib.parse import unquote, urlsplit

__all__ = [
    "SECRET_NAME_WORDS",
    "address_carries_secret",
    "error_without_url_secrets",
    "launch_args_carry_secret",
    "strip_url_secrets",
    "traceback_without_url_secrets",
    "url_carries_secret",
    "url_secret_reasons",
]

# A URL inside free text: scheme://… up to white space. A URL has no white
# space; anything else can be part of it, a quote or a bracket in a password
# included, so nothing else ends the match.
_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
# What follows a URL in a sentence rather than belonging to it. Only looked
# for at the very end of a match.
_TRAILING_PUNCTUATION = ".,;:!?)]}>'\"`"
# User-info and a ``?name=value`` tail outside anything the pass above took
# for a URL (no scheme, as in ``//user:pass@host/x``).
_STRAY_USERINFO = re.compile(r"//[^/\s@]*@")
_STRAY_QUERY = re.compile(r"\?[^\s=&?#]+=\S*")


def _bare_url(url: str) -> str:
    """*url* without user-info, query string and fragment; token-shaped segments masked."""
    scheme, _, rest = url.partition("://")
    # The query string and the fragment: everything from the first of them.
    rest = re.split(r"[?#]", rest, maxsplit=1)[0]
    authority, slash, path = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    elif "@" in path and not re.fullmatch(r"[^:]*(:\d*)?|\[[^\]]*\](:\d*)?", authority):
        # ``user:p/ss@host/x``: a password with a slash in it. No client
        # would send it there, but what is printed must not keep it.
        authority, slash, path = path.rsplit("@", 1)[1].partition("/")
    path = "/".join(_masked_segment(segment) for segment in path.split("/"))
    return f"{scheme}://{authority}{slash}{path}"


def _masked_segment(raw_segment: str) -> str:
    """A path segment as it may be printed: ``***`` where it is shaped like a secret."""
    reason = _segment_reason(raw_segment)
    if reason is None:
        return raw_segment
    name, has_parameters, _ = raw_segment.partition(";")
    if reason == URL_PATH_PARAMETER and has_parameters and _segment_reason(name) is None:
        # ``mcp;token=…``: the name stays, the parameters go.
        return f"{name};***"
    return "***"


def strip_url_secrets(text: str | None) -> str | None:
    """*text* with the user-info, query string and fragment cut from every URL in it.

    A path segment the address rule takes for a token
    (:func:`url_secret_reasons`: a UUID, a long hex or mixed string, a known
    token prefix) is printed as ``***``, and ``;name=value`` path parameters
    as ``;***``: hosted MCP servers and webhooks keep their secret there.

    An error message quotes the URL that failed, and a URL can carry a
    credential (``https://user:pass@host/…``, ``…?api_key=…``). A record or
    a log keeps where the call went, not what authenticated it.

    The secret may hold any character but white space, quotes and brackets
    included: a URL is taken up to the next white space, and only closing
    punctuation at its very end is given back to the sentence. What the
    text has after a cut query string is gone with it.
    """
    if not text:
        return text

    def _bare(match: re.Match[str]) -> str:
        url = match.group(0)
        core = url.rstrip(_TRAILING_PUNCTUATION)
        # The closing punctuation goes back to the sentence. Where it was
        # the end of a secret instead, it is a bracket or a quote of it.
        return _bare_url(core) + url[len(core) :]

    stripped = _URL_IN_TEXT.sub(_bare, text)
    stripped = _STRAY_USERINFO.sub("//", stripped)
    return _STRAY_QUERY.sub("", stripped)


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


# Why a URL is taken to carry a secret (:func:`url_secret_reasons`).
URL_USER_INFO = "url_user_info"
URL_QUERY = "url_query"
URL_FRAGMENT = "url_fragment"
URL_PATH_SEGMENT = "url_path_segment"
URL_PATH_PARAMETER = "url_path_parameter"
URL_TOKEN = "url_token"
URL_UNPARSEABLE = "url_unparseable"

_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_LONG_HEX = re.compile(r"[0-9a-fA-F]{16,}")
_LONG_ID = re.compile(r"[A-Za-z0-9_\-]{20,}")


def _segment_is_token(segment: str) -> bool:
    """Whether a path segment (percent-decoded) is shaped like a credential.

    A known token shape, a UUID, sixteen or more hex digits, or twenty or
    more of ``[A-Za-z0-9_-]`` in which letters and digits are mixed. That is
    how hosted MCP endpoints write a per-user secret into the path
    (``/api/mcp/s/<secret>/mcp``, ``/<32 hex>/sse``); the fixed paths of the
    well-known servers (``/mcp``, ``/sse``, ``/v1/sse``) are none of them.
    """
    if not segment:
        return False
    if _TOKEN_SHAPES.match(segment) or _UUID.fullmatch(segment) or _LONG_HEX.fullmatch(segment):
        return True
    if not _LONG_ID.fullmatch(segment):
        return False
    # Letters and digits mixed within one run: ``aB3dE5gH7jK9mN1pQ3sT5``,
    # ``ab12cd34-ef56gh78-…``. Words joined by dashes with a version among
    # them (``streamable-http-transport-v2``) are a name, not a token.
    return any(
        len(run) >= 8 and re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run)
        for run in re.split(r"[-_]", segment)
    )


def _segment_reason(raw_segment: str) -> str | None:
    """Why one raw path segment counts, or ``None``."""
    segment = unquote(raw_segment)
    name, has_parameters, parameters = segment.partition(";")
    # ``;name=value`` path parameters, and a ``?`` or ``#`` that was
    # percent-encoded into the path (``/mcp%3Ftoken=…``).
    if (has_parameters and "=" in parameters) or re.search(r"[?#]", segment):
        return URL_PATH_PARAMETER
    if _segment_is_token(name):
        return URL_PATH_SEGMENT
    return None


def url_secret_reasons(url: str | None) -> list[str]:
    """Why *url* is taken to carry a secret; empty when it is not.

    - user-info, a query string or a fragment: each is where a credential
      is put in a URL, and none can be told from one by looking;
    - a path segment shaped like a credential (:func:`_segment_is_token`),
      a ``;name=value`` path parameter, or a known token shape anywhere;
    - a URL that cannot be parsed.

    The path rules are a heuristic: an opaque segment may be an account or
    a workspace id, and a short or worded secret segment is not recognised.
    """
    if not url or not url.strip():
        return []
    try:
        parts = urlsplit(url.strip())
        # ``.port`` is what raises for a malformed network location.
        _ = parts.port
    except ValueError:
        return [URL_UNPARSEABLE]
    reasons: list[str] = []
    if parts.username or parts.password or "@" in parts.netloc:
        reasons.append(URL_USER_INFO)
    if parts.query:
        reasons.append(URL_QUERY)
    if parts.fragment:
        reasons.append(URL_FRAGMENT)
    for raw_segment in parts.path.split("/"):
        reason = _segment_reason(raw_segment)
        if reason is not None and reason not in reasons:
            reasons.append(reason)
    if not reasons and _TOKEN_SHAPES.search(unquote(url)):
        reasons.append(URL_TOKEN)
    return reasons


def url_carries_secret(url: str | None) -> bool:
    """Whether *url* is taken to carry a secret (:func:`url_secret_reasons`)."""
    return bool(url_secret_reasons(url))


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
