"""What may be quoted of an address a call went to.

:func:`strip_url_secrets` is what records, logs and error messages are
passed through before they quote a URL.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["strip_url_secrets"]

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
