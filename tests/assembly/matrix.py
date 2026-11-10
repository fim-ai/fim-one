"""The assembly contract matrix: one rule, every entry point.

Each entry point that lets a user reach a resource (chat with an agent,
chat without one, a ``call_agent`` sub-agent, three kinds of workflow node,
the ``run_workflow`` tool, schedules, trigger keys) is driven for real
against a seeded database, and what it does is normalised to an
:class:`Outcome`. :func:`expected` is the rule the assembly layer is being
built to enforce (``dev/C1-assembly-layer.md`` §2.4 to §2.7), computed the same
way for every entry point. Where an entry point does something else today,
the difference is registered in :data:`KNOWN_DEVIATIONS`.

``tests/assembly/test_matrix.py`` asserts *actual deviations == registered
deviations*: fixing one without removing its registration fails, and so
does introducing a new one. The table is what C1 phases 2 to 4 remove.

A **cell** is ``(role, resource, allow_fallback, is_active, has_personal)``:

- *role* of the acting user towards the resource: its ``owner``; a
  ``member`` of the owner's organization who has not subscribed; a
  ``subscriber``; an ``outsider`` from another organization.
- *resource*: nine kinds, see :data:`RESOURCES`.
- the resource's ``allow_fallback`` and ``is_active`` switches, and whether
  the acting user saved a personal credential for it.

A **scene** seeds one of every resource under one ``(role, allow_fallback,
is_active, has_personal)`` combination, plus an always-usable *carrier*
agent bound to those resources, so that one assembly per entry point serves
all nine cells of the scene. External HTTP, MCP and the model are faked.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.security.encryption import encrypt_credential, encrypt_db_config
from fim_one.db.models.agent import Agent
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.connector_credential import ConnectorCredential
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.mcp_server_credential import MCPServerCredential
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.skill import Skill
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun

# ---------------------------------------------------------------------------
# Axes
# ---------------------------------------------------------------------------

ROLES = ("owner", "member", "subscriber", "outsider")
RESOURCES = (
    "api_bearer",  # API connector, bearer token
    "api_none",  # API connector, no authentication
    "db",  # database connector
    "mcp_stdio",  # MCP server, stdio transport (reads env)
    "mcp_http",  # MCP server, streamable HTTP (reads headers)
    "kb",  # knowledge base
    "skill",
    "workflow",
    "agent",
)
ENTRIES = (
    "chat_agent",  # chat with an agent
    "chat_noagent",  # chat with no agent: everything visible is auto-discovered
    "sub_agent",  # call_agent delegate inside a no-agent chat
    "wf_connector_node",
    "wf_mcp_node",
    "wf_agent_node",
    "run_workflow",  # the run_workflow agent tool
    "schedule",
    "trigger",
)

API_CONNECTORS = ("api_bearer", "api_none")
MCP_SERVERS = ("mcp_stdio", "mcp_http")
CREDENTIALED = (*API_CONNECTORS, *MCP_SERVERS)


@dataclass(frozen=True, order=True)
class Cell:
    role: str
    resource: str
    allow_fallback: bool
    is_active: bool
    has_personal: bool

    def __str__(self) -> str:
        return (
            f"{self.role}/{self.resource}"
            f"/fallback={'on' if self.allow_fallback else 'off'}"
            f"/{'active' if self.is_active else 'inactive'}"
            f"/personal={'yes' if self.has_personal else 'no'}"
        )


@dataclass(frozen=True)
class Outcome:
    """What an entry point did with a resource, in comparable terms."""

    # The acting user got to use the resource through this entry point.
    exposed: bool
    # The refusal the entry point surfaced to its caller, if it surfaced one.
    refusal_code: str | None = None
    # Whose credential the call went out with: personal / owner /
    # owner_fallback / none; ``None`` where the resource has no credential.
    credential_source: str | None = None
    # The call left a call record.
    logged: bool = False
    # An action flagged "requires confirmation" was put before a human.
    confirmed: bool = False


FIELDS = ("exposed", "refusal_code", "credential_source", "logged", "confirmed")


@dataclass(frozen=True)
class SceneKey:
    role: str
    allow_fallback: bool
    is_active: bool
    has_personal: bool

    def cell(self, resource: str) -> Cell:
        return Cell(self.role, resource, self.allow_fallback, self.is_active, self.has_personal)


def all_scene_keys() -> list[SceneKey]:
    return [
        SceneKey(role, fallback, active, personal)
        for role, fallback, active, personal in itertools.product(
            ROLES, (True, False), (True, False), (True, False)
        )
    ]


# ---------------------------------------------------------------------------
# The rule (what every entry point is expected to do)
# ---------------------------------------------------------------------------

# Entry point and resource pairs that exist. Anything else is not applicable.
APPLICABLE: dict[str, tuple[str, ...]] = {
    "chat_agent": RESOURCES,
    "chat_noagent": RESOURCES,
    "sub_agent": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb", "agent"),
    "wf_connector_node": (*API_CONNECTORS, "db"),
    "wf_mcp_node": MCP_SERVERS,
    "wf_agent_node": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb", "agent"),
    "run_workflow": ("workflow",),
    "schedule": ("workflow",),
    "trigger": ("workflow",),
}

# Schedules and trigger keys have no acting user: the run is the author's.
AUTHOR_ENTRIES = ("schedule", "trigger")

# Resources an entry point reaches through a binding owned by the resource's
# owner (the carrier agent, or the workflow the node belongs to): the owner's
# view decides visibility. Everything else is judged by the acting user.
_BOUND: dict[str, tuple[str, ...]] = {
    "chat_agent": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb"),
    "sub_agent": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb"),
    "wf_connector_node": (*API_CONNECTORS, "db"),
    "wf_mcp_node": MCP_SERVERS,
    "wf_agent_node": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb", "agent"),
}

# Where a refused resource is simply not offered (a tool list, a catalogue),
# no refusal reaches the caller and none is expected. Elsewhere the entry
# point fails the operation and the code is part of the contract.
_DROPS_SILENTLY: dict[str, tuple[str, ...]] = {
    "chat_agent": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb", "skill", "workflow"),
    "chat_noagent": RESOURCES,
    "sub_agent": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb", "agent"),
    "wf_agent_node": (*API_CONNECTORS, "db", *MCP_SERVERS, "kb"),
    "run_workflow": ("workflow",),
}


# An entry point's own spelling of a refusal, where it has one. Trigger keys
# and schedules answer a switched-off workflow with ``workflow_inactive``
# (403): the code is part of the trigger API and of recorded schedule
# refusals, so it stays; it is the same refusal as ``resource_inactive``.
_SPELLED: dict[str, dict[str, str]] = {
    "schedule": {"resource_inactive": "workflow_inactive"},
    "trigger": {"resource_inactive": "workflow_inactive"},
}

# Not an entry point: the rule for a resource used directly, with every
# refusal code surfaced (what ``authorize_use`` and ``resolve_credential``
# are compared with).
DIRECT = "direct"


def applicable(entry: str, cell: Cell) -> bool:
    if cell.resource not in APPLICABLE[entry]:
        return False
    # With no acting user there is one principal, the author.
    return entry not in AUTHOR_ENTRIES or cell.role == "owner"


def expected(entry: str, cell: Cell) -> Outcome:
    """The rule, for *cell* reached through *entry*."""
    subject = "owner" if cell.resource in _BOUND.get(entry, ()) else cell.role
    return rule(entry, cell, subject=subject)


def rule(entry: str, cell: Cell, *, subject: str) -> Outcome:
    """The rule with the subject given (see :func:`expected` for who it is)."""
    resource = cell.resource
    principal = cell.role

    def refuse(code: str) -> Outcome:
        if resource in _DROPS_SILENTLY.get(entry, ()):
            return Outcome(exposed=False)
        return Outcome(exposed=False, refusal_code=_SPELLED.get(entry, {}).get(code, code))

    # 1. Visible to the subject. Knowledge bases cannot be shared.
    if not sees(subject, resource):
        return refuse("resource_not_found")
    # 2. Switched on.
    if not cell.is_active:
        return refuse("resource_inactive")
    # 3. Database connectors: the acting user must be the owner.
    if resource == "db":
        if principal != "owner":
            return refuse("owner_only")
        return Outcome(exposed=True, credential_source="owner", logged=True)
    # 4. One credential rule for API connectors and MCP servers.
    if resource in CREDENTIALED:
        source = credential_source(cell)
        if source is None:
            return refuse("credential_required")
        return Outcome(
            exposed=True,
            credential_source=source,
            logged=True,
            # Only connector actions carry a "requires confirmation" flag.
            confirmed=resource in API_CONNECTORS,
        )
    return Outcome(exposed=True)


def sees(role: str, resource: str) -> bool:
    """Whether a user in *role* can see the resource at all."""
    return role == "owner" or (role == "subscriber" and resource != "kb")


def credential_source(cell: Cell) -> str | None:
    """Whose credential the acting user's call goes out with; ``None``: refused."""
    if cell.resource == "api_none":
        return "none"  # needs no credential, so no fallback switch applies
    if cell.has_personal:
        return "personal"  # the user's own, never mixed with the owner's
    if cell.role == "owner":
        return "owner"
    if cell.allow_fallback:
        return "owner_fallback"
    return None


# ---------------------------------------------------------------------------
# Scene: the seeded world for one combination of switches
# ---------------------------------------------------------------------------

OWNER_TOKEN = "tok-owner"
PERSONAL_TOKEN = "tok-personal"
OWNER_ENV = {"KEY": "env-owner"}
PERSONAL_ENV = {"KEY": "env-personal"}
OWNER_HEADERS = {"Authorization": "Bearer hdr-owner"}
PERSONAL_HEADERS = {"Authorization": "Bearer hdr-personal"}
AGENT_MARKER = "INSTRUCTIONS-OF-THE-AGENT-UNDER-TEST"

_BLUEPRINT = {
    "nodes": [
        {"id": "start_1", "type": "start", "data": {"type": "START"}},
        {"id": "llm_1", "type": "llm", "data": {"type": "LLM", "prompt_template": "hi"}},
        {"id": "end_1", "type": "end", "data": {"type": "END"}},
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_1"},
        {"id": "e2", "source": "llm_1", "target": "end_1"},
    ],
}


@dataclass
class Scene:
    key: SceneKey
    users: dict[str, str]  # role -> user id
    ids: dict[str, str]  # resource -> id
    names: dict[str, str]  # resource -> name
    carrier_agent_id: str
    actions: dict[str, dict[str, str]]  # connector resource -> action name -> id

    @property
    def actor(self) -> str:
        return self.users[self.key.role]


async def seed_scene(factory: async_sessionmaker[AsyncSession], key: SceneKey) -> Scene:
    """Seed one of every resource, owned by the scene's owner."""
    tag = uuid.uuid4().hex[:8]
    users = {role: f"{role}-{tag}" for role in ROLES}
    ids = {resource: f"{resource}-{tag}" for resource in RESOURCES}
    names = {resource: f"{resource}_{tag}" for resource in RESOURCES}
    org, other_org = f"org-{tag}", f"other-{tag}"
    owner, actor = users["owner"], users[key.role]
    carrier = f"carrier-{tag}"
    shared: dict[str, Any] = {"visibility": "org", "org_id": org}
    actions: dict[str, dict[str, str]] = {}

    async with factory() as s:
        for uid in users.values():
            s.add(User(id=uid, email=f"{uid}@x.io", username=uid))
        s.add(Organization(id=org, name=org, slug=org, owner_id=owner))
        s.add(
            Organization(id=other_org, name=other_org, slug=other_org, owner_id=users["outsider"])
        )
        for role in ("owner", "member", "subscriber"):
            s.add(OrgMembership(org_id=org, user_id=users[role], role="member"))
        s.add(OrgMembership(org_id=other_org, user_id=users["outsider"], role="member"))

        # --- resources under test ---------------------------------------
        for resource, auth_type in (("api_bearer", "bearer"), ("api_none", "none")):
            s.add(
                Connector(
                    id=ids[resource],
                    user_id=owner,
                    name=names[resource],
                    type="api",
                    base_url=f"https://{resource.replace('_', '-')}.example.test",
                    auth_type=auth_type,
                    status="published",
                    is_active=key.is_active,
                    allow_fallback=key.allow_fallback,
                    **shared,
                )
            )
            actions[resource] = {}
            for name, method, flagged in (("ping", "GET", False), ("wipe", "POST", True)):
                action_id = f"{resource}-{name}-{tag}"
                actions[resource][name] = action_id
                s.add(
                    ConnectorAction(
                        id=action_id,
                        connector_id=ids[resource],
                        name=name,
                        method=method,
                        path=f"/{name}",
                        requires_confirmation=flagged,
                    )
                )
            if key.has_personal:
                s.add(
                    ConnectorCredential(
                        connector_id=ids[resource],
                        user_id=actor,
                        credentials_blob=encrypt_credential({"token": PERSONAL_TOKEN}),
                    )
                )
        s.add(
            ConnectorCredential(
                connector_id=ids["api_bearer"],
                user_id=None,
                credentials_blob=encrypt_credential({"default_token": OWNER_TOKEN}),
            )
        )
        s.add(
            Connector(
                id=ids["db"],
                user_id=owner,
                name=names["db"],
                type="database",
                status="published",
                is_active=key.is_active,
                allow_fallback=key.allow_fallback,
                db_config=encrypt_db_config(
                    {
                        "driver": "postgresql",
                        "host": "db.example.test",
                        "port": 5432,
                        "database": "d",
                        "username": "u",
                        "password": "p",
                    }
                ),
                **shared,
            )
        )
        s.add(
            MCPServer(
                id=ids["mcp_stdio"],
                user_id=owner,
                name=names["mcp_stdio"],
                transport="stdio",
                command="npx",
                env=dict(OWNER_ENV),
                is_active=key.is_active,
                allow_fallback=key.allow_fallback,
                **shared,
            )
        )
        s.add(
            MCPServer(
                id=ids["mcp_http"],
                user_id=owner,
                name=names["mcp_http"],
                transport="streamable_http",
                url="https://mcp.example.test/mcp",
                headers=dict(OWNER_HEADERS),
                is_active=key.is_active,
                allow_fallback=key.allow_fallback,
                **shared,
            )
        )
        if key.has_personal:
            s.add(
                MCPServerCredential(
                    server_id=ids["mcp_stdio"], user_id=actor, env_blob=dict(PERSONAL_ENV)
                )
            )
            s.add(
                MCPServerCredential(
                    server_id=ids["mcp_http"], user_id=actor, headers_blob=dict(PERSONAL_HEADERS)
                )
            )
        # A knowledge base cannot be shared: it stays personal.
        s.add(KnowledgeBase(id=ids["kb"], user_id=owner, name=names["kb"], is_active=key.is_active))
        s.add(
            Skill(
                id=ids["skill"],
                user_id=owner,
                name=names["skill"],
                content="do it",
                is_active=key.is_active,
                **shared,
            )
        )
        s.add(
            Workflow(
                id=ids["workflow"],
                user_id=owner,
                name=names["workflow"],
                blueprint=_BLUEPRINT,
                status="active",
                is_active=key.is_active,
                **shared,
            )
        )
        s.add(
            Agent(
                id=ids["agent"],
                user_id=owner,
                name=names["agent"],
                instructions=AGENT_MARKER,
                is_active=key.is_active,
                **shared,
            )
        )
        for resource in RESOURCES:
            if resource == "kb":
                continue
            s.add(
                ResourceSubscription(
                    user_id=users["subscriber"],
                    resource_type=_SUBSCRIPTION_TYPE[resource],
                    resource_id=ids[resource],
                    org_id=org,
                )
            )

        # --- carrier: an agent every role may enter, bound to the above ---
        s.add(
            Agent(
                id=carrier,
                user_id=owner,
                name=f"carrier_{tag}",
                instructions="carrier",
                is_active=True,
                connector_ids=[ids["api_bearer"], ids["api_none"], ids["db"]],
                mcp_server_ids=[ids["mcp_stdio"], ids["mcp_http"]],
                kb_ids=[ids["kb"]],
                grounding_config={"retrieval_mode": "simple"},
                **shared,
            )
        )
        for role in ("member", "subscriber", "outsider"):
            s.add(
                ResourceSubscription(
                    user_id=users[role], resource_type="agent", resource_id=carrier, org_id=org
                )
            )
        await s.commit()

    return Scene(
        key=key, users=users, ids=ids, names=names, carrier_agent_id=carrier, actions=actions
    )


_SUBSCRIPTION_TYPE = {
    "api_bearer": "connector",
    "api_none": "connector",
    "db": "connector",
    "mcp_stdio": "mcp_server",
    "mcp_http": "mcp_server",
    "skill": "skill",
    "workflow": "workflow",
    "agent": "agent",
}


# ---------------------------------------------------------------------------
# Fakes for the outside world
# ---------------------------------------------------------------------------


@dataclass
class Outside:
    """Everything that left the process during a probe."""

    http: list[dict[str, Any]] = field(default_factory=list)
    mcp_connects: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_calls: list[str] = field(default_factory=list)
    gate_asked: list[str] = field(default_factory=list)
    agents_built: list[dict[str, Any]] = field(default_factory=list)


class _FakeEngine:
    """Stands in for the workflow engine: the run completes at once."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def execute_streaming(
        self, _parsed: Any, _inputs: Any, context: Any = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        yield "run_completed", {"status": "completed", "outputs": {"ok": 1}, "total_tokens": 0}


@contextmanager
def outside_world(factory: async_sessionmaker[AsyncSession]) -> Iterator[Outside]:
    """Patch the database factory, HTTP, MCP, the model loop and the gates."""
    from fim_one.core.assembly.confirmation import ConfirmationService
    from fim_one.core.mcp.adapter import MCPToolAdapter
    from fim_one.core.workflow import run_service as rs

    out = Outside()

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    def _handler(request: httpx.Request) -> httpx.Response:
        out.http.append(
            {"url": str(request.url), "authorization": request.headers.get("authorization")}
        )
        return httpx.Response(200, json={"ok": True})

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), **kwargs)

    class _FakeMCPClient:
        async def _connect(self, name: str, **kwargs: Any) -> list[Any]:
            out.mcp_connects[name] = kwargs

            async def _call(tool_name: str, _arguments: dict[str, Any]) -> Any:
                out.mcp_calls.append(f"{name}.{tool_name}")
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="pong")], isError=False
                )

            return [
                MCPToolAdapter(
                    server_name=name,
                    tool_def={
                        "name": "echo",
                        "description": "echo",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    call_fn=_call,
                )
            ]

        async def connect_stdio(self, *, name: str, **kwargs: Any) -> list[Any]:
            return await self._connect(name, **kwargs)

        async def connect_sse(self, *, name: str, **kwargs: Any) -> list[Any]:
            return await self._connect(name, **kwargs)

        async def connect_streamable_http(self, *, name: str, **kwargs: Any) -> list[Any]:
            return await self._connect(name, **kwargs)

        async def disconnect_all(self) -> None:
            return None

    class _RecordingAgent:
        """Stands in for ReActAgent: records what the entry point assembled."""

        def __init__(self, **kwargs: Any) -> None:
            out.agents_built.append(kwargs)

        async def run(self, _query: str) -> Any:
            return SimpleNamespace(answer="done", iterations=1, usage=None)

    async def _asked(_self: Any, spec: Any) -> Any:
        out.gate_asked.append(str(spec.payload.get("tool_name")))
        return SimpleNamespace(id="asked", mode="inline")

    async def _approved(_self: Any, _confirmation_id: str, **_kwargs: Any) -> str:
        return "approve"

    with ExitStack() as stack:
        for target, value in (
            ("fim_one.db.create_session", _create_session),
            ("fim_one.db.engine.create_session", _create_session),
            ("fim_one.core.tool.connector.adapter.get_safe_async_client", _client),
            ("fim_one.core.tool.connector.adapter.validate_url", lambda *a, **k: None),
            ("fim_one.core.mcp.MCPClient", _FakeMCPClient),
            ("fim_one.core.security.is_stdio_allowed", lambda: True),
            ("fim_one.core.agent.ReActAgent", _RecordingAgent),
            ("fim_one.core.agent.react.ReActAgent", _RecordingAgent),
            ("fim_one.web.services.feature_flags.is_feature_enabled", AsyncMock(return_value=True)),
            ("fim_one.web.services.quota_backend.usage_and_cap", AsyncMock(return_value=(0, 0))),
            (
                "fim_one.web.services.quota_enforcer.is_user_unentitled",
                AsyncMock(return_value=False),
            ),
            ("fim_one.web.api.chat._resolve_llm", AsyncMock(return_value=MagicMock())),
            ("fim_one.web.deps.get_effective_fast_llm", AsyncMock(return_value=MagicMock())),
            ("fim_one.web.deps._cached_registry", None),
            ("fim_one.web.deps._cached_registry_version", -1),
        ):
            stack.enter_context(patch(target, value))
        stack.enter_context(patch.object(ConfirmationService, "request", _asked))
        stack.enter_context(patch.object(ConfirmationService, "wait", _approved))
        stack.enter_context(
            patch.object(rs, "_default_service", rs.WorkflowRunService(engine_factory=_FakeEngine))
        )
        yield out


# ---------------------------------------------------------------------------
# Observation: turn what an entry point assembled into Outcomes
# ---------------------------------------------------------------------------


@dataclass
class Assembled:
    """What an entry point hands its agent loop."""

    tools: Any  # ToolRegistry
    hooks: Any  # HookRegistry | None
    agent_id: str | None
    user_id: str | None


async def _log_count(
    factory: async_sessionmaker[AsyncSession], connector_id: str | None = None
) -> int:
    async with factory() as s:
        stmt = select(func.count()).select_from(ConnectorCallLog)
        if connector_id is not None:
            stmt = stmt.where(ConnectorCallLog.connector_id == connector_id)
        return int((await s.execute(stmt)).scalar_one())


def _token_source(scene: Scene, authorization: str | None) -> str:
    if authorization is None:
        return "none"
    if authorization == f"Bearer {PERSONAL_TOKEN}":
        return "personal"
    if authorization == f"Bearer {OWNER_TOKEN}":
        return "owner" if scene.key.role == "owner" else "owner_fallback"
    return f"unexpected:{authorization}"


def _mcp_source(scene: Scene, resource: str, connect_kwargs: dict[str, Any]) -> str:
    if resource == "mcp_stdio":
        used, own, owners = connect_kwargs.get("env"), PERSONAL_ENV, OWNER_ENV
    else:
        used, own, owners = connect_kwargs.get("headers"), PERSONAL_HEADERS, OWNER_HEADERS
    if used == own:
        return "personal"
    if used == owners:
        return "owner" if scene.key.role == "owner" else "owner_fallback"
    return f"unexpected:{used}"


async def _gate_asks(
    out: Outside, asm: Assembled, tool_name: str, tool_args: dict[str, Any]
) -> bool:
    """Whether the entry point's hooks put this tool call before a human.

    Builds the hook context the way ``ReActAgent._build_hook_metadata`` does
    (the agent loop itself is faked here) and runs the entry point's own
    registry; the gate's ask-and-wait step is replaced by a recorder.
    """
    from fim_one.core.agent.hooks import HookContext, HookPoint

    if asm.hooks is None:
        return False
    tool = asm.tools.get(tool_name)
    per_args = getattr(tool, "requires_confirmation_for", None)
    requires = (
        bool(per_args(tool_args))
        if callable(per_args)
        else bool(getattr(tool, "requires_confirmation", False))
    )
    before = len(out.gate_asked)
    await asm.hooks.run_pre_tool(
        HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name=tool_name,
            tool_args=tool_args,
            agent_id=asm.agent_id,
            user_id=asm.user_id,
            metadata={"requires_confirmation": requires},
        )
    )
    return len(out.gate_asked) > before


async def _observe_tools(
    scene: Scene,
    out: Outside,
    factory: async_sessionmaker[AsyncSession],
    asm: Assembled,
    resources: tuple[str, ...],
) -> dict[str, Outcome]:
    """Outcomes for connectors, the database connector and MCP servers."""
    results: dict[str, Outcome] = {}
    connector_tool = asm.tools.get("connector")
    database_tool = asm.tools.get("database")
    mcp_tool = asm.tools.get("mcp")

    for resource in resources:
        name = scene.names[resource]
        if resource in API_CONNECTORS:
            if connector_tool is None or name not in connector_tool.connector_names:
                results[resource] = Outcome(exposed=False)
                continue
            sent, logs = len(out.http), await _log_count(factory, scene.ids[resource])
            await connector_tool.run(
                subcommand="execute", connector=name, action="ping", parameters={}
            )
            if len(out.http) == sent:
                results[resource] = Outcome(exposed=False)
                continue
            results[resource] = Outcome(
                exposed=True,
                credential_source=_token_source(scene, out.http[-1]["authorization"]),
                logged=await _log_count(factory, scene.ids[resource]) > logs,
                confirmed=await _gate_asks(
                    out,
                    asm,
                    "connector",
                    {
                        "subcommand": "execute",
                        "connector": name,
                        "action": "wipe",
                        "parameters": {},
                    },
                ),
            )
        elif resource == "db":
            if database_tool is None or name not in database_tool.database_names:
                results[resource] = Outcome(exposed=False)
                continue
            # No database to query here: the record is observed by wiring.
            results[resource] = Outcome(
                exposed=True,
                credential_source="owner",
                logged=database_tool._on_call_complete is not None,
            )
        elif resource in MCP_SERVERS:
            if name not in out.mcp_connects or mcp_tool is None:
                results[resource] = Outcome(exposed=False)
                continue
            logs = await _log_count(factory)
            await mcp_tool.run(subcommand="call", server=name, tool="echo", parameters={})
            assert f"{name}.echo" in out.mcp_calls
            results[resource] = Outcome(
                exposed=True,
                credential_source=_mcp_source(scene, resource, out.mcp_connects[name]),
                logged=await _log_count(factory) > logs,
            )
    return results


def _observe_bound_kb(scene: Scene, asm: Assembled) -> Outcome:
    tool = asm.tools.get("kb_retrieve")
    kb = scene.ids["kb"]
    return Outcome(
        exposed=tool is not None
        and kb in getattr(tool, "_bound_kb_ids", ())
        and kb in getattr(tool, "_kb_owner_map", {})
    )


async def _kb_tool_reads(tools: Any, kb_id: str) -> bool:
    """Whether ``kb_retrieve`` with no bound KBs reads *kb_id* when asked to.

    The vector store is faked; the retrieval layer's own owner check is not.
    """
    from fim_one.rag.manager import KnowledgeBaseManager

    read: list[str] = []

    class _Manager:
        async def retrieve(self, _query: str, *, kb_id: str, user_id: str, **_: Any) -> list[Any]:
            if await KnowledgeBaseManager._resolve_owner_for_caller(kb_id, user_id) is not None:
                read.append(kb_id)
            return []

    tool = tools.get("kb_retrieve")
    if tool is None:
        return False
    with patch("fim_one.web.deps.get_kb_manager", lambda: _Manager()):
        await tool.run(query="anything", kb_id=kb_id)
    return kb_id in read


def _classify(error: str | None) -> str:
    """The refusal code a node error or tool error text amounts to."""
    text = error or ""
    if "not found or not accessible" in text:
        return "resource_not_found"
    if "requires your own" in text and "credentials" in text:
        return "credential_required"
    if "is disabled" in text:
        return "resource_inactive"
    if "can only be used by its owner" in text:
        return "owner_only"
    if "requires confirmation" in text:
        return "confirmation_unavailable"
    return "error"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

Probe = Callable[[Scene, async_sessionmaker[AsyncSession]], Awaitable[dict[str, Outcome]]]
_TOOL_RESOURCES = (*API_CONNECTORS, "db", *MCP_SERVERS)


async def _chat_hooks(agent_cfg: dict[str, Any] | None) -> Any:
    """The hook registry chat builds for a turn (``chat.py``, before its agent)."""
    from fim_one.db import create_session
    from fim_one.web.hooks_bootstrap import build_hook_registry_for_agent

    return await build_hook_registry_for_agent(
        SimpleNamespace(model_config_json=(agent_cfg or {}).get("model_config_json")),
        create_session,
    )


async def probe_chat_agent(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    """Chat with the carrier agent; the agent under test as the chat's agent."""
    from fim_one.web.api import chat

    with outside_world(factory) as out:
        cfg = await chat._resolve_agent_config(scene.carrier_agent_id, None, scene.actor)
        assert cfg is not None, "every role can enter the carrier agent"
        tools = await chat._resolve_tools(cfg, None, user_id=scene.actor)
        await chat._connect_pending_mcp_servers(tools, cfg)
        asm = Assembled(tools, await _chat_hooks(cfg), cfg.get("agent_id"), scene.actor)

        results = await _observe_tools(scene, out, factory, asm, _TOOL_RESOURCES)
        results["kb"] = _observe_bound_kb(scene, asm)
        results["skill"] = Outcome(
            exposed=scene.ids["skill"] in await chat._resolve_user_skill_ids(scene.actor)
        )
        results["workflow"] = Outcome(
            exposed=scene.ids["workflow"] in await chat._resolve_user_workflow_ids(scene.actor)
        )
        # Entering the agent under test, as the chat endpoints do.
        try:
            entered = await chat._resolve_agent_config(
                scene.ids["agent"], None, scene.actor, refuse_unavailable=True
            )
        except AssemblyRefusal as refusal:
            results["agent"] = Outcome(exposed=False, refusal_code=refusal.error_code)
        else:
            results["agent"] = Outcome(exposed=entered is not None)
    return results


async def probe_chat_noagent(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.web.api import chat

    with outside_world(factory) as out:
        tools = await chat._resolve_tools(None, None, user_id=scene.actor)
        await chat._connect_pending_mcp_servers(tools, None)
        asm = Assembled(tools, await _chat_hooks(None), None, scene.actor)

        results = await _observe_tools(scene, out, factory, asm, _TOOL_RESOURCES)
        results["kb"] = Outcome(exposed=await _kb_tool_reads(tools, scene.ids["kb"]))
        results["skill"] = Outcome(
            exposed=scene.ids["skill"] in await chat._resolve_user_skill_ids(scene.actor)
        )
        results["workflow"] = Outcome(
            exposed=scene.ids["workflow"] in await chat._resolve_user_workflow_ids(scene.actor)
        )
        call_agent = tools.get("call_agent")
        results["agent"] = Outcome(
            exposed=call_agent is not None
            and scene.ids["agent"] in getattr(call_agent, "_agents", {})
        )
    return results


def _last_agent(out: Outside) -> Assembled:
    built = out.agents_built[-1]
    return Assembled(
        built["tools"], built.get("hook_registry"), built.get("agent_id"), built.get("user_id")
    )


async def probe_sub_agent(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    """``call_agent`` from a no-agent chat, delegating to the carrier agent."""
    from fim_one.web.api import chat

    with outside_world(factory) as out:
        tools = await chat._resolve_tools(None, None, user_id=scene.actor)
        call_agent = tools.get("call_agent")
        assert call_agent is not None

        await call_agent.run(agent_id=scene.carrier_agent_id, task="go")
        asm = _last_agent(out)
        results = await _observe_tools(scene, out, factory, asm, _TOOL_RESOURCES)
        results["kb"] = _observe_bound_kb(scene, asm)

        built = len(out.agents_built)
        await call_agent.run(agent_id=scene.ids["agent"], task="go")
        results["agent"] = Outcome(
            exposed=len(out.agents_built) > built
            and out.agents_built[-1].get("extra_instructions") == AGENT_MARKER
        )
    return results


def _context(scene: Scene) -> Any:
    """The context the run service gives a run of the owner's workflow by the actor."""
    from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
    from fim_one.core.workflow.types import ExecutionContext

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    return ExecutionContext(
        run_id=run_id,
        user_id=scene.actor,
        workflow_id=scene.ids["workflow"],
        call_context=CallContext(
            principal=provisional_principal(scene.actor),
            binding=Binding(
                kind="workflow", id=scene.ids["workflow"], owner_id=scene.users["owner"]
            ),
            entry="workflow_node",
            workflow_run_id=run_id,
        ),
    )


def _node(node_type: Any, data: dict[str, Any]) -> Any:
    from fim_one.core.workflow.types import WorkflowNodeDef

    return WorkflowNodeDef(id="n1", type=node_type, data=data)


async def probe_wf_connector_node(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.core.workflow.nodes import ConnectorExecutor
    from fim_one.core.workflow.types import NodeStatus, NodeType
    from fim_one.core.workflow.variable_store import VariableStore

    async def _run(resource: str, action_id: str) -> Any:
        node = _node(
            NodeType.CONNECTOR,
            {"connector_id": scene.ids[resource], "action_id": action_id, "parameters": {}},
        )
        return await ConnectorExecutor().execute(node, VariableStore(), _context(scene))

    results: dict[str, Outcome] = {}
    with outside_world(factory) as out:
        for resource in API_CONNECTORS:
            sent, logs = len(out.http), await _log_count(factory, scene.ids[resource])
            result = await _run(resource, scene.actions[resource]["ping"])
            if result.status != NodeStatus.COMPLETED:
                results[resource] = Outcome(exposed=False, refusal_code=_classify(result.error))
                continue
            assert len(out.http) == sent + 1
            # The flagged action: a node that waits for a human asks the
            # gate; one that cannot wait fails.
            asked = len(out.gate_asked)
            flagged = await _run(resource, scene.actions[resource]["wipe"])
            results[resource] = Outcome(
                exposed=True,
                credential_source=_token_source(scene, out.http[sent]["authorization"]),
                logged=await _log_count(factory, scene.ids[resource]) > logs,
                confirmed=len(out.gate_asked) > asked and flagged.status == NodeStatus.COMPLETED,
            )
        # A database connector has no actions; the node is given none.
        result = await _run("db", "no-such-action")
        results["db"] = (
            Outcome(exposed=True, credential_source="owner", logged=True)
            if result.status == NodeStatus.COMPLETED
            else Outcome(exposed=False, refusal_code=_classify(result.error))
        )
    return results


async def probe_wf_mcp_node(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.core.workflow.nodes import MCPExecutor
    from fim_one.core.workflow.types import NodeStatus, NodeType
    from fim_one.core.workflow.variable_store import VariableStore

    results: dict[str, Outcome] = {}
    with outside_world(factory) as out:
        for resource in MCP_SERVERS:
            logs = await _log_count(factory)
            node = _node(
                NodeType.MCP,
                {"server_id": scene.ids[resource], "tool_name": "echo", "parameters": {}},
            )
            result = await MCPExecutor().execute(node, VariableStore(), _context(scene))
            if result.status != NodeStatus.COMPLETED:
                results[resource] = Outcome(exposed=False, refusal_code=_classify(result.error))
                continue
            results[resource] = Outcome(
                exposed=True,
                credential_source=_mcp_source(
                    scene, resource, out.mcp_connects[scene.names[resource]]
                ),
                logged=await _log_count(factory) > logs,
            )
    return results


async def probe_wf_agent_node(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.core.workflow.nodes import AgentExecutor
    from fim_one.core.workflow.types import NodeStatus, NodeType
    from fim_one.core.workflow.variable_store import VariableStore

    async def _run(agent_id: str) -> Any:
        node = _node(NodeType.AGENT, {"agent_id": agent_id, "prompt_template": "go"})
        return await AgentExecutor().execute(node, VariableStore(), _context(scene))

    with outside_world(factory) as out:
        result = await _run(scene.carrier_agent_id)
        assert result.status == NodeStatus.COMPLETED, result.error
        asm = _last_agent(out)
        results = await _observe_tools(scene, out, factory, asm, _TOOL_RESOURCES)
        results["kb"] = _observe_bound_kb(scene, asm)

        result = await _run(scene.ids["agent"])
        if result.status != NodeStatus.COMPLETED:
            results["agent"] = Outcome(exposed=False, refusal_code=_classify(result.error))
        else:
            # The node ran. It ran *the agent* only if the agent's
            # instructions reached the loop; otherwise it ran a bare model.
            results["agent"] = Outcome(
                exposed=out.agents_built[-1].get("extra_instructions") == AGENT_MARKER
            )
    return results


async def _workflow(scene: Scene, factory: async_sessionmaker[AsyncSession]) -> Workflow:
    async with factory() as s:
        wf = await s.get(Workflow, scene.ids["workflow"])
        assert wf is not None
        return wf


async def probe_run_workflow(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    """The ``run_workflow`` tool as chat builds it: listed workflows, then run."""
    from fim_one.core.tool.builtin.run_workflow import RunWorkflowTool
    from fim_one.web.api import chat

    with outside_world(factory):
        listed = await chat._resolve_user_workflow_ids(scene.actor)
        text = await RunWorkflowTool(workflow_ids=listed, user_id=scene.actor).run(
            name=scene.names["workflow"]
        )
    if not text.startswith("[Error]"):
        return {"workflow": Outcome(exposed=True)}
    if "not found" in text:
        # Not in the tool's catalogue: nothing surfaced beyond "no such workflow".
        return {"workflow": Outcome(exposed=False)}
    return {"workflow": Outcome(exposed=False, refusal_code=text.rsplit("(", 1)[-1].rstrip(")"))}


async def probe_schedule(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.core.workflow.scheduler import WorkflowScheduler

    wf = await _workflow(scene, factory)
    with outside_world(factory):
        await WorkflowScheduler(poll_interval=60)._execute_workflow(wf)
    async with factory() as s:
        run = (
            await s.execute(
                select(WorkflowRun).where(
                    WorkflowRun.workflow_id == wf.id, WorkflowRun.mode == "schedule"
                )
            )
        ).scalar_one()
    if run.status == "completed":
        return {"workflow": Outcome(exposed=True)}
    return {"workflow": Outcome(exposed=False, refusal_code=run.error)}


async def probe_trigger(
    scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[str, Outcome]:
    from fim_one.web.api import workflows as wf_api
    from fim_one.web.exceptions import AppError
    from fim_one.web.schemas.workflow import WorkflowTriggerRequest

    wf = await _workflow(scene, factory)
    with outside_world(factory):
        try:
            await wf_api._execute_trigger(wf, WorkflowTriggerRequest(inputs={}))
        except AppError as exc:
            return {"workflow": Outcome(exposed=False, refusal_code=exc.error_code)}
    return {"workflow": Outcome(exposed=True)}


PROBES: dict[str, Probe] = {
    "chat_agent": probe_chat_agent,
    "chat_noagent": probe_chat_noagent,
    "sub_agent": probe_sub_agent,
    "wf_connector_node": probe_wf_connector_node,
    "wf_mcp_node": probe_wf_mcp_node,
    "wf_agent_node": probe_wf_agent_node,
    "run_workflow": probe_run_workflow,
    "schedule": probe_schedule,
    "trigger": probe_trigger,
}


async def probe(
    entry: str, scene: Scene, factory: async_sessionmaker[AsyncSession]
) -> dict[Cell, Outcome]:
    """Drive *entry* in *scene*; outcomes for the cells that apply to it."""
    by_resource = await PROBES[entry](scene, factory)
    return {
        scene.key.cell(resource): outcome
        for resource, outcome in by_resource.items()
        if applicable(entry, scene.key.cell(resource))
    }


# ---------------------------------------------------------------------------
# Known deviations: where an entry point does not follow the rule today
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Deviation:
    """One way an entry point differs from the rule today.

    ``today`` takes a cell and the outcome so far (the rule's, then earlier
    deviations applied in list order) and returns what the entry point
    actually does, or ``None`` where this deviation changes nothing.
    """

    name: str
    entries: tuple[str, ...]
    resources: tuple[str, ...]
    what: str  # what the entry point does instead of the rule
    ref: str  # where the design records it
    removed_in: str  # the phase that removes it
    today: Callable[[str, Cell, Outcome], Outcome | None]


def _replace(outcome: Outcome, **changes: Any) -> Outcome:
    return Outcome(**{**{f: getattr(outcome, f) for f in FIELDS}, **changes})


def _runner_is_subject(entry: str, cell: Cell, _so_far: Outcome) -> Outcome | None:
    return rule(entry, cell, subject=cell.role)


def _not_confirmed(_entry: str, _cell: Cell, so_far: Outcome) -> Outcome | None:
    return _replace(so_far, confirmed=False) if so_far.exposed else None


def _database_unsupported(_entry: str, _cell: Cell, so_far: Outcome) -> Outcome | None:
    if so_far.exposed or so_far.refusal_code == "owner_only":
        return Outcome(exposed=False, refusal_code="error")
    return None


# Applied in this order: a later entry sees the outcome the earlier ones left.
KNOWN_DEVIATIONS: tuple[Deviation, ...] = (
    Deviation(
        name="workflow-node-judged-by-runner",
        entries=("wf_connector_node", "wf_mcp_node"),
        resources=(*API_CONNECTORS, "db", *MCP_SERVERS),
        what=(
            "Visibility is judged by whoever runs the workflow, not by its author: a "
            "member or outsider running a shared workflow gets 'not found' for a "
            "resource the author can see (and 'not found' instead of the inactive, "
            "credential or owner-only refusal)."
        ),
        ref="S10; design §1.1 rows 'wf-节点'; B15",
        removed_in="C1 phase 4",
        today=_runner_is_subject,
    ),
    Deviation(
        name="connector-node-cannot-confirm",
        entries=("wf_connector_node",),
        resources=API_CONNECTORS,
        what="An action flagged 'requires confirmation' fails the node instead of waiting.",
        ref="design §1.3 row 'wf-节点 (连接器)'",
        removed_in="T10 P5 (not C1)",
        today=_not_confirmed,
    ),
    Deviation(
        name="connector-node-database-unsupported",
        entries=("wf_connector_node",),
        resources=("db",),
        what=(
            "A database connector is treated as an HTTP connector and the node fails "
            "with an unrelated error, for the owner and for a subscriber alike."
        ),
        ref="design §1.2 row 'DB 连接器 wf-节点'",
        removed_in="T10 P4 (support, or refuse at save)",
        today=_database_unsupported,
    ),
)


def as_built(entry: str, cell: Cell) -> Outcome:
    """What *entry* does with *cell* today: the rule plus the known deviations."""
    outcome = expected(entry, cell)
    for deviation in KNOWN_DEVIATIONS:
        if entry in deviation.entries and cell.resource in deviation.resources:
            changed = deviation.today(entry, cell, outcome)
            if changed is not None:
                outcome = changed
    return outcome


def all_cells(entry: str) -> list[Cell]:
    return [
        cell
        for key in all_scene_keys()
        for resource in RESOURCES
        if applicable(entry, cell := key.cell(resource))
    ]


def registered_deviations() -> dict[tuple[str, Cell], Outcome]:
    """Every (entry, cell) where today's registered behaviour is not the rule."""
    registered: dict[tuple[str, Cell], Outcome] = {}
    for entry in ENTRIES:
        for cell in all_cells(entry):
            today = as_built(entry, cell)
            if today != expected(entry, cell):
                registered[(entry, cell)] = today
    return registered


def describe(expected_outcome: Outcome, actual: Outcome) -> str:
    return "; ".join(
        f"{f}: rule {getattr(expected_outcome, f)!r}, does {getattr(actual, f)!r}"
        for f in FIELDS
        if getattr(expected_outcome, f) != getattr(actual, f)
    )
