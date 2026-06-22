"""Node executors — one class per workflow node type.

Each executor implements the ``NodeExecutor`` protocol: given a node definition,
a variable store, and an execution context, it performs the node's action and
returns a ``NodeResult``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .types import ExecutionContext, NodeResult, NodeStatus, NodeType, WorkflowNodeDef
from .variable_store import VariableStore

if TYPE_CHECKING:
    from fim_one.core.tool.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NodeExecutor(Protocol):
    """Protocol that all node executors must implement."""

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        """Execute the node and return a result."""
        ...

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        """Declare output variables this node type produces.

        Returns a list of ``{name, type, description}`` dicts.  Used by the
        frontend variable picker to show available variables from upstream
        nodes.  The default implementation returns an empty list.
        """
        return []


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ms_since(start: float) -> int:
    """Milliseconds elapsed since *start* (``time.time()`` epoch)."""
    return int((time.time() - start) * 1000)


def _flatten_eval_names(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a flattened namespace for ``simpleeval`` from a dotted-key snapshot.

    Given ``{"input.score": 80, "start_1.name": "A"}``, produces::

        {"input_score": 80, "score": 80, "input.score": 80,
         "start_1_name": "A", "name": "A", "start_1.name": "A"}

    Short names (last segment) are added only if not already present to avoid
    overwriting more specific keys.
    """
    names: dict[str, Any] = {}
    for key, val in snapshot.items():
        # Underscore-joined alias: "input.score" → "input_score"
        names[key.replace(".", "_")] = val
        # Short alias: "input.score" → "score"
        short = key.rsplit(".", 1)[-1] if "." in key else key
        if short not in names:
            names[short] = val
        # Full dotted key (for explicit references)
        names[key] = val
    return names


# ---------------------------------------------------------------------------
# 1. Start node
# ---------------------------------------------------------------------------


class StartExecutor:
    """Copy inputs into the variable store."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        # Start node outputs are dynamic (defined by the input_schema).
        # Return a generic entry; the API layer reads the actual schema
        # from node.data["input_schema"] for precise variable listing.
        return [
            {"name": "output", "type": "object", "description": "All workflow inputs as a dict"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            # The START node's input_schema defines expected keys.
            # Inputs are already in the store under "input.*" — copy them
            # also under the node's output namespace.
            snapshot = await store.snapshot()
            input_vars = {
                k.removeprefix("input."): v
                for k, v in snapshot.items()
                if k.startswith("input.")
            }
            # Set outputs for downstream reference
            for key, value in input_vars.items():
                await store.set(f"{node.id}.{key}", value)

            # Also expose all inputs as the Start node's combined output
            await store.set(f"{node.id}.output", input_vars)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=input_vars,
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Start node error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 2. End node
# ---------------------------------------------------------------------------


class EndExecutor:
    """Read output_mapping from store and produce the final result."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        # End node is a terminal — no downstream consumers.
        return []

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            output_mapping = node.data.get("output_mapping", {})
            outputs: dict[str, Any] = {}

            if output_mapping:
                for key, var_ref in output_mapping.items():
                    if isinstance(var_ref, str) and "{{" in var_ref:
                        outputs[key] = await store.interpolate(var_ref)
                    elif isinstance(var_ref, str):
                        outputs[key] = await store.get(var_ref)
                    else:
                        outputs[key] = var_ref
            else:
                # Default: collect all node outputs
                snapshot = await store.snapshot()
                outputs = {
                    k: v
                    for k, v in snapshot.items()
                    if not k.startswith("env.") and not k.startswith("input.")
                }

            await store.set(f"{node.id}.output", outputs)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=outputs,
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"End node error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 3. LLM node
# ---------------------------------------------------------------------------


class LLMExecutor:
    """Interpolate prompt template, call LLM, store output."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "LLM response text"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from fim_one.core.model.types import ChatMessage
            from fim_one.web.deps import get_effective_llm
            from fim_one.db import create_session

            # Get config from node data (accept both frontend and legacy keys)
            prompt_template = node.data.get("prompt_template", "") or node.data.get("prompt", "")
            system_prompt = node.data.get("system_prompt", "")

            # Interpolate variables in prompts
            prompt = await store.interpolate(prompt_template)
            if system_prompt:
                system_prompt = await store.interpolate(system_prompt)

            # Resolve the LLM the same way the playground/chat does: the
            # system-configured model (provider config, else .env), with its own
            # internal fast/degradation handling. The node deliberately exposes
            # no model knob — what runs is the processed system model selection.
            async with create_session() as db:
                llm = await get_effective_llm(db)

            # Build messages
            messages: list[ChatMessage] = []
            if system_prompt:
                messages.append(ChatMessage(role="system", content=system_prompt))
            messages.append(ChatMessage(role="user", content=prompt))

            result = await llm.chat(messages)
            output = result.message.content or ""

            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500],  # preview
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("LLM node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"LLM error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 4. ConditionBranch node
# ---------------------------------------------------------------------------


class ConditionBranchExecutor:
    """Evaluate conditions and return which branch handles to activate.

    The node's data contains ``conditions``: a list of dicts, each with
    ``handle`` (the sourceHandle to activate) and ``expression`` (a safe
    expression to evaluate).  The first truthy condition wins.  A ``default``
    handle is activated if no conditions match.
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "The label of the active branch handle"},
            {"name": "active_handle", "type": "string", "description": "The source handle ID that was activated"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from simpleeval import simple_eval  # type: ignore[import-untyped]

            conditions = node.data.get("conditions", [])
            default_handle = node.data.get("default_handle", "source-default")

            snapshot = await store.snapshot_safe()
            eval_names = _flatten_eval_names(snapshot)
            mode = node.data.get("mode", "expression")

            active_handle: str | None = None
            for cond in conditions:
                # Frontend sends {id, label, variable, operator, value, expression, llm_prompt}
                # The sourceHandle format is "condition-{id}"
                cond_id = cond.get("id", cond.get("handle", ""))
                handle = f"condition-{cond_id}" if cond_id and not cond_id.startswith("condition-") else cond_id

                if mode == "expression":
                    # Build expression from variable/operator/value fields
                    variable = cond.get("variable", "")
                    operator = cond.get("operator", "==")
                    value = cond.get("value", "")
                    expr = cond.get("expression", "")

                    # M8: If mode is "expression" but this condition has no
                    # expression and no variable (i.e. it was configured with
                    # an llm_prompt instead), fall through to LLM evaluation
                    # for this condition rather than silently skipping it.
                    if not expr and not variable and cond.get("llm_prompt"):
                        logger.warning(
                            "ConditionBranch node %s condition '%s' has no "
                            "expression but has llm_prompt; falling through "
                            "to LLM evaluation for this condition.",
                            node.id, cond_id,
                        )
                        # Delegate just this condition to LLM evaluation
                        llm_handle = await self._evaluate_llm(
                            node, [cond], store, default_handle,
                        )
                        if llm_handle is not None:
                            active_handle = llm_handle
                            break
                        continue

                    if not expr and variable:
                        # Auto-build expression from structured fields
                        if operator in ("is_empty",):
                            expr = f"not {variable}"
                        elif operator in ("is_not_empty",):
                            expr = f"bool({variable})"
                        elif operator == "contains":
                            expr = f"{json.dumps(value)} in str({variable})"
                        elif operator == "not_contains":
                            expr = f"{json.dumps(value)} not in str({variable})"
                        else:
                            # Try to parse value as number, else quote it
                            try:
                                float(value)
                                expr = f"{variable} {operator} {value}"
                            except (ValueError, TypeError):
                                expr = f"{variable} {operator} {json.dumps(value)}"

                    if not expr or not handle:
                        continue
                    try:
                        result = simple_eval(expr, names=eval_names)
                        if result:
                            active_handle = handle
                            break
                    except Exception as e:
                        logger.warning(
                            "Condition expression '%s' failed: %s", expr, e
                        )
                        continue
                else:
                    # LLM mode — defer to batch LLM evaluation below
                    pass

            if mode != "expression" and active_handle is None:
                active_handle = await self._evaluate_llm(
                    node, conditions, store, default_handle,
                )

            if active_handle is None:
                active_handle = default_handle

            await store.set(f"{node.id}.output", active_handle)
            await store.set(f"{node.id}.active_handle", active_handle)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=active_handle,
                active_handles=[active_handle],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("ConditionBranch node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Condition error: {exc}",
                duration_ms=_ms_since(t0),
            )

    async def _evaluate_llm(
        self,
        node: WorkflowNodeDef,
        conditions: list[dict[str, Any]],
        store: VariableStore,
        default_handle: str,
    ) -> str | None:
        """Use the fast LLM to decide which condition branch to activate.

        Builds a classification prompt from each condition's ``llm_prompt``
        and ``label``, asks the LLM to pick exactly one, then maps the
        response back to the corresponding ``condition-{id}`` handle.

        Returns the matched handle, or *None* so the caller falls through
        to the default.
        """
        from fim_one.core.model.types import ChatMessage
        from fim_one.db import create_session
        from fim_one.web.deps import get_effective_fast_llm

        # Build the list of candidate branches with interpolated prompts
        candidates: list[dict[str, str]] = []
        for cond in conditions:
            label = cond.get("label", "")
            llm_prompt = cond.get("llm_prompt", "")
            if not label or not llm_prompt:
                continue
            cond_id = cond.get("id", cond.get("handle", ""))
            handle = (
                f"condition-{cond_id}"
                if cond_id and not cond_id.startswith("condition-")
                else cond_id
            )
            if not handle:
                continue
            # Interpolate variables referenced in the llm_prompt
            interpolated_prompt = await store.interpolate(llm_prompt)
            candidates.append({
                "label": label,
                "description": interpolated_prompt,
                "handle": handle,
            })

        if not candidates:
            logger.warning(
                "ConditionBranch node %s in LLM mode has no valid candidates",
                node.id,
            )
            return None

        # Build the numbered list for the system prompt
        numbered = "\n".join(
            f"{i + 1}. {c['label']}: {c['description']}"
            for i, c in enumerate(candidates)
        )

        # Optional user-level context from the node's llm_prompt field
        node_llm_prompt = node.data.get("llm_prompt", "")
        context_section = ""
        if node_llm_prompt:
            interpolated_context = await store.interpolate(node_llm_prompt)
            context_section = f"\nAdditional context:\n{interpolated_context}\n"

        system_prompt = (
            "You are a condition evaluator. Based on the descriptions below, "
            "determine which ONE condition is satisfied. Respond with ONLY the "
            "condition label — nothing else. If none clearly match, respond "
            'with "DEFAULT".\n\n'
            f"Conditions:\n{numbered}"
            f"{context_section}"
        )

        # Provide the current variable snapshot as user context so the LLM
        # can reason about runtime values.
        snapshot = await store.snapshot_safe()
        # Trim large values to keep token usage reasonable
        trimmed: dict[str, Any] = {}
        for k, v in snapshot.items():
            sv = str(v)
            trimmed[k] = sv[:500] if len(sv) > 500 else v
        user_content = (
            "Current variables:\n"
            f"```json\n{json.dumps(trimmed, ensure_ascii=False, default=str)}\n```\n\n"
            "Which condition is satisfied?"
        )

        async with create_session() as db:
            llm = await get_effective_fast_llm(db)

        result = await llm.chat([
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ])

        _raw_content = result.message.content
        answer = (_raw_content if isinstance(_raw_content, str) else "").strip()
        logger.debug(
            "ConditionBranch LLM response for node %s: %r", node.id, answer,
        )

        # Match the LLM answer to a candidate label (case-insensitive)
        answer_lower = answer.lower()
        for c in candidates:
            if c["label"].lower() == answer_lower:
                return c["handle"]

        # Fuzzy fallback: check if the answer contains exactly one label
        matched_handles: list[str] = []
        for c in candidates:
            if c["label"].lower() in answer_lower:
                matched_handles.append(c["handle"])
        if len(matched_handles) == 1:
            return matched_handles[0]

        # No match — fall through to default
        if answer_lower != "default":
            logger.warning(
                "ConditionBranch LLM node %s returned unrecognized answer %r; "
                "falling back to default handle",
                node.id,
                answer,
            )
        return None
class AgentExecutor:
    """Load an agent configuration and run it via ReActAgent."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "Agent final answer text"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from fim_one.core.agent import ReActAgent
            from fim_one.core.tool import ToolRegistry
            from fim_one.db import create_session
            from fim_one.web.deps import get_effective_fast_llm, get_tools

            agent_id = node.data.get("agent_id", "")
            query_template = node.data.get("prompt_template", "") or node.data.get("query", "")

            query = await store.interpolate(query_template) if query_template else ""

            if not query:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Agent node has no query",
                    duration_ms=_ms_since(t0),
                )

            # Resolve LLM and tools
            async with create_session() as db:
                llm = await get_effective_fast_llm(db)

                # If agent_id specified, load agent config
                instructions: str | None = None
                if agent_id:
                    from fim_one.db.models import Agent
                    from sqlalchemy import select

                    result = await db.execute(
                        select(Agent).where(Agent.id == agent_id)
                    )
                    agent_model = result.scalar_one_or_none()
                    if agent_model:
                        instructions = agent_model.instructions

            tools = get_tools()
            agent = ReActAgent(
                llm=llm,
                tools=tools,
                extra_instructions=instructions,
                max_iterations=10,
            )

            agent_result = await agent.run(query)
            output = agent_result.answer

            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("Agent node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Agent error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 7. KnowledgeRetrieval node
# ---------------------------------------------------------------------------


class KnowledgeRetrievalExecutor:
    """Query the RAG pipeline for relevant knowledge."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "Combined text from retrieved knowledge chunks"},
            {"name": "results", "type": "array", "description": "Raw retrieval results with content and score"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from fim_one.web.deps import get_kb_manager

            # Frontend sends kb_id (singular string), legacy uses kb_ids (list)
            kb_ids = node.data.get("kb_ids", [])
            if not kb_ids:
                single_id = node.data.get("kb_id", "")
                if single_id:
                    kb_ids = [single_id]
            query_template = node.data.get("query_template", "") or node.data.get("query", "")
            top_k = node.data.get("top_k", 5)

            query = await store.interpolate(query_template) if query_template else ""

            if not query:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="KnowledgeRetrieval node has no query",
                    duration_ms=_ms_since(t0),
                )

            kb_manager = get_kb_manager()
            all_results: list[dict[str, Any]] = []

            for kb_id in kb_ids:
                try:
                    results = await kb_manager.retrieve(
                        query, kb_id=kb_id, user_id=context.user_id, top_k=top_k
                    )
                    for r in results:
                        all_results.append({
                            "kb_id": kb_id,
                            "content": r.content if hasattr(r, "content") else str(r),
                            "score": r.score if hasattr(r, "score") else 0,
                        })
                except Exception as e:
                    logger.warning("KB search failed for %s: %s", kb_id, e)

            # Sort by score descending, limit to top_k
            all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            all_results = all_results[:top_k]

            # Combine into text
            combined = "\n\n".join(
                r.get("content", "") for r in all_results
            )
            await store.set(f"{node.id}.output", combined)
            await store.set(f"{node.id}.results", all_results)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=f"Retrieved {len(all_results)} results",
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("KnowledgeRetrieval node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"KB retrieval error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 8. Connector node
# ---------------------------------------------------------------------------


class ConnectorExecutor:
    """Load a connector + action and execute it."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "object", "description": "Connector action response"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from fim_one.core.tool.connector.adapter import ConnectorToolAdapter
            from fim_one.db import create_session

            connector_id = node.data.get("connector_id", "")
            action_id = node.data.get("action_id", "") or node.data.get("action", "")
            params_template = node.data.get("parameters", {})

            if not connector_id or not action_id:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Connector node requires connector_id and action_id",
                    duration_ms=_ms_since(t0),
                )

            # Interpolate parameters
            params: dict[str, Any] = {}
            for key, val in params_template.items():
                if isinstance(val, str) and "{{" in val:
                    params[key] = await store.interpolate(val)
                else:
                    params[key] = val

            # Load connector and action from DB
            async with create_session() as db:
                from fim_one.db.models.connector import Connector, ConnectorAction
                from sqlalchemy import select

                # Enforce visibility against the run's identity: a shared
                # workflow is a graph of references, and each referenced
                # connector enforces its own per-user access at run time. Only
                # connectors the runner owns or has subscribed to are usable —
                # not any connector that merely exists by id (tightened from
                # existence-only to match chat assembly + agent binding).
                conn_stmt = select(Connector).where(Connector.id == connector_id)
                if context.user_id:
                    from fim_one.web.visibility import resolve_visibility

                    _vis_clause, _, _ = await resolve_visibility(
                        Connector, context.user_id, "connector", db
                    )
                    conn_stmt = conn_stmt.where(_vis_clause)
                conn_result = await db.execute(conn_stmt)
                connector = conn_result.scalar_one_or_none()
                if not connector:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=(
                            f"Connector '{connector_id}' not found or not "
                            "accessible to you"
                        ),
                        duration_ms=_ms_since(t0),
                    )

                action_result = await db.execute(
                    select(ConnectorAction).where(
                        ConnectorAction.id == action_id,
                        ConnectorAction.connector_id == connector_id,
                    )
                )
                action = action_result.scalar_one_or_none()
                if not action:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=f"Action '{action_id}' not found",
                        duration_ms=_ms_since(t0),
                    )

                # Fail closed on confirmation-required actions (audit P0#3b).
                # A workflow has no per-call interactive confirmation channel,
                # so an action flagged requires_confirmation must not run
                # unattended (the previous behavior silently bypassed the gate).
                # The workflow-native way to gate a sensitive step is to place a
                # Human Intervention node before it.
                if action.requires_confirmation:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=(
                            f"Action '{action.name}' requires confirmation and "
                            "cannot run unattended in a workflow. Add a Human "
                            "Intervention node before this step to gate it, or "
                            "use an action that does not require confirmation."
                        ),
                        duration_ms=_ms_since(t0),
                    )

                # Resolve auth credentials via the shared helper so workflow
                # and chat paths share identical lookup semantics (per-user
                # override, owner-exempt default fallback, etc).
                from fim_one.core.security.connector_credentials import (
                    resolve_connector_credentials,
                )

                auth_credentials: dict[str, Any] = await resolve_connector_credentials(
                    connector, context.user_id, db
                )

                # Audit every workflow connector call, mirroring the chat path
                # (chat.py `_log_connector_call`). Without this, connector calls
                # made from a workflow left no ConnectorCallLog trail. Runs in
                # its own session; conversation_id/agent_id are absent in the
                # workflow context.
                _log_user_id = context.user_id

                async def _log_connector_call(**kwargs: Any) -> None:
                    """Persist a connector call log entry in its own DB session."""
                    try:
                        from fim_one.db import create_session as _log_cs
                        from fim_one.db.models.connector_call_log import (
                            ConnectorCallLog,
                        )

                        async with _log_cs() as log_session:
                            log_session.add(
                                ConnectorCallLog(
                                    connector_id=kwargs.get("connector_id", ""),
                                    connector_name=kwargs.get("connector_name", ""),
                                    action_id=kwargs.get("action_id"),
                                    action_name=kwargs.get("action_name", ""),
                                    conversation_id=None,
                                    user_id=_log_user_id,
                                    agent_id=None,
                                    request_method=kwargs.get("request_method", ""),
                                    request_url=kwargs.get("request_url", ""),
                                    response_status=kwargs.get("response_status"),
                                    response_time_ms=kwargs.get("response_time_ms"),
                                    success=kwargs.get("success", False),
                                    error_message=kwargs.get("error_message"),
                                )
                            )
                            await log_session.commit()
                    except Exception:
                        logger.debug(
                            "Failed to log workflow connector call", exc_info=True
                        )

                adapter = ConnectorToolAdapter(
                    connector_name=connector.name,
                    connector_base_url=connector.base_url or "",
                    connector_auth_type=connector.auth_type or "none",
                    connector_auth_config=connector.auth_config,
                    action_name=action.name,
                    action_description=action.description or "",
                    action_method=action.method or "GET",
                    action_path=action.path or "",
                    action_parameters_schema=action.parameters_schema,
                    action_request_body_template=action.request_body_template,
                    action_response_extract=action.response_extract,
                    # Always False here: confirmation-required actions are
                    # rejected above (fail-closed); a Human Intervention node is
                    # the workflow-native approval gate.
                    action_requires_confirmation=False,
                    auth_credentials=auth_credentials,
                    connector_id=connector_id,
                    action_id=action_id,
                    on_call_complete=_log_connector_call,
                )

            output = await adapter.run(**params)
            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500] if isinstance(output, str) else str(output)[:500],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("Connector node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Connector error: {exc}",
                duration_ms=_ms_since(t0),
            )
class HumanInterventionExecutor:
    """Pause workflow execution and wait for external human approval.

    Creates a ``WorkflowApproval`` record, then polls the database for a
    status change.  Supports timeout-based expiration and configurable
    polling intervals.

    Node data shape::

        {
            "title": "Review content before publishing",
            "description": "Please review the generated content",
            "assignee": "{{manager_id}}",       # optional, supports interpolation
            "timeout_hours": 24,                 # default 24
            "poll_interval_seconds": 5,          # default 5
            "output_variable": "approval_result"
        }
    """

    DEFAULT_POLL_INTERVAL: float = 5.0
    DEFAULT_TIMEOUT_HOURS: float = 24.0

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "object", "description": "Approval result with status, decision_by, and decision_note"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            return await self._execute_inner(node, store, context, t0)
        except Exception as exc:
            logger.exception("HumanIntervention node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"HumanIntervention error: {exc}",
                duration_ms=_ms_since(t0),
            )

    async def _execute_inner(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
        t0: float,
    ) -> NodeResult:
        # Extract node configuration
        title_template = node.data.get("title", "Approval required")
        description_template = node.data.get("description", "")
        assignee_template = node.data.get("assignee", "")
        timeout_hours = float(
            node.data.get("timeout_hours", self.DEFAULT_TIMEOUT_HOURS)
        )
        poll_interval = float(
            node.data.get("poll_interval_seconds", self.DEFAULT_POLL_INTERVAL)
        )

        # Interpolate templates
        title = await store.interpolate(title_template) if title_template else "Approval required"
        description = await store.interpolate(description_template) if description_template else None
        assignee = await store.interpolate(assignee_template) if assignee_template else None

        # When no DB is available, auto-approve immediately (headless / test mode)
        db_session_factory = context.db_session_factory
        if db_session_factory is None:
            prompt_message = node.data.get("prompt_message", "")
            if prompt_message:
                message = await store.interpolate(prompt_message)
            else:
                message = "Please review and approve this step."
            output_variable = node.data.get("output_variable", "approval_result")
            output = {
                "status": "approved",
                "assignee": assignee or "",
                "timeout_hours": timeout_hours,
                "message": message,
                "auto_approved": True,
            }
            await store.set(f"{node.id}.output", output)
            await store.set(output_variable, output)
            await store.set(f"{node.id}.{output_variable}", output)
            logger.info(
                "HumanIntervention node %s auto-approved (no db_session_factory)",
                node.id,
            )
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output,
                duration_ms=_ms_since(t0),
            )

        # Create approval record
        import uuid as _uuid

        approval_id = str(_uuid.uuid4())

        async with db_session_factory() as db:
            from fim_one.db.models.workflow import WorkflowApproval

            approval = WorkflowApproval(
                id=approval_id,
                workflow_run_id=context.run_id,
                node_id=node.id,
                title=title,
                description=description,
                assignee=assignee if assignee else None,
                status="pending",
                timeout_hours=timeout_hours,
            )
            db.add(approval)
            await db.commit()

        logger.info(
            "HumanIntervention node %s created approval %s (timeout=%.1fh)",
            node.id,
            approval_id,
            timeout_hours,
        )

        # Poll for approval status change
        timeout_seconds = timeout_hours * 3600
        start_wait = time.time()

        while True:
            elapsed = time.time() - start_wait

            # Check timeout
            if elapsed >= timeout_seconds:
                # Mark as expired in DB
                async with db_session_factory() as db:
                    from fim_one.db.models.workflow import WorkflowApproval
                    from sqlalchemy import select

                    result = await db.execute(
                        select(WorkflowApproval).where(
                            WorkflowApproval.id == approval_id
                        )
                    )
                    record = result.scalar_one_or_none()
                    if record and record.status == "pending":
                        from datetime import datetime, timezone

                        record.status = "expired"
                        record.resolved_at = datetime.now(timezone.utc)
                        await db.commit()

                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Approval timed out after {timeout_hours}h",
                    duration_ms=_ms_since(t0),
                )

            # Poll DB for status change
            async with db_session_factory() as db:
                from fim_one.db.models.workflow import WorkflowApproval
                from sqlalchemy import select

                result = await db.execute(
                    select(WorkflowApproval).where(
                        WorkflowApproval.id == approval_id
                    )
                )
                record = result.scalar_one_or_none()

            if record is None:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Approval record not found (deleted externally?)",
                    duration_ms=_ms_since(t0),
                )

            if record.status == "approved":
                output = {
                    "status": "approved",
                    "approval_id": approval_id,
                    "decision_by": record.decision_by,
                    "decision_note": record.decision_note,
                }
                await store.set(f"{node.id}.output", output)
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.COMPLETED,
                    output=output,
                    duration_ms=_ms_since(t0),
                )

            if record.status == "rejected":
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Approval rejected: {record.decision_note or 'No reason given'}",
                    duration_ms=_ms_since(t0),
                )

            if record.status == "expired":
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Approval expired after {timeout_hours}h",
                    duration_ms=_ms_since(t0),
                )

            # Still pending -- wait and poll again
            await asyncio.sleep(poll_interval)


class MCPExecutor:
    """Connect to an MCP server, invoke a tool, and store the result.

    Node data shape::

        {
            "server_id": "uuid-of-mcp-server",
            "tool_name": "tool_name_string",
            "parameters": {"key": "{{variable}}", ...},
            "output_variable": "result_var_name"
        }
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "any", "description": "MCP tool execution result"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        mcp_client = None
        try:
            server_id: str = node.data.get("server_id", "")
            tool_name: str = node.data.get("tool_name", "")
            params_template: dict[str, Any] = node.data.get("parameters", {})
            output_variable: str = node.data.get("output_variable", "")

            if not server_id:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="MCP node requires server_id",
                    duration_ms=_ms_since(t0),
                )
            if not tool_name:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="MCP node requires tool_name",
                    duration_ms=_ms_since(t0),
                )

            # Interpolate parameters
            params: dict[str, Any] = {}
            for key, val in params_template.items():
                if isinstance(val, str) and "{{" in val:
                    params[key] = await store.interpolate(val)
                else:
                    params[key] = val

            # Load MCP server config from DB
            from fim_one.db import create_session
            from fim_one.db.models.mcp_server import MCPServer
            from sqlalchemy import select

            async with create_session() as db:
                result = await db.execute(
                    select(MCPServer).where(MCPServer.id == server_id)
                )
                server = result.scalar_one_or_none()

                if not server:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=f"MCP server '{server_id}' not found",
                        duration_ms=_ms_since(t0),
                    )

                if not server.is_active:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=f"MCP server '{server.name}' is disabled",
                        duration_ms=_ms_since(t0),
                    )

                # Resolve per-user credentials vs server-level env/headers
                effective_env: dict[str, str] | None = server.env
                effective_headers: dict[str, str] | None = server.headers

                if context.user_id:
                    has_user_cred = False
                    try:
                        from fim_one.db.models.mcp_server_credential import (
                            MCPServerCredential,
                        )

                        cred_result = await db.execute(
                            select(MCPServerCredential).where(
                                MCPServerCredential.server_id == server_id,
                                MCPServerCredential.user_id == context.user_id,
                            )
                        )
                        cred = cred_result.scalar_one_or_none()
                        if cred:
                            env_dict: dict[str, str] = dict(cred.env_blob) if cred.env_blob else {}
                            if env_dict:
                                effective_env = {
                                    **(effective_env or {}),
                                    **env_dict,
                                }
                            headers_dict: dict[str, str] = dict(cred.headers_blob) if cred.headers_blob else {}
                            if headers_dict:
                                effective_headers = {
                                    **(effective_headers or {}),
                                    **headers_dict,
                                }
                            has_user_cred = bool(env_dict or headers_dict)
                    except Exception:
                        logger.warning(
                            "Failed to load MCP credentials for user %s, server %s",
                            context.user_id,
                            server_id,
                            exc_info=True,
                        )

                    # allow_fallback gate (align with chat.py MCP path): a
                    # non-owner with no usable per-user credential must not
                    # silently fall back to the owner's server-level env/headers.
                    if (
                        not has_user_cred
                        and not getattr(server, "allow_fallback", True)
                        and server.user_id != context.user_id
                    ):
                        return NodeResult(
                            node_id=node.id,
                            status=NodeStatus.FAILED,
                            error=(
                                f"MCP server '{server.name}' requires your own "
                                "credentials (fallback to the owner's is "
                                "disabled). Configure per-user credentials to "
                                "use it in a workflow."
                            ),
                            duration_ms=_ms_since(t0),
                        )

            # Connect to MCP server and discover tools
            from fim_one.core.mcp import MCPClient

            mcp_client = MCPClient()

            tools: list[Any] = []
            if server.transport == "stdio" and server.command:
                from fim_one.core.security import is_stdio_allowed

                if not is_stdio_allowed():
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=(
                            "STDIO MCP transport is disabled. "
                            "Set ALLOW_STDIO_MCP=true to enable."
                        ),
                        duration_ms=_ms_since(t0),
                    )
                tools = await mcp_client.connect_stdio(
                    name=server.name,
                    command=server.command,
                    args=server.args or [],
                    env=effective_env,
                    working_dir=server.working_dir,
                )
            elif server.transport == "sse" and server.url:
                tools = await mcp_client.connect_sse(
                    name=server.name,
                    url=server.url,
                    headers=effective_headers,
                )
            elif server.transport == "streamable_http" and server.url:
                tools = await mcp_client.connect_streamable_http(
                    name=server.name,
                    url=server.url,
                    headers=effective_headers,
                )
            else:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=(
                        f"MCP server '{server.name}' has unsupported or "
                        f"misconfigured transport: {server.transport}"
                    ),
                    duration_ms=_ms_since(t0),
                )

            # Find the requested tool by original name
            target_tool = None
            for t in tools:
                # MCPToolAdapter stores original name as _original_name
                original = getattr(t, "_original_name", "")
                if original == tool_name:
                    target_tool = t
                    break

            if target_tool is None:
                available = [getattr(t, "_original_name", t.name) for t in tools]
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=(
                        f"Tool '{tool_name}' not found on MCP server "
                        f"'{server.name}'. Available tools: {available}"
                    ),
                    duration_ms=_ms_since(t0),
                )

            # Execute the tool
            logger.info(
                "MCP node %s calling %s.%s with params: %s",
                node.id,
                server.name,
                tool_name,
                list(params.keys()),
            )
            output = await target_tool.run(**params)

            # Store output
            await store.set(f"{node.id}.output", output)
            if output_variable:
                await store.set(output_variable, output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500] if isinstance(output, str) else str(output)[:500],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("MCP node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"MCP tool error: {exc}",
                duration_ms=_ms_since(t0),
            )
        finally:
            if mcp_client:
                try:
                    await mcp_client.disconnect_all()
                except Exception:
                    logger.warning(
                        "Failed to disconnect MCP client for node %s",
                        node.id,
                        exc_info=True,
                    )
EXECUTOR_REGISTRY: dict[NodeType, type[NodeExecutor]] = {
    NodeType.START: StartExecutor,
    NodeType.END: EndExecutor,
    NodeType.LLM: LLMExecutor,
    NodeType.CONDITION_BRANCH: ConditionBranchExecutor,
    NodeType.AGENT: AgentExecutor,
    NodeType.KNOWLEDGE_RETRIEVAL: KnowledgeRetrievalExecutor,
    NodeType.CONNECTOR: ConnectorExecutor,
    NodeType.HUMAN_INTERVENTION: HumanInterventionExecutor,
    NodeType.MCP: MCPExecutor,
}


def get_executor(node_type: NodeType) -> NodeExecutor:
    """Return an executor instance for the given node type.

    Raises
    ------
    ValueError
        If no executor is registered for the given type.
    """
    cls = EXECUTOR_REGISTRY.get(node_type)
    if cls is None:
        raise ValueError(f"No executor registered for node type: {node_type}")
    return cls()
