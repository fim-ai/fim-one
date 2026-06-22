"""Workflow connector calls must leave a ConnectorCallLog trail (audit P0#3c).

Before the fix, the workflow CONNECTOR node built its ConnectorToolAdapter
without an ``on_call_complete`` callback, so connector calls made from a
workflow left no audit record — unlike the chat path. These tests pin the
wiring and the log row the callback writes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import true as _sa_true

import pytest

from fim_one.core.workflow.nodes import ConnectorExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.models.connector_call_log import ConnectorCallLog


def _fake_db_cm(added: list[Any], *, requires_confirmation: bool = False) -> AsyncMock:
    """A create_session() mock returning connector then action, capturing add()."""
    fake_connector = MagicMock(
        id="c1", name="C", base_url="http://x", auth_type="none", auth_config=None
    )
    fake_action = MagicMock(
        name="act",
        description="d",
        method="GET",
        path="/p",
        parameters_schema=None,
        request_body_template=None,
        response_extract=None,
        requires_confirmation=requires_confirmation,
    )
    conn_res = MagicMock()
    conn_res.scalar_one_or_none.return_value = fake_connector
    act_res = MagicMock()
    act_res.scalar_one_or_none.return_value = fake_action

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[conn_res, act_res])
    mock_session.add = MagicMock(side_effect=lambda o: added.append(o))

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.asyncio
async def test_connector_call_is_audited() -> None:
    added: list[Any] = []
    captured: dict[str, Any] = {}

    class FakeAdapter:
        def __init__(self, **kwargs: Any) -> None:
            captured["on_call_complete"] = kwargs.get("on_call_complete")

        async def run(self, **kwargs: Any) -> dict[str, Any]:
            cb = captured["on_call_complete"]
            assert cb is not None  # wiring: the audit callback must be passed
            await cb(
                connector_id="c1",
                connector_name="C",
                action_id="a1",
                action_name="act",
                request_method="GET",
                request_url="http://x/p",
                response_status=200,
                response_time_ms=5,
                success=True,
                error_message=None,
            )
            return {"ok": True}

    node = WorkflowNodeDef(
        id="conn_1",
        type=NodeType.CONNECTOR,
        data={"type": "CONNECTOR", "connector_id": "c1", "action_id": "a1"},
    )
    store = VariableStore()
    ctx = ExecutionContext(run_id="r", user_id="user-9", workflow_id="w")

    with (
        patch("fim_one.db.create_session", return_value=_fake_db_cm(added)),
        patch(
            "fim_one.web.visibility.resolve_visibility",
            new=AsyncMock(return_value=(_sa_true(), [], [])),
        ),
        patch(
            "fim_one.core.security.connector_credentials.resolve_connector_credentials",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "fim_one.core.tool.connector.adapter.ConnectorToolAdapter",
            FakeAdapter,
        ),
    ):
        result = await ConnectorExecutor().execute(node, store, ctx)

    assert result.status == NodeStatus.COMPLETED
    logs = [o for o in added if isinstance(o, ConnectorCallLog)]
    assert len(logs) == 1
    log = logs[0]
    assert log.user_id == "user-9"
    assert log.conversation_id is None  # no conversation in workflow context
    assert log.agent_id is None
    assert log.success is True


@pytest.mark.asyncio
async def test_confirmation_required_action_fails_closed() -> None:
    """A requires_confirmation action must not run unattended in a workflow (P0#3b).

    Previously the connector node hardcoded confirmation off and ran the action
    anyway. It now fails closed before resolving credentials or calling out.
    """
    added: list[Any] = []
    node = WorkflowNodeDef(
        id="conn_1",
        type=NodeType.CONNECTOR,
        data={"type": "CONNECTOR", "connector_id": "c1", "action_id": "a1"},
    )
    store = VariableStore()
    ctx = ExecutionContext(run_id="r", user_id="user-9", workflow_id="w")

    resolve = AsyncMock(return_value={})
    with (
        patch(
            "fim_one.db.create_session",
            return_value=_fake_db_cm(added, requires_confirmation=True),
        ),
        patch(
            "fim_one.web.visibility.resolve_visibility",
            new=AsyncMock(return_value=(_sa_true(), [], [])),
        ),
        patch(
            "fim_one.core.security.connector_credentials.resolve_connector_credentials",
            new=resolve,
        ),
    ):
        result = await ConnectorExecutor().execute(node, store, ctx)

    assert result.status == NodeStatus.FAILED
    assert "confirmation" in (result.error or "").lower()
    # Must short-circuit: no credential resolution, no audit log, no call.
    resolve.assert_not_awaited()
    assert not [o for o in added if isinstance(o, ConnectorCallLog)]


@pytest.mark.asyncio
async def test_connector_not_visible_to_runner_is_blocked() -> None:
    """A workflow node may only use connectors the runner can see (PR-1.3).

    The connector query is filtered by the runner's visibility (own +
    subscribed). When the referenced connector is not visible, the query
    returns no row and the node fails with a not-accessible error before
    resolving credentials — a shared workflow can't reach a connector the
    runner has no access to just by hardcoding its id.
    """
    # Connector query returns None: the visibility filter excluded it.
    conn_res = MagicMock()
    conn_res.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=conn_res)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    node = WorkflowNodeDef(
        id="conn_1",
        type=NodeType.CONNECTOR,
        data={"type": "CONNECTOR", "connector_id": "c1", "action_id": "a1"},
    )
    store = VariableStore()
    ctx = ExecutionContext(run_id="r", user_id="outsider", workflow_id="w")

    resolve = AsyncMock(return_value={})
    with (
        patch("fim_one.db.create_session", return_value=mock_cm),
        patch(
            "fim_one.web.visibility.resolve_visibility",
            new=AsyncMock(return_value=(_sa_true(), [], [])),
        ),
        patch(
            "fim_one.core.security.connector_credentials.resolve_connector_credentials",
            new=resolve,
        ),
    ):
        result = await ConnectorExecutor().execute(node, store, ctx)

    assert result.status == NodeStatus.FAILED
    assert "not" in (result.error or "").lower()
    resolve.assert_not_awaited()
