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
  listed at :func:`launch_arg_reasons`. A secret passed as a bare
  positional argument with no known prefix, or after a flag whose name says
  nothing (``-k``, ``--id``), is not recognised.

Both lists lean towards flagging where the shape is ambiguous (any query
string counts), and name what they found (``*_reasons``) so that the owner
is told which part to look at. A false positive costs the owner a
decision (switch ``allow_fallback`` on, or move the value into ``env`` or
``headers``) before others can use the server; a miss puts the owner's
credential in someone else's hands.
"""

from __future__ import annotations

import re
import traceback
from collections.abc import Sequence
from itertools import pairwise
from typing import Any
from urllib.parse import unquote, urlsplit

__all__ = [
    "SECRET_NAME_WORDS",
    "address_carries_secret",
    "address_secret_reasons",
    "error_without_url_secrets",
    "launch_arg_reasons",
    "launch_args_carry_secret",
    "names_a_secret",
    "strip_path_secrets",
    "strip_url_secrets",
    "traceback_without_url_secrets",
    "url_carries_secret",
    "url_secret_reasons",
    "value_is_secret_shaped",
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


def strip_path_secrets(path: str | None) -> str | None:
    """A request path given without scheme or host, as it may be printed or stored.

    A connector action's path (``/v1/send?api_key=…``,
    ``/bot123456789:AAH…/sendMessage``) carries a credential in the same
    places a URL does. The query string and fragment are cut and a segment
    shaped like a token is written ``***``, as :func:`strip_url_secrets`
    does for the path of a URL.
    """
    if not path:
        return path
    bare = re.split(r"[?#]", path, maxsplit=1)[0]
    return "/".join(_masked_segment(segment) for segment in bare.split("/"))


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

# A flag, an assignment or a header is "named after a secret" when one of
# these is a whole word of its name. A name is cut into words at ``-``,
# ``_``, ``.`` and camelCase humps, so ``--api-key``, ``API_KEY`` and
# ``apiKey`` have the word ``key`` and ``--keyboard-layout``, ``--hotkey``
# and ``--monkey`` do not. The singular only: ``--max-tokens 100`` and
# ``--allowed-keys a,b`` say how many or which, not what the secret is.
SECRET_NAME_WORDS = (
    "token",
    "key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "bearer",
    "pat",
    "dsn",
    "connection",
    "connstr",
    "cookie",
    "session",
    # Written without a separator.
    "apikey",
    "accesskey",
    "secretkey",
    "privatekey",
    "authtoken",
    "accesstoken",
    "connectionstring",
)
# A name that starts with one of these switches something off: ``--no-auth``.
_NEGATING_WORDS = frozenset({"no", "disable", "disabled", "skip", "without"})
# A name that ends in one of these says where the secret is or how it is
# used, and its value is that: ``--key-file /path``, ``--auth-type basic``,
# ``--token-url https://…`` (a URL is judged as a URL).
_DESCRIBING_WORDS = frozenset(
    {
        "file",
        "path",
        "dir",
        "directory",
        "type",
        "mode",
        "method",
        "scheme",
        "name",
        "header",
        "prefix",
        "url",
        "uri",
        "endpoint",
        "host",
        "port",
        "timeout",
        "ttl",
        "length",
        "size",
        "limit",
        "count",
        "enabled",
        "required",
    }
)
# Values that switch something rather than authenticate.
_SWITCH_VALUES = frozenset({"true", "false", "yes", "no", "on", "off", "none", "null", "0", "1"})

# Values that are a credential whatever they are passed as. A prefix counts
# at the start of the argument or after a character that cannot be part of
# a token (``=``, ``:``, a space, a quote), so ``task-list`` is not ``sk-``.
_NOT_AFTER_TOKEN_CHAR = r"(?<![A-Za-z0-9_\-])"
_TOKEN_BODY = (
    r"(?:"
    # OpenAI-style, Stripe, GitHub, GitLab and Slack tokens: the prefix and
    # at least eight more characters.
    r"(?:sk-|sk_live_|sk_test_|ghp_|gho_|ghs_|ghu_|github_pat_|glpat-"
    r"|xoxb-|xoxp-|xoxa-|xapp-)[A-Za-z0-9_\-]{8,}"
    # Prefixes that also begin ordinary words (Notion, Hugging Face, npm,
    # Tavily, Firecrawl, HubSpot): sixteen more characters.
    r"|(?:ntn_|secret_|hf_|npm_|tvly-|fc-|pat-)[A-Za-z0-9_\-]{16,}"
    # A Google API key.
    r"|AIza[A-Za-z0-9_\-]{20,}"
    # An AWS access key id.
    r"|AKIA[0-9A-Z]{16}"
    # A JWT: base64url parts joined by dots, the first starting with ``{"``.
    r"|eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{4,}"
    r")"
)
_TOKEN_SHAPES = re.compile(_NOT_AFTER_TOKEN_CHAR + _TOKEN_BODY)
# In a launch argument a ``/`` before the prefix means a file path
# (``/data/sk-archive-2024``); a URL in an argument is judged segment by
# segment by the URL rule instead.
_TOKEN_IN_ARGUMENT = re.compile(r"(?<![A-Za-z0-9_\-/])" + _TOKEN_BODY)
# ``Bearer <token>``: the word alone (``--mode bearer``) or in a sentence
# (``bearer of news``) is not one.
_BEARER = re.compile(
    r"(?i)(?<![A-Za-z0-9_\-])bearer\s+(?:\$\S+|[A-Za-z0-9._~+/=\-]{8,})(?!\S*\s+\w)"
)
# ``NAME=value``, and ``Name: value`` as a header is written.
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*[=:]\s*(\S.*)$", re.DOTALL)
# ``Password=…`` / ``Pwd=…`` inside a connection string, and ``user:pass@``
# at the start of a DSN written without a scheme.
_CONNECTION_STRING = re.compile(r"(?i)(?<![A-Za-z])(?:password|pwd)\s*=\s*[^;\s]")
_DSN_USER_INFO = re.compile(r"^[^\s:@/=]+:[^\s@/]+@\S")
# An opaque value long enough to be a token whatever it is passed as.
_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_\-+=.]{32,}")
# Docker's way of handing a container its environment.
_ENV_FLAGS = frozenset({"-e", "--env"})
_NAME_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

# Why a URL is taken to carry a secret (:func:`url_secret_reasons`).
URL_USER_INFO = "url_user_info"
URL_QUERY = "url_query"
URL_FRAGMENT = "url_fragment"
URL_PATH_SEGMENT = "url_path_segment"
URL_PATH_PARAMETER = "url_path_parameter"
URL_TOKEN = "url_token"
URL_UNPARSEABLE = "url_unparseable"

# What joins a name to a token inside one path segment.
# A dot or a bracket joins one too (``hook.sk-live-…``, ``key.<32 hex>``,
# ``(token)sk-live-…``); the pieces of a file name (``report.final.json``,
# ``v1.2``) are judged one by one and none of them is a token.
_SEGMENT_PIECES = re.compile(r"[:=,@!$~|.()\[\]]")
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
    # A name and a token in one segment: ``bot123456789:AAH…`` (Telegram),
    # ``key=…``, ``v1,…``, ``user@…``, ``hook.…``. Each piece is judged on its own.
    pieces = [piece for piece in _SEGMENT_PIECES.split(segment) if piece]
    if len(pieces) > 1:
        return any(_segment_is_token(piece) for piece in pieces)
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


# Why launch arguments are taken to carry a secret
# (:func:`launch_arg_reasons`); a URL inside an argument gives the URL's own.
ARG_NAME = "arg_name"
ARG_TOKEN = "arg_token"
ARG_ENV = "arg_env"
ARG_CONNECTION_STRING = "arg_connection_string"


def _name_words(name: str) -> list[str]:
    """The words of a flag, variable or header name, lower-cased."""
    return [word.lower() for word in _NAME_WORDS.findall(name)]


def _names_a_secret(name: str) -> bool:
    """Whether *name* says its value is a secret (see :data:`SECRET_NAME_WORDS`)."""
    words = _name_words(name)
    # ``conn-str`` is ``connstr`` in two words.
    joined = [first + second for first, second in pairwise(words)]
    if not any(word in SECRET_NAME_WORDS for word in (*words, *joined)):
        return False
    return words[0] not in _NEGATING_WORDS and words[-1] not in _DESCRIBING_WORDS


def names_a_secret(name: str) -> bool:
    """Whether a field, flag, variable or header called *name* holds a secret.

    The same reading of a name the launch-argument rule uses
    (:data:`SECRET_NAME_WORDS`), for callers outside this module that must
    decide whether a value may be printed: ``api_key``, ``webhookSecret``
    and ``Authorization`` are, ``key_file`` and ``no_auth`` are not.
    """
    return bool(name) and _names_a_secret(name)


def value_is_secret_shaped(text: str | None) -> bool:
    """Whether *text* is a credential by its own shape, whatever it is called.

    A known token prefix, a ``Bearer`` value, a long opaque string, a
    connection string with a password, or a URL that carries a secret.
    """
    return bool(text) and bool(_value_reasons(str(text)))


def _holds_a_secret(name: str, value: str) -> bool:
    """Whether ``name value`` is a secret by its name and is not a mere switch."""
    return _names_a_secret(name) and value.strip().lower() not in _SWITCH_VALUES


def _value_reasons(text: str) -> list[str]:
    """Why *text* is a secret by its own shape: a token, or a URL that carries one."""
    reasons: list[str] = []
    for match in _URL_IN_TEXT.finditer(text):
        reasons.extend(url_secret_reasons(match.group(0).rstrip(_TRAILING_PUNCTUATION)))
    rest = _URL_IN_TEXT.sub(" ", text)
    if _TOKEN_IN_ARGUMENT.search(rest) or _BEARER.search(text) or _is_opaque(rest):
        reasons.append(ARG_TOKEN)
    if _CONNECTION_STRING.search(rest) or _DSN_USER_INFO.match(rest.strip()):
        reasons.append(ARG_CONNECTION_STRING)
    return reasons


def _is_opaque(text: str) -> bool:
    """Whether *text*, or what it assigns, is a long opaque value or a UUID.

    Thirty-two or more characters with letters and digits mixed in a run of
    eight (a hex digest, a base64 key), or a UUID: long enough to be a
    token whatever flag it follows, a one-letter one or none.
    """
    for candidate in {text.strip(), re.split(r"[=:]\s*", text.strip(), maxsplit=1)[-1]}:
        if _UUID.fullmatch(candidate):
            return True
        if _OPAQUE_VALUE.fullmatch(candidate) and any(
            len(run) >= 8 and re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run)
            for run in re.split(r"[-_.+=]", candidate)
        ):
            return True
    return False


def launch_arg_reasons(args: Sequence[Any] | None) -> list[str]:
    """Why the arguments a stdio server is started with carry a secret; empty when not.

    - ``arg_name``: a flag, an assignment or a header named after a secret
      is given a value: ``--api-key VALUE``, ``--api-key=VALUE``, the
      single-dash spelling of a word (``-token VALUE``), ``API_KEY=VALUE``,
      ``X-Api-Key: VALUE``. "Named after a secret": one of
      :data:`SECRET_NAME_WORDS` is a whole word of the name, the name does
      not start by negating (``--no-auth``) and does not end by describing
      (``--key-file``, ``--auth-type``). A one-letter flag (``-k VALUE``)
      says nothing. A flag with no value (the last argument, or followed by
      another flag) or with a switch for one (``true``, ``none``) holds
      nothing;
    - ``arg_token``: a value shaped like a known credential, wherever it
      stands: ``sk-…``, ``sk_live_…``, ``sk_test_…``, ``ghp_…``, ``gho_…``,
      ``ghs_…``, ``ghu_…``, ``github_pat_…``, ``glpat-…``, ``xoxb-…``,
      ``xoxp-…``, ``xoxa-…``, ``xapp-…`` (each with at least eight more
      characters), ``ntn_…``, ``secret_…``, ``hf_…``, ``npm_…``,
      ``tvly-…``, ``fc-…``, ``pat-…`` (sixteen more), ``AIza…``, an AWS
      access key id (``AKIA`` and sixteen capitals or digits), a JWT
      (``eyJ…`` with a dot), ``Bearer <token>``; or any value that is a
      UUID or thirty-two or more characters mixing letters and digits,
      whatever flag it follows;
    - ``arg_env``: ``-e NAME=VALUE`` / ``--env NAME=VALUE`` as Docker takes
      it, whatever the name; ``-e NAME`` alone holds nothing;
    - ``arg_connection_string``: ``Password=…`` or ``Pwd=…`` inside a
      value, or a DSN that starts ``user:pass@``;
    - the reasons of :func:`url_secret_reasons` for a URL that is an
      argument or stands inside one (``--endpoint=https://user:pass@host/``).
    """
    if not args:
        return []
    reasons: list[str] = []
    items = [str(arg) for arg in args]
    for index, arg in enumerate(items):
        reasons.extend(_value_reasons(arg))
        flag, _, inline = arg.partition("=")
        if flag in _ENV_FLAGS:
            # ``-e NAME=value`` / ``--env=NAME=value``: a value handed to
            # the container, whatever it is called. ``-e NAME`` alone passes
            # the variable through from the server's own environment.
            assigned = inline or (items[index + 1] if index + 1 < len(items) else "")
            name, has_value, value = assigned.partition("=")
            if name and has_value and value:
                reasons.append(ARG_ENV)
            continue
        if arg.startswith("-"):
            name, has_value, value = arg.lstrip("-").partition("=")
            if len(name) < 2:
                # ``-k`` says nothing about what follows; ``-token`` does.
                continue
            if not has_value:
                following = items[index + 1] if index + 1 < len(items) else ""
                value = "" if following.startswith("-") else following
            if value and _holds_a_secret(name, value):
                reasons.append(ARG_NAME)
            continue
        assignment = _ASSIGNMENT.match(arg)
        if assignment is not None and _holds_a_secret(assignment.group(1), assignment.group(2)):
            reasons.append(ARG_NAME)
    return list(dict.fromkeys(reasons))


def launch_args_carry_secret(args: Sequence[Any] | None) -> bool:
    """Whether the launch arguments carry a secret (:func:`launch_arg_reasons`)."""
    return bool(launch_arg_reasons(args))


def address_secret_reasons(server: Any) -> list[str]:
    """Why the address of MCP server *server* is taken to carry a secret; empty when not.

    The address is what the transport is opened with besides ``env`` and
    ``headers``: the launch arguments of a stdio server, the URL of an SSE
    or streamable-HTTP server. A row with any other transport cannot be
    connected; both are looked at. The reasons name the part, never its
    value, so they can be shown to the owner.
    """
    transport = getattr(server, "transport", None)
    url = getattr(server, "url", None)
    args = getattr(server, "args", None)
    if transport == "stdio":
        return launch_arg_reasons(args)
    if transport in ("sse", "streamable_http"):
        return url_secret_reasons(url)
    return list(dict.fromkeys([*url_secret_reasons(url), *launch_arg_reasons(args)]))


def address_carries_secret(server: Any) -> bool:
    """Whether the address of MCP server *server* carries a secret."""
    return bool(address_secret_reasons(server))
