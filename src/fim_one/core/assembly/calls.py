"""The call record: one row per call a tool made to a connector or database.

:func:`call_recorder` returns the ``on_call_complete`` callback the tool
adapters invoke when a call finishes. Every entry point gets its recorder
here, so a call is recorded the same way whoever made it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .principal import CallContext

__all__ = ["RecordKind", "call_recorder"]

logger = logging.getLogger(__name__)

RecordKind = Literal["connector", "database"]


def call_recorder(ctx: CallContext, *, kind: RecordKind) -> Callable[..., Awaitable[None]]:
    """The ``on_call_complete`` callback for tools assembled for *ctx*.

    Each call writes one ``connector_call_logs`` row in a session of its
    own, attributed to the acting user and the conversation and agent the
    call was made in. A record that cannot be written is logged and dropped:
    it never fails the call it describes.
    """
    user_id = ctx.principal.user_id or None

    async def _record(**call: Any) -> None:
        try:
            from fim_one.db import create_session
            from fim_one.db.models.connector_call_log import ConnectorCallLog

            async with create_session() as session:
                session.add(
                    ConnectorCallLog(
                        connector_id=call.get("connector_id", ""),
                        connector_name=call.get("connector_name", ""),
                        action_id=call.get("action_id"),
                        action_name=call.get("action_name", ""),
                        conversation_id=ctx.conversation_id,
                        user_id=user_id,
                        agent_id=ctx.agent_id,
                        request_method=call.get("request_method", ""),
                        request_url=call.get("request_url", ""),
                        response_status=call.get("response_status"),
                        response_time_ms=call.get("response_time_ms"),
                        success=call.get("success", False),
                        error_message=call.get("error_message"),
                        scope_rules_applied=call.get("scope_rules_applied"),
                    )
                )
                await session.commit()
        except Exception:
            logger.debug("Failed to record %s call", kind, exc_info=True)

    return _record
