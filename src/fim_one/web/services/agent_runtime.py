"""The runtime of an agent: its models, tools and hooks, assembled in one order.

The stages below are what a chat turn runs, in this order:

1. :func:`resolve_agent`: the agent's configuration, or a refusal.
2. :func:`resolve_models`: the main and the fast model.
3. :func:`resolve_toolset`: built-in, knowledge-base and connector tools,
   with the MCP servers looked up but not yet connected.
4. :func:`connect_mcp`: connect them, in the task that will run the agent.
5. :func:`build_hooks`: the agent's enforcement hooks.

The chat endpoints call the stages one by one, because a turn resolves its
models before the response starts streaming and connects MCP inside the
stream. :class:`WebAgentRuntimeFactory` runs them back to back; it is the
implementation of ``core.assembly.runtime.AgentRuntimeFactory`` that
``call_agent`` delegates and workflow Agent steps get their runtime from.

The stage bodies still live in ``web/api/chat.py``; they are reached through
the module at call time.
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fim_one.core.assembly.errors import AUTHENTICATION_REQUIRED, AssemblyRefusal
from fim_one.core.assembly.principal import Binding, CallContext, Entry, Principal
from fim_one.core.assembly.runtime import AgentRuntime
from fim_one.core.assembly.sandbox import SANDBOXED_TOOLS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_one.core.agent.hooks import HookRegistry
    from fim_one.core.model.base import BaseLLM
    from fim_one.core.tool.registry import ToolRegistry

__all__ = [
    "BARE_RUNTIME_EXCLUDED",
    "SANDBOXED_TOOLS",
    "WebAgentRuntimeFactory",
    "bare_runtime",
    "bind_send_notification",
    "build_hooks",
    "call_context",
    "connect_mcp",
    "resolve_agent",
    "resolve_models",
    "resolve_toolset",
]


def _chat() -> Any:
    from fim_one.web.api import chat

    return chat


async def resolve_agent(
    ctx: CallContext,
    agent_id: str | None,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any] | None:
    """The configuration of the agent a run uses; ``None`` when it uses none.

    *agent_id* names the agent; without it the conversation's own agent is
    used. The agent is judged by ``ctx``'s subject. An agent that is not
    usable is a refusal: ``agent_unavailable`` (409) for the conversation's
    own agent, ``resource_not_found`` / ``resource_inactive`` for one named
    explicitly. A context with no acting user is refused
    (``authentication_required``): the lookup below reads an agent without
    any check when it is given no user to check for.
    """
    if not ctx.principal.user_id:
        raise AssemblyRefusal(
            AUTHENTICATION_REQUIRED, 401, "An agent cannot be used without an acting user"
        )
    config: dict[str, Any] | None = await _chat()._resolve_agent_config(
        agent_id,
        conversation_id,
        user_id=ctx.principal.user_id,
        refuse_unavailable=True,
        ctx=ctx,
    )
    return config


def call_context(
    principal: Principal,
    agent_cfg: dict[str, Any] | None,
    *,
    conversation_id: str | None = None,
    entry: Entry | None = None,
    base: CallContext | None = None,
) -> CallContext:
    """The context an agent's resources are used under.

    The agent is the binding (resources are judged by its owner's view); an
    agent config that names no owner, or no agent at all, leaves the
    principal using resources directly. *base* carries over the run a
    nested use belongs to (a workflow run and step).
    """
    agent_id = str(agent_cfg.get("agent_id") or agent_cfg.get("id") or "") if agent_cfg else ""
    owner_id = agent_cfg.get("owner_user_id") if agent_cfg else None
    binding = (
        Binding(kind="agent", id=agent_id, owner_id=owner_id, org_id=agent_cfg.get("org_id"))
        if agent_cfg and owner_id
        else None
    )
    resolved_entry: Entry = entry or ("chat" if agent_cfg else "chat_noagent")
    if base is not None:
        return dataclasses.replace(
            base,
            binding=binding,
            entry=entry or base.entry,
            conversation_id=conversation_id or base.conversation_id,
            agent_id=agent_id or None,
        )
    return CallContext(
        principal=principal,
        binding=binding,
        entry=resolved_entry,
        conversation_id=conversation_id,
        agent_id=agent_id or None,
    )


async def resolve_models(
    agent_cfg: dict[str, Any] | None, db: AsyncSession
) -> tuple[BaseLLM, BaseLLM]:
    """``(main model, fast model)`` for the run.

    Raises ``ValueError`` when no model is configured.
    """
    chat = _chat()
    llm, fast_llm = await asyncio.gather(
        chat._resolve_llm(agent_cfg, db),
        chat._resolve_fast_llm(agent_cfg, db),
    )
    return llm, fast_llm


async def resolve_toolset(
    ctx: CallContext,
    agent_cfg: dict[str, Any] | None,
    *,
    sandbox_id: str | None = None,
) -> ToolRegistry:
    """The run's tools. MCP servers are looked up; :func:`connect_mcp` connects them.

    The shell, code-execution and file tools work in the directory of
    *sandbox_id*, or of the context's conversation; with neither the
    toolset has none of them.
    """
    tools: ToolRegistry = await _chat()._resolve_tools(
        agent_cfg,
        ctx.conversation_id,
        user_id=ctx.principal.user_id,
        ctx=ctx,
        sandbox_id=sandbox_id,
    )
    return tools


async def connect_mcp(tools: ToolRegistry, agent_cfg: dict[str, Any] | None) -> Any | None:
    """Connect the MCP servers *tools* is waiting for and register their tools.

    Returns the client holding the connections (``None`` when there are no
    servers). Must run in the task that will later disconnect it.
    """
    return await _chat()._connect_pending_mcp_servers(tools, agent_cfg)


async def build_hooks(
    agent_cfg: dict[str, Any] | None, *, conversation_id: str | None = None
) -> HookRegistry:
    """The enforcement hooks of the run; the approval gate is always among them."""
    from fim_one.db import create_session
    from fim_one.web.hooks_bootstrap import build_hook_registry_for_agent

    return await build_hook_registry_for_agent(
        SimpleNamespace(model_config_json=(agent_cfg or {}).get("model_config_json")),
        create_session,
        conversation_id=conversation_id,
    )


# Built-in tools a step that names no agent does not get, besides the
# sandboxed ones: they write files for, or read the knowledge bases of, a
# user the step has no agent to act for, or they message people from a run
# that a trigger or a schedule can start with nobody watching. A step that
# should send mail or a notification selects an agent that has the tool.
BARE_RUNTIME_EXCLUDED = (
    *SANDBOXED_TOOLS,
    "generate_image",
    "kb_list",
    "kb_retrieve",
    "email_send",
    "send_notification",
)


def bind_send_notification(tools: ToolRegistry, user_id: str | None) -> ToolRegistry:
    """*tools* with ``send_notification`` acting for *user_id*.

    The tool sends to the channels of the organizations its user belongs
    to, so a run gets an instance bound to the run's acting user (the
    principal: the person chatting, or whoever a workflow run acts for),
    not the unbound one from tool discovery. Left alone when the operator
    has not enabled the tool or the run's tool selection dropped it.
    """
    if "send_notification" not in tools:
        return tools
    from fim_one.core.tool.builtin.send_notification import SendNotificationTool

    bound = tools.exclude_by_name("send_notification")
    bound.register(SendNotificationTool(user_id=user_id or None))
    return bound


async def _disabled_builtin_tools() -> list[str]:
    """The built-in tools an administrator switched off for the instance."""
    import json

    from fim_one.db import create_session
    from fim_one.web.api.admin import SETTING_DISABLED_BUILTIN_TOOLS
    from fim_one.web.api.admin_utils import get_setting

    async with create_session() as db:
        raw = await get_setting(db, SETTING_DISABLED_BUILTIN_TOOLS, default="[]")
    names = json.loads(raw)
    return [str(name) for name in names] if isinstance(names, list) else []


async def bare_runtime(ctx: CallContext) -> AgentRuntime:
    """The runtime of a step that names no agent: a model and plain tools.

    The fast model and the built-in tools that need neither a user's
    resources nor a sandbox: no connectors, MCP servers, knowledge bases or
    delegation (the step selected no agent to bind them to), and none of
    :data:`BARE_RUNTIME_EXCLUDED` (the sandboxed tools, image generation,
    the knowledge-base tools, and the mail and notification tools). Tools an administrator disabled are left
    out, as everywhere else.
    """
    from fim_one.db import create_session
    from fim_one.web.deps import get_effective_fast_llm, get_tools

    async with create_session() as db:
        llm = await get_effective_fast_llm(db)
    tools = get_tools().exclude_by_name(
        *BARE_RUNTIME_EXCLUDED, *await _disabled_builtin_tools()
    )
    # No agent, so no organization of an agent's: the notification tool
    # sends to the channels of the run's acting user.
    tools = bind_send_notification(tools, ctx.principal.user_id)
    return AgentRuntime(
        ctx=ctx,
        agent=None,
        llm=llm,
        fast_llm=llm,
        tools=tools,
        hooks=await build_hooks(None),
        instructions=None,
    )


class WebAgentRuntimeFactory:
    """``core.assembly.runtime.AgentRuntimeFactory``: the stages, back to back."""

    async def build(
        self,
        ctx: CallContext,
        agent_id: str | None,
        *,
        conversation_id: str | None = None,
        sandbox_id: str | None = None,
    ) -> AgentRuntime:
        from fim_one.db import create_session

        agent_cfg = await resolve_agent(ctx, agent_id, conversation_id=conversation_id)
        if agent_cfg is None and ctx.entry not in ("chat", "chat_noagent"):
            # Only a chat turn has a "no agent" mode with everything the user
            # can use; anywhere else no agent is no bound resources.
            return await bare_runtime(ctx)
        run_ctx = call_context(
            ctx.principal, agent_cfg, conversation_id=conversation_id, base=ctx
        )
        async with create_session() as db:
            llm, fast_llm = await resolve_models(agent_cfg, db)
        tools = await resolve_toolset(run_ctx, agent_cfg, sandbox_id=sandbox_id)
        mcp_client = await connect_mcp(tools, agent_cfg)
        try:
            hooks = await build_hooks(agent_cfg, conversation_id=conversation_id)
        except BaseException:
            if mcp_client is not None:
                await mcp_client.disconnect_all()
            raise
        return AgentRuntime(
            ctx=run_ctx,
            agent=agent_cfg,
            llm=llm,
            fast_llm=fast_llm,
            tools=tools,
            hooks=hooks,
            instructions=(agent_cfg or {}).get("instructions"),
            mcp_client=mcp_client,
        )
