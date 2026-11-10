"""Binding two connectors, or two MCP servers, that would share tool names.

A connector's tools are named after its normalised name and an MCP server's
after its sanitised name. Two bound resources with the same result collide at
run time and binding order decides which one keeps the plain name, so the
agent endpoints refuse such a set when the binding list changes.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import fim_one.db.models  # noqa: F401
from fim_one.core.mcp.adapter import MCPToolAdapter, server_tool_prefix
from fim_one.core.tool.connector.adapter import ConnectorToolAdapter, connector_tool_prefix
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.user import User
from fim_one.web import create_app
from fim_one.web.auth import create_access_token

pytestmark = pytest.mark.timeout(60)

USER = "user-collide"


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-binding-name-collision-long")
    await init_db()
    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with create_session() as db:
        db.add(User(id=USER, email="u@x.io", username="user", password_hash="x"))
        await db.commit()
    token = create_access_token(USER, "u@x.io")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()),
            base_url="http://t",
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            yield http
    finally:
        await shutdown_db()
        os.unlink(path)


async def _connector(name: str, type_: str = "api") -> str:
    cid = str(uuid.uuid4())
    async with create_session() as db:
        db.add(
            Connector(
                id=cid,
                user_id=USER,
                name=name,
                type=type_,
                base_url="https://api.example.test",
                auth_type="none",
                is_active=True,
            )
        )
        await db.commit()
    return cid


async def _server(name: str) -> str:
    sid = str(uuid.uuid4())
    async with create_session() as db:
        db.add(
            MCPServer(
                id=sid,
                user_id=USER,
                name=name,
                transport="sse",
                url="https://mcp.example.test/sse",
                is_active=True,
            )
        )
        await db.commit()
    return sid


async def _stored_agent(**bindings: Any) -> str:
    """An agent written straight to the table, as one saved before the check."""
    aid = str(uuid.uuid4())
    async with create_session() as db:
        db.add(Agent(id=aid, user_id=USER, name="Old", status="draft", **bindings))
        await db.commit()
    return aid


def _error(resp: Any) -> dict[str, Any]:
    body: dict[str, Any] = resp.json()
    return body


class TestTheNormalisationIsTheOneToolNamesUse:
    def test_a_connector_tool_is_named_after_the_prefix(self) -> None:
        tool = ConnectorToolAdapter(
            connector_name="My CRM (prod)",
            connector_base_url="https://api.example.test",
            connector_auth_type="none",
            connector_auth_config=None,
            action_name="ping",
            action_description="",
            action_method="GET",
            action_path="/ping",
            action_parameters_schema=None,
            action_request_body_template=None,
            action_response_extract=None,
            action_requires_confirmation=False,
            on_call_complete=None,
        )
        assert connector_tool_prefix("My CRM (prod)") == "my_crm__prod"
        assert tool.name == "my_crm__prod__ping"

    def test_an_mcp_tool_is_named_after_the_prefix(self) -> None:
        async def _call(*_a: Any, **_k: Any) -> Any:
            return None

        tool = MCPToolAdapter("my server", {"name": "search"}, _call)
        assert server_tool_prefix("my server") == "my_server"
        assert tool.name == "my_server__search"


class TestCreate:
    async def test_two_connectors_with_the_same_normalised_name_are_refused(
        self, client: AsyncClient
    ) -> None:
        first = await _connector("CRM")
        second = await _connector("crm!")

        resp = await client.post(
            "/api/agents", json={"name": "A", "connector_ids": [first, second]}
        )

        assert resp.status_code == 400, resp.text
        body = _error(resp)
        assert body["error_code"] == "agent_connector_names_collide"
        assert body["error_args"]["first"] == "CRM"
        assert body["error_args"]["second"] == "crm!"
        assert body["error_args"]["tool_name"] == "crm"
        assert '"CRM"' in body["detail"] and '"crm!"' in body["detail"]

    async def test_connectors_with_distinct_names_are_bound(self, client: AsyncClient) -> None:
        ids = [await _connector("CRM"), await _connector("ERP")]

        resp = await client.post("/api/agents", json={"name": "A", "connector_ids": ids})

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["connector_ids"] == ids

    async def test_an_api_and_a_database_connector_may_share_a_name(
        self, client: AsyncClient
    ) -> None:
        # They are offered through different tools, each with its own names.
        ids = [await _connector("orders"), await _connector("orders", "database")]

        resp = await client.post("/api/agents", json={"name": "A", "connector_ids": ids})

        assert resp.status_code == 200, resp.text

    async def test_two_database_connectors_with_the_same_name_are_refused(
        self, client: AsyncClient
    ) -> None:
        ids = [await _connector("orders", "database"), await _connector("Orders", "database")]

        resp = await client.post("/api/agents", json={"name": "A", "connector_ids": ids})

        assert resp.status_code == 400, resp.text
        assert _error(resp)["error_code"] == "agent_connector_names_collide"

    async def test_names_with_no_ascii_letter_or_digit_do_not_collide(
        self, client: AsyncClient
    ) -> None:
        # Both normalise to "" and are then named after their ids.
        ids = [await _connector("客户系统"), await _connector("订单系统")]

        resp = await client.post("/api/agents", json={"name": "A", "connector_ids": ids})

        assert resp.status_code == 200, resp.text

    async def test_the_same_connector_listed_twice_is_not_a_collision(
        self, client: AsyncClient
    ) -> None:
        cid = await _connector("CRM")

        resp = await client.post("/api/agents", json={"name": "A", "connector_ids": [cid, cid]})

        assert resp.status_code == 200, resp.text

    async def test_two_mcp_servers_with_the_same_sanitised_name_are_refused(
        self, client: AsyncClient
    ) -> None:
        first = await _server("my server")
        second = await _server("my_server")

        resp = await client.post(
            "/api/agents", json={"name": "A", "mcp_server_ids": [first, second]}
        )

        assert resp.status_code == 400, resp.text
        body = _error(resp)
        assert body["error_code"] == "agent_mcp_server_names_collide"
        assert body["error_args"]["first"] == "my server"
        assert body["error_args"]["second"] == "my_server"
        assert body["error_args"]["tool_name"] == "my_server"

    async def test_mcp_server_names_are_compared_with_their_case(
        self, client: AsyncClient
    ) -> None:
        # Tool names keep the server name's case, so these two do not collide.
        ids = [await _server("Docs"), await _server("docs")]

        resp = await client.post("/api/agents", json={"name": "A", "mcp_server_ids": ids})

        assert resp.status_code == 200, resp.text


class TestUpdate:
    async def test_an_agent_that_already_collides_can_still_be_edited(
        self, client: AsyncClient
    ) -> None:
        ids = [await _connector("CRM"), await _connector("crm")]
        servers = [await _server("docs"), await _server("docs")]
        agent = await _stored_agent(connector_ids=ids, mcp_server_ids=servers)

        # The settings form sends both lists with every save.
        resp = await client.put(
            f"/api/agents/{agent}",
            json={"name": "Renamed", "connector_ids": ids, "mcp_server_ids": servers},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["name"] == "Renamed"

    async def test_changing_the_list_of_an_agent_that_collides_is_refused(
        self, client: AsyncClient
    ) -> None:
        ids = [await _connector("CRM"), await _connector("crm")]
        agent = await _stored_agent(connector_ids=ids)
        third = await _connector("ERP")

        resp = await client.put(f"/api/agents/{agent}", json={"connector_ids": [*ids, third]})

        assert resp.status_code == 400, resp.text
        assert _error(resp)["error_code"] == "agent_connector_names_collide"

    async def test_removing_one_of_the_two_resolves_it(self, client: AsyncClient) -> None:
        ids = [await _connector("CRM"), await _connector("crm")]
        agent = await _stored_agent(connector_ids=ids)

        resp = await client.put(f"/api/agents/{agent}", json={"connector_ids": ids[:1]})

        assert resp.status_code == 200, resp.text

    async def test_adding_a_colliding_connector_is_refused(self, client: AsyncClient) -> None:
        first = await _connector("CRM")
        agent = await _stored_agent(connector_ids=[first])
        second = await _connector("CRM")

        resp = await client.put(f"/api/agents/{agent}", json={"connector_ids": [first, second]})

        assert resp.status_code == 400, resp.text

    async def test_adding_a_colliding_mcp_server_is_refused_and_connectors_are_left_alone(
        self, client: AsyncClient
    ) -> None:
        ids = [await _connector("CRM"), await _connector("crm")]
        first = await _server("docs")
        agent = await _stored_agent(connector_ids=ids, mcp_server_ids=[first])
        second = await _server("docs")

        resp = await client.put(
            f"/api/agents/{agent}",
            json={"connector_ids": ids, "mcp_server_ids": [first, second]},
        )

        assert resp.status_code == 400, resp.text
        assert _error(resp)["error_code"] == "agent_mcp_server_names_collide"
