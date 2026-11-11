"""The call record: one row per call a tool made to an outside resource.

:func:`call_recorder` returns the ``on_call_complete`` callback the tool
adapters invoke when a call finishes. Every entry point gets its recorder
here, so a call to a connector, a database or an MCP server is recorded the
same way whoever made it: who, from where, through which agent or workflow
run (and which organization's), and with whose credential.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .mcp_address import strip_url_secrets

if TYPE_CHECKING:
    from .credentials import CredentialSource
    from .principal import CallContext

__all__ = ["RecordKind", "call_recorder"]

logger = logging.getLogger(__name__)

RecordKind = Literal["connector", "database", "mcp"]


def _where_it_went(url: Any) -> Any:
    """*url* without user-info, query string and fragment.

    A connector's base URL may carry a credential (``https://user:pass@host``,
    ``…?api_key=…``). The record keeps where the call went, and organization
    administrators read it; what authenticated the call is not part of that.
    Synthetic ``db://`` and ``mcp://`` addresses have none of the three and
    come back as they are.
    """
    if not isinstance(url, str) or not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]
    if not (parts.query or parts.fragment or "@" in parts.netloc):
        return url
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _fit(value: Any, limit: int) -> str:
    """*value* as text no longer than the column that stores it.

    PostgreSQL rejects an over-long value, and the record with it; a long
    action description must not cost the call its record.
    """
    return str(value or "")[:limit]


def call_recorder(
    ctx: CallContext,
    *,
    kind: RecordKind,
    credential_source: CredentialSource | Mapping[str, CredentialSource] | None = None,
) -> Callable[..., Awaitable[None]]:
    """The ``on_call_complete`` callback for tools assembled for *ctx*.

    Each call writes one ``connector_call_logs`` row in a session of its
    own. *credential_source* is whose credential the calls go out with; a
    tool that serves several resources passes a mapping keyed by resource id
    (connector id or MCP server id). A record that cannot be written is
    logged and dropped: it never fails the call it describes.
    """
    user_id = ctx.principal.user_id or None
    org_id = ctx.binding.org_id if ctx.binding is not None else None

    def _source_of(resource_id: str | None) -> str | None:
        if credential_source is None:
            return None
        if isinstance(credential_source, Mapping):
            source = credential_source.get(resource_id) if resource_id else None
            return str(source) if source is not None else None
        return str(credential_source)

    async def _record(**call: Any) -> None:
        try:
            from fim_one.db import create_session
            from fim_one.db.models.connector_call_log import ConnectorCallLog

            connector_id = call.get("connector_id") or None
            mcp_server_id = call.get("mcp_server_id") or None
            async with create_session() as session:
                session.add(
                    ConnectorCallLog(
                        resource_kind=kind,
                        connector_id=connector_id,
                        mcp_server_id=mcp_server_id,
                        connector_name=_fit(call.get("connector_name"), 200),
                        action_id=call.get("action_id"),
                        action_name=_fit(call.get("action_name"), 200),
                        conversation_id=ctx.conversation_id,
                        user_id=user_id,
                        org_id=org_id,
                        agent_id=ctx.agent_id,
                        workflow_run_id=ctx.workflow_run_id,
                        entry=ctx.entry,
                        credential_source=_source_of(connector_id or mcp_server_id),
                        request_method=_fit(call.get("request_method"), 10),
                        request_url=_fit(_where_it_went(call.get("request_url")), 1000),
                        response_status=call.get("response_status"),
                        response_time_ms=call.get("response_time_ms"),
                        success=call.get("success", False),
                        error_message=strip_url_secrets(call.get("error_message")),
                        scope_rules_applied=call.get("scope_rules_applied"),
                    )
                )
                await session.commit()
        except Exception as exc:
            # WARNING, so that an audit write that fails every time is seen.
            # Only the error's type: its text can echo the row (the URL
            # called, the error message stored).
            logger.warning("Failed to record a %s call: %s", kind, type(exc).__name__)

    return _record
