"""Tests for the Market dependency analyzer.

Covers: credential schema extraction, DependencyManifest operations,
and the main resolve_solution_dependencies function with mocked DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.web.dependency_analyzer import (
    ConnectionDep,
    ContentDep,
    DependencyManifest,
    extract_connector_credential_schema,
    extract_mcp_credential_schema,
    resolve_solution_dependencies,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight ORM stand-ins
# ---------------------------------------------------------------------------


def _fake_connector(
    *,
    id: str = "conn-1",
    name: str = "Test Connector",
    auth_type: str = "none",
    base_url: str | None = None,
    auth_config: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        name=name,
        auth_type=auth_type,
        base_url=base_url,
        auth_config=auth_config,
    )


def _fake_mcp_server(
    *,
    id: str = "srv-1",
    name: str = "Test MCP",
    transport: str = "stdio",
    env: dict | None = None,
    headers: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        name=name,
        transport=transport,
        env=env,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# extract_connector_credential_schema
# ---------------------------------------------------------------------------


class TestExtractConnectorCredentialSchema:
    def test_none_auth(self) -> None:
        c = _fake_connector(auth_type="none")
        schema = extract_connector_credential_schema(c)
        assert schema == {}

    def test_bearer_auth(self) -> None:
        c = _fake_connector(auth_type="bearer")
        schema = extract_connector_credential_schema(c)
        assert "api_key" in schema
        assert schema["api_key"]["type"] == "password"
        assert schema["api_key"]["required"] is True

    def test_basic_auth(self) -> None:
        c = _fake_connector(auth_type="basic")
        schema = extract_connector_credential_schema(c)
        assert "username" in schema
        assert "password" in schema
        assert schema["username"]["type"] == "text"
        assert schema["password"]["type"] == "password"

    def test_api_key_auth(self) -> None:
        c = _fake_connector(auth_type="api_key")
        schema = extract_connector_credential_schema(c)
        assert "api_key" in schema
        assert schema["api_key"]["type"] == "password"
        assert schema["api_key"]["required"] is True

    def test_oauth2_auth(self) -> None:
        c = _fake_connector(auth_type="oauth2")
        schema = extract_connector_credential_schema(c)
        assert "client_id" in schema
        assert "client_secret" in schema
        assert schema["client_id"]["type"] == "text"
        assert schema["client_secret"]["type"] == "password"

    def test_base_url_included(self) -> None:
        c = _fake_connector(auth_type="none", base_url="https://api.example.com")
        schema = extract_connector_credential_schema(c)
        assert "base_url" in schema
        assert schema["base_url"]["required"] is False
        assert schema["base_url"]["default"] == "https://api.example.com"

    def test_auth_config_extra_fields(self) -> None:
        c = _fake_connector(
            auth_type="bearer",
            auth_config={"api_version": "v2", "custom_secret": "xxx"},
        )
        schema = extract_connector_credential_schema(c)
        # bearer's api_key should still be there
        assert "api_key" in schema
        # extra fields from auth_config
        assert "api_version" in schema
        assert schema["api_version"]["type"] == "text"
        assert "custom_secret" in schema
        assert schema["custom_secret"]["type"] == "password"  # contains SECRET

    def test_auth_config_does_not_override_existing(self) -> None:
        c = _fake_connector(
            auth_type="bearer",
            auth_config={"api_key": "should-not-override"},
        )
        schema = extract_connector_credential_schema(c)
        # api_key should still have the standard definition, not overridden
        assert schema["api_key"]["required"] is True

    def test_unknown_auth_type(self) -> None:
        c = _fake_connector(auth_type="custom_unknown")
        schema = extract_connector_credential_schema(c)
        assert schema == {}

    def test_none_value_auth_type(self) -> None:
        """When auth_type is None (not set), treat as 'none'."""
        c = SimpleNamespace(id="c1", name="C", auth_type=None, base_url=None, auth_config=None)
        schema = extract_connector_credential_schema(c)
        assert schema == {}


# ---------------------------------------------------------------------------
# extract_mcp_credential_schema
# ---------------------------------------------------------------------------


class TestExtractMCPCredentialSchema:
    def test_empty(self) -> None:
        server = _fake_mcp_server(env=None, headers=None)
        schema = extract_mcp_credential_schema(server)
        assert schema == {}

    def test_env_secret_keys(self) -> None:
        server = _fake_mcp_server(env={"OPENAI_API_KEY": "sk-...", "MODEL_NAME": "gpt-4"})
        schema = extract_mcp_credential_schema(server)
        assert "OPENAI_API_KEY" in schema
        assert schema["OPENAI_API_KEY"]["type"] == "password"
        assert "MODEL_NAME" in schema
        assert schema["MODEL_NAME"]["type"] == "text"

    def test_headers(self) -> None:
        server = _fake_mcp_server(headers={"Authorization": "Bearer xxx", "X-API-TOKEN": "yyy"})
        schema = extract_mcp_credential_schema(server)
        assert "Authorization" in schema
        assert schema["Authorization"]["type"] == "text"  # no secret keyword
        assert "X-API-TOKEN" in schema
        assert schema["X-API-TOKEN"]["type"] == "password"  # contains TOKEN

    def test_env_and_headers_combined(self) -> None:
        server = _fake_mcp_server(
            env={"DB_PASSWORD": "secret"},
            headers={"X-Custom": "val"},
        )
        schema = extract_mcp_credential_schema(server)
        assert len(schema) == 2
        assert "DB_PASSWORD" in schema
        assert schema["DB_PASSWORD"]["type"] == "password"
        assert "X-Custom" in schema
        assert schema["X-Custom"]["type"] == "text"

    def test_headers_do_not_override_env(self) -> None:
        """If the same key exists in both env and headers, env wins."""
        server = _fake_mcp_server(
            env={"SHARED_KEY": "from-env"},
            headers={"SHARED_KEY": "from-headers"},
        )
        schema = extract_mcp_credential_schema(server)
        assert len(schema) == 1
        assert "SHARED_KEY" in schema

    def test_empty_dicts(self) -> None:
        server = _fake_mcp_server(env={}, headers={})
        schema = extract_mcp_credential_schema(server)
        assert schema == {}


# ---------------------------------------------------------------------------
# DependencyManifest
# ---------------------------------------------------------------------------


class TestDependencyManifest:
    def test_deduplicate_content(self) -> None:
        m = DependencyManifest(
            content_deps=[
                ContentDep("knowledge_base", "kb-1", "KB One"),
                ContentDep("knowledge_base", "kb-1", "KB One"),
                ContentDep("knowledge_base", "kb-2", "KB Two"),
            ]
        )
        m.deduplicate()
        assert len(m.content_deps) == 2
        ids = {d.resource_id for d in m.content_deps}
        assert ids == {"kb-1", "kb-2"}

    def test_deduplicate_connection(self) -> None:
        m = DependencyManifest(
            connection_deps=[
                ConnectionDep("connector", "c-1", "Conn", {}),
                ConnectionDep("connector", "c-1", "Conn", {}),
                ConnectionDep("mcp_server", "s-1", "MCP", {}),
            ]
        )
        m.deduplicate()
        assert len(m.connection_deps) == 2

    def test_deduplicate_preserves_order(self) -> None:
        m = DependencyManifest(
            content_deps=[
                ContentDep("knowledge_base", "kb-2", "Second"),
                ContentDep("knowledge_base", "kb-1", "First"),
                ContentDep("knowledge_base", "kb-2", "Second"),
            ]
        )
        m.deduplicate()
        assert m.content_deps[0].resource_id == "kb-2"
        assert m.content_deps[1].resource_id == "kb-1"

    def test_merge(self) -> None:
        m1 = DependencyManifest(
            content_deps=[ContentDep("knowledge_base", "kb-1", "KB")],
            connection_deps=[ConnectionDep("connector", "c-1", "Conn", {})],
        )
        m2 = DependencyManifest(
            content_deps=[ContentDep("skill", "sk-1", "Skill")],
            connection_deps=[ConnectionDep("mcp_server", "s-1", "MCP", {})],
        )
        m1.merge(m2)
        assert len(m1.content_deps) == 2
        assert len(m1.connection_deps) == 2

    def test_to_dict(self) -> None:
        m = DependencyManifest(
            content_deps=[ContentDep("knowledge_base", "kb-1", "My KB")],
            connection_deps=[
                ConnectionDep("connector", "c-1", "My Conn", {"api_key": {"type": "password"}})
            ],
        )
        d = m.to_dict()
        assert len(d["content_deps"]) == 1
        assert d["content_deps"][0]["resource_type"] == "knowledge_base"
        assert d["content_deps"][0]["resource_id"] == "kb-1"
        assert d["content_deps"][0]["resource_name"] == "My KB"
        assert len(d["connection_deps"]) == 1
        assert d["connection_deps"][0]["credential_schema"] == {"api_key": {"type": "password"}}

    def test_to_dict_empty(self) -> None:
        d = DependencyManifest().to_dict()
        assert d == {"content_deps": [], "connection_deps": []}


# ---------------------------------------------------------------------------
# resolve_solution_dependencies — mocked DB
# ---------------------------------------------------------------------------


def _mock_db_fetch(resource_map: dict[str, object]) -> AsyncMock:
    """Create a mock AsyncSession whose ``execute()`` returns rows from *resource_map*.

    ``resource_map`` maps resource IDs to mock ORM objects.  Any query
    containing a ``where(model.id == <id>)`` clause will return the
    corresponding object (or None).
    """
    db = AsyncMock()

    async def _execute(stmt):
        # Rough heuristic: extract the id from the compiled SQL params
        result = MagicMock()
        # Try to get the ID from the compiled statement
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        compiled_str = str(compiled)

        # Search for a known resource_id in the query string
        found = None
        for rid, obj in resource_map.items():
            if rid in compiled_str:
                found = obj
                break
        result.scalar_one_or_none.return_value = found
        return result

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    return db


class TestResolveAgentDependencies:
    @pytest.mark.asyncio
    async def test_agent_with_kb_and_connector(self) -> None:
        agent = SimpleNamespace(
            id="agt-1",
            name="Test Agent",
            kb_ids=["kb-1"],
            connector_ids=["conn-1"],
            mcp_server_ids=None,
            skill_ids=None,
        )
        kb = SimpleNamespace(id="kb-1", name="My KB")
        conn = _fake_connector(id="conn-1", name="My Connector", auth_type="bearer")

        resource_map = {"agt-1": agent, "kb-1": kb, "conn-1": conn}
        db = _mock_db_fetch(resource_map)

        manifest = await resolve_solution_dependencies("agent", "agt-1", db)

        assert len(manifest.content_deps) == 1
        assert manifest.content_deps[0].resource_id == "kb-1"
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_id == "conn-1"
        assert "api_key" in manifest.connection_deps[0].credential_schema

    @pytest.mark.asyncio
    async def test_agent_not_found(self) -> None:
        db = _mock_db_fetch({})
        manifest = await resolve_solution_dependencies("agent", "missing", db)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0

    @pytest.mark.asyncio
    async def test_agent_with_empty_ids(self) -> None:
        agent = SimpleNamespace(
            id="agt-1", name="Agent", kb_ids=None, connector_ids=None, mcp_server_ids=None, skill_ids=None
        )
        db = _mock_db_fetch({"agt-1": agent})
        manifest = await resolve_solution_dependencies("agent", "agt-1", db)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0


class TestResolveSkillDependencies:
    @pytest.mark.asyncio
    async def test_skill_with_connector_ref(self) -> None:
        skill = SimpleNamespace(
            id="sk-1",
            name="Test Skill",
            resource_refs=[
                {"type": "connector", "id": "conn-1", "name": "C1"},
                {"type": "other", "id": "x-1"},
            ],
        )
        conn = _fake_connector(id="conn-1", name="C1", auth_type="api_key")
        db = _mock_db_fetch({"sk-1": skill, "conn-1": conn})

        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_id == "conn-1"

    @pytest.mark.asyncio
    async def test_skill_with_kb_ref(self) -> None:
        """KB refs should appear as content_deps (auto-included for subscribers)."""
        skill = SimpleNamespace(
            id="sk-1",
            name="Test Skill",
            resource_refs=[
                {"type": "knowledge_base", "id": "kb-1", "name": "KB"},
            ],
        )
        kb = SimpleNamespace(id="kb-1", name="KB")
        db = _mock_db_fetch({"sk-1": skill, "kb-1": kb})

        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        assert len(manifest.content_deps) == 1
        assert manifest.content_deps[0].resource_type == "knowledge_base"
        assert manifest.content_deps[0].resource_id == "kb-1"
        assert len(manifest.connection_deps) == 0

    @pytest.mark.asyncio
    async def test_skill_with_mcp_server_ref(self) -> None:
        """MCP server refs should appear as connection_deps (requires credentials)."""
        skill = SimpleNamespace(
            id="sk-1",
            name="Test Skill",
            resource_refs=[
                {"type": "mcp_server", "id": "srv-1", "name": "MCP"},
            ],
        )
        srv = _fake_mcp_server(id="srv-1", name="MCP", env={"API_KEY": "xxx"})
        db = _mock_db_fetch({"sk-1": skill, "srv-1": srv})

        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_type == "mcp_server"
        assert manifest.connection_deps[0].resource_id == "srv-1"
        assert "API_KEY" in manifest.connection_deps[0].credential_schema

    @pytest.mark.asyncio
    async def test_skill_with_mixed_refs(self) -> None:
        """Skills with KB + Connector + MCP should classify deps correctly."""
        skill = SimpleNamespace(
            id="sk-1",
            name="Test Skill",
            resource_refs=[
                {"type": "knowledge_base", "id": "kb-1", "name": "KB"},
                {"type": "connector", "id": "conn-1", "name": "C1"},
                {"type": "mcp_server", "id": "srv-1", "name": "MCP"},
                {"type": "other", "id": "x-1"},
            ],
        )
        kb = SimpleNamespace(id="kb-1", name="KB")
        conn = _fake_connector(id="conn-1", name="C1", auth_type="api_key")
        srv = _fake_mcp_server(id="srv-1", name="MCP", env={"TOKEN": "t"})
        db = _mock_db_fetch({"sk-1": skill, "kb-1": kb, "conn-1": conn, "srv-1": srv})

        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        assert len(manifest.content_deps) == 1
        assert manifest.content_deps[0].resource_id == "kb-1"
        assert len(manifest.connection_deps) == 2
        conn_ids = {d.resource_id for d in manifest.connection_deps}
        assert conn_ids == {"conn-1", "srv-1"}

    @pytest.mark.asyncio
    async def test_skill_with_agent_ref(self) -> None:
        """Agent refs should appear as content_deps and recursively resolve the agent's own deps."""
        skill = SimpleNamespace(
            id="sk-1",
            name="Test Skill",
            resource_refs=[
                {"type": "agent", "id": "agt-1", "name": "My Agent"},
            ],
        )
        agent = SimpleNamespace(
            id="agt-1",
            name="My Agent",
            kb_ids=["kb-1"],
            connector_ids=["conn-1"],
            mcp_server_ids=None,
            skill_ids=None,
        )
        kb = SimpleNamespace(id="kb-1", name="Agent KB")
        conn = _fake_connector(id="conn-1", name="Agent Conn", auth_type="bearer")
        db = _mock_db_fetch({"sk-1": skill, "agt-1": agent, "kb-1": kb, "conn-1": conn})

        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        # Agent itself + Agent's KB = 2 content deps
        assert len(manifest.content_deps) == 2
        content_types = {(d.resource_type, d.resource_id) for d in manifest.content_deps}
        assert ("agent", "agt-1") in content_types
        assert ("knowledge_base", "kb-1") in content_types
        # Agent's Connector = 1 connection dep
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_id == "conn-1"

    @pytest.mark.asyncio
    async def test_skill_with_no_refs(self) -> None:
        skill = SimpleNamespace(id="sk-1", name="Skill", resource_refs=None)
        db = _mock_db_fetch({"sk-1": skill})
        manifest = await resolve_solution_dependencies("skill", "sk-1", db)
        assert len(manifest.connection_deps) == 0


class TestResolveWorkflowDependencies:
    @pytest.mark.asyncio
    async def test_workflow_with_connector_and_kb_nodes(self) -> None:
        workflow = SimpleNamespace(
            id="wf-1",
            name="Test WF",
            blueprint={
                "nodes": [
                    {"type": "CONNECTOR", "data": {"connector_id": "conn-1"}},
                    {"type": "KNOWLEDGE_RETRIEVAL", "data": {"kb_id": "kb-1"}},
                    {"type": "LLM", "data": {"prompt": "hello"}},
                ],
                "edges": [],
            },
        )
        conn = _fake_connector(id="conn-1", name="Conn", auth_type="basic")
        kb = SimpleNamespace(id="kb-1", name="KB")
        db = _mock_db_fetch({"wf-1": workflow, "conn-1": conn, "kb-1": kb})

        manifest = await resolve_solution_dependencies("workflow", "wf-1", db)
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_id == "conn-1"
        assert "username" in manifest.connection_deps[0].credential_schema
        assert len(manifest.content_deps) == 1
        assert manifest.content_deps[0].resource_id == "kb-1"

    @pytest.mark.asyncio
    async def test_workflow_with_mcp_node(self) -> None:
        workflow = SimpleNamespace(
            id="wf-1",
            name="WF",
            blueprint={
                "nodes": [
                    {"type": "MCP", "data": {"server_id": "srv-1"}},
                ],
                "edges": [],
            },
        )
        server = _fake_mcp_server(
            id="srv-1", name="MCP Server", env={"API_KEY": "sk-..."}
        )
        db = _mock_db_fetch({"wf-1": workflow, "srv-1": server})

        manifest = await resolve_solution_dependencies("workflow", "wf-1", db)
        assert len(manifest.connection_deps) == 1
        assert manifest.connection_deps[0].resource_type == "mcp_server"
        assert "API_KEY" in manifest.connection_deps[0].credential_schema

    @pytest.mark.asyncio
    async def test_workflow_not_found(self) -> None:
        db = _mock_db_fetch({})
        manifest = await resolve_solution_dependencies("workflow", "missing", db)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0

    @pytest.mark.asyncio
    async def test_workflow_empty_blueprint(self) -> None:
        workflow = SimpleNamespace(id="wf-1", name="WF", blueprint=None)
        db = _mock_db_fetch({"wf-1": workflow})
        manifest = await resolve_solution_dependencies("workflow", "wf-1", db)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0


class TestUnknownSolutionType:
    @pytest.mark.asyncio
    async def test_unknown_type(self) -> None:
        db = AsyncMock()
        manifest = await resolve_solution_dependencies("unknown_type", "id-1", db)
        assert len(manifest.content_deps) == 0
        assert len(manifest.connection_deps) == 0
