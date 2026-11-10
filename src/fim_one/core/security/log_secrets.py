"""Keep what a URL authenticates with out of the server log.

The HTTP clients log every request with its full URL
(``HTTP Request: POST https://user:pass@host/mcp?token=…``), and the MCP
SDK logs the traceback of a failed connection, whose message names the URL
the same way. An MCP server's URL, a connector's base URL and a webhook can
all carry a credential there: in the user-info, the query string or a path
segment.

:func:`install_url_secret_log_filter` makes every log record of those
libraries pass through :func:`~fim_one.core.assembly.mcp_address.strip_url_secrets`
before any handler sees it. It works at the log record factory, not as a
filter on named loggers: a logger's filter does not apply to its children
(``mcp.client.sse``), and a handler added later would not have it. It is
installed when the web app configures logging and when the MCP client
module is imported, so each uvicorn worker, and any process that opens an
MCP server, has it.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

__all__ = ["FILTERED_LOGGERS", "install_url_secret_log_filter"]

# The logger trees whose records are stripped: the HTTP clients in use
# (``httpx`` for connectors, webhooks and model calls, ``httpx2`` under the
# MCP SDK, ``httpcore`` beneath both) and the MCP SDK itself.
FILTERED_LOGGERS = ("httpx", "httpx2", "httpcore", "mcp")

_MARK = "_fim_strips_url_secrets"


def _is_filtered(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in FILTERED_LOGGERS)


def _strip(record: logging.LogRecord) -> None:
    """Rewrite *record* in place so that no URL in it keeps a secret."""
    from fim_one.core.assembly.mcp_address import strip_url_secrets

    try:
        message = record.getMessage()
    except Exception:
        # Arguments that do not fit the format: nothing can be said about
        # what they would print, so they are not printed.
        message = str(record.msg)
    # The formatted text replaces format and arguments: an argument (a URL
    # object) would otherwise be formatted again, whole, by the handler.
    record.msg = strip_url_secrets(message) or ""
    record.args = None
    if record.exc_info:
        exc_info = record.exc_info
        if isinstance(exc_info, tuple) and exc_info[0] is not None:
            text = "".join(traceback.format_exception(*exc_info)).rstrip("\n")
            record.exc_text = strip_url_secrets(text)
        # A formatter prints ``exc_text`` when it is set and would render
        # ``exc_info`` again, unstripped, where it is not consulted.
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = strip_url_secrets(record.exc_text)
    if record.stack_info:
        record.stack_info = strip_url_secrets(record.stack_info)


def install_url_secret_log_filter() -> None:
    """Strip URL secrets from every record of :data:`FILTERED_LOGGERS`, from now on.

    Safe to call more than once and from several places; the factory that
    was installed before stays in the chain.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _MARK, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        if _is_filtered(record.name):
            try:
                _strip(record)
            except Exception:
                # Logging must not fail the call it describes, and must not
                # print what could not be checked.
                record.msg, record.args = "(log message withheld: it could not be checked)", None
                record.exc_info = record.exc_text = None
        return record

    setattr(factory, _MARK, True)
    logging.setLogRecordFactory(factory)
