"""Node executors — one class per workflow node type.

Each executor implements the ``NodeExecutor`` protocol: given a node definition,
a variable store, and an execution context, it performs the node's action and
returns a ``NodeResult``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from typing import Any, Protocol, runtime_checkable

from .types import ExecutionContext, NodeResult, NodeStatus, NodeType, WorkflowNodeDef
from .variable_store import VariableStore

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
            from fim_one.web.deps import get_effective_fast_llm, get_effective_llm
            from fim_one.db import create_session

            # Get config from node data (accept both frontend and legacy keys)
            prompt_template = node.data.get("prompt_template", "") or node.data.get("prompt", "")
            system_prompt = node.data.get("system_prompt", "")
            model_tier = node.data.get("model_tier", "fast")  # "fast" or "main"

            # Interpolate variables in prompts
            prompt = await store.interpolate(prompt_template)
            if system_prompt:
                system_prompt = await store.interpolate(system_prompt)

            # Resolve LLM
            async with create_session() as db:
                if model_tier == "main":
                    llm = await get_effective_llm(db)
                else:
                    llm = await get_effective_fast_llm(db)

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
            from simpleeval import simple_eval

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

        answer = (result.message.content or "").strip()
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


# ---------------------------------------------------------------------------
# 5. QuestionClassifier node
# ---------------------------------------------------------------------------


class QuestionClassifierExecutor:
    """Use LLM to classify input text into one of several categories.

    Node data contains ``categories``: a list of dicts with ``label``
    and ``handle`` (sourceHandle to activate).
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "The classification label chosen by the LLM"},
            {"name": "active_handle", "type": "string", "description": "The source handle ID for the matched category"},
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
            from fim_one.web.deps import get_effective_fast_llm
            from fim_one.db import create_session

            input_var = node.data.get("input_variable", "") or node.data.get("prompt", "")
            # Frontend sends "classes", legacy uses "categories"
            categories = node.data.get("classes", []) or node.data.get("categories", [])

            if not categories:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="No categories defined for classification",
                    duration_ms=_ms_since(t0),
                )

            # Get the input text
            if input_var and "{{" in input_var:
                text = await store.interpolate(input_var)
            elif input_var:
                text = str(await store.get(input_var, ""))
            else:
                text = ""

            # Build classification prompt
            category_list = "\n".join(
                f"- {c['label']}" for c in categories if c.get("label")
            )
            system_prompt = (
                "You are a text classifier. Classify the given text into exactly one "
                "of the following categories. Respond with ONLY the category label, "
                "nothing else.\n\n"
                f"Categories:\n{category_list}"
            )

            async with create_session() as db:
                llm = await get_effective_fast_llm(db)

            result = await llm.chat([
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=text),
            ])

            classification = (result.message.content or "").strip()

            # Find the matching category handle
            # Frontend uses "class-{id}" format for sourceHandles
            active_handle: str | None = None
            for cat in categories:
                if cat.get("label", "").strip().lower() == classification.lower():
                    cat_id = cat.get("id", cat.get("handle", cat.get("label", "")))
                    active_handle = f"class-{cat_id}" if cat_id and not cat_id.startswith("class-") else cat_id
                    break

            # Fallback to default if no exact match
            if active_handle is None:
                default_handle = node.data.get("default_handle", "")
                if default_handle:
                    active_handle = default_handle
                elif categories:
                    cat_id = categories[0].get("id", categories[0].get("handle", categories[0].get("label", "")))
                    active_handle = f"class-{cat_id}" if cat_id and not cat_id.startswith("class-") else cat_id
                else:
                    active_handle = "default"

            await store.set(f"{node.id}.output", classification)
            await store.set(f"{node.id}.active_handle", active_handle)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=classification,
                active_handles=[active_handle],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("QuestionClassifier node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Classification error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 6. Agent node
# ---------------------------------------------------------------------------


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
                    from fim_one.web.models import Agent
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
                    results = await kb_manager.search(
                        kb_id=kb_id, query=query, top_k=top_k
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
                from fim_one.web.models.connector import Connector, ConnectorAction
                from sqlalchemy import select

                conn_result = await db.execute(
                    select(Connector).where(Connector.id == connector_id)
                )
                connector = conn_result.scalar_one_or_none()
                if not connector:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=f"Connector '{connector_id}' not found",
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

                # Decrypt auth credentials if present
                auth_credentials: dict[str, str] = {}
                from fim_one.web.models.connector_credential import ConnectorCredential

                cred_result = await db.execute(
                    select(ConnectorCredential).where(
                        ConnectorCredential.connector_id == connector_id,
                        ConnectorCredential.user_id == context.user_id,
                    )
                )
                cred = cred_result.scalar_one_or_none()
                if cred:
                    from fim_one.core.security.encryption import decrypt_credential

                    auth_credentials = decrypt_credential(cred.credentials_blob)

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
                    action_requires_confirmation=False,
                    auth_credentials=auth_credentials,
                    connector_id=connector_id,
                    action_id=action_id,
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


# ---------------------------------------------------------------------------
# 9. HTTPRequest node
# ---------------------------------------------------------------------------


class HTTPRequestExecutor:
    """Execute a raw HTTP request via aiohttp."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "object", "description": "Response body (parsed JSON if possible, else string)"},
            {"name": "status_code", "type": "integer", "description": "HTTP response status code"},
            {"name": "headers", "type": "object", "description": "Response headers as a dict"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            import httpx

            from fim_one.core.security import get_safe_async_client

            url_template = node.data.get("url", "")
            method = node.data.get("method", "GET").upper()
            headers_template = node.data.get("headers", {})
            body_template = node.data.get("body", "")
            timeout = node.data.get("timeout", 30)

            url = await store.interpolate(url_template)

            # Interpolate headers
            headers: dict[str, str] = {}
            for key, val in headers_template.items():
                if isinstance(val, str) and "{{" in val:
                    headers[key] = await store.interpolate(val)
                else:
                    headers[key] = str(val)

            # Interpolate body
            body: str | None = None
            if body_template:
                if isinstance(body_template, str):
                    body = await store.interpolate(body_template)
                else:
                    body = json.dumps(body_template)

            async with get_safe_async_client(timeout=timeout) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=headers if headers else None,
                    content=body.encode("utf-8") if body else None,
                )

            # Parse response body — try JSON first, fall back to raw text
            raw_text = resp.text
            try:
                output: Any = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                output = raw_text

            status_code = resp.status_code
            response_headers = dict(resp.headers)

            await store.set(f"{node.id}.output", output)
            await store.set(f"{node.id}.status_code", status_code)
            await store.set(f"{node.id}.headers", response_headers)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=f"HTTP {status_code}: {raw_text[:200]}",
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("HTTPRequest node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"HTTP request error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 10. VariableAssign node
# ---------------------------------------------------------------------------


class VariableAssignExecutor:
    """Evaluate an expression and assign the result to a variable."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "object", "description": "Dict of all assigned variable names and their values"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from simpleeval import simple_eval

            assignments = node.data.get("assignments", [])
            snapshot = _flatten_eval_names(await store.snapshot_safe())

            results: dict[str, Any] = {}
            for assignment in assignments:
                var_name = assignment.get("variable", "")
                expression = assignment.get("expression", "")
                if not var_name:
                    continue

                if expression and "{{" in expression:
                    # Template interpolation mode
                    value = await store.interpolate(expression)
                elif expression:
                    # Safe expression evaluation
                    try:
                        value = simple_eval(expression, names=snapshot)
                    except Exception as e:
                        logger.warning(
                            "Expression '%s' failed: %s", expression, e
                        )
                        value = None
                else:
                    value = assignment.get("value")

                await store.set(var_name, value)
                await store.set(f"{node.id}.{var_name}", value)
                results[var_name] = value

            await store.set(f"{node.id}.output", results)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=results,
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("VariableAssign node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Variable assign error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 11. TemplateTransform node
# ---------------------------------------------------------------------------


class TemplateTransformExecutor:
    """Render a Jinja2 template with variables from the store."""

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "string", "description": "Rendered template output"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            from jinja2.sandbox import SandboxedEnvironment

            template_str = node.data.get("template", "")
            if not template_str:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="TemplateTransform node has no template",
                    duration_ms=_ms_since(t0),
                )

            snapshot = await store.snapshot_safe()
            env = SandboxedEnvironment()
            template = env.from_string(template_str)
            output = template.render(**snapshot)

            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("TemplateTransform node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Template error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 12. CodeExecution node
# ---------------------------------------------------------------------------


class CodeExecutionExecutor:
    """Execute Python code in a subprocess for isolation.

    The user code is written to a temporary file and executed via
    ``asyncio.create_subprocess_exec`` with a 30-second timeout.  The code
    should print its result as JSON to stdout.  This avoids blocking the
    event loop **and** prevents sandbox escape (no in-process ``exec``).
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "object", "description": "Code execution result (parsed from stdout JSON)"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        tmp_path: str | None = None
        try:
            code = node.data.get("code", "")
            if not code:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="CodeExecution node has no code",
                    duration_ms=_ms_since(t0),
                )

            snapshot = await store.snapshot_safe()

            # Build a wrapper script that injects variables and captures output
            wrapper = (
                "import json, math, re, sys\n"
                f"variables = json.loads({json.dumps(json.dumps(snapshot, default=str))})\n"
                "\n"
                f"{code}\n"
                "\n"
                "# Emit result as JSON to stdout\n"
                "if 'result' in dir() and result is not None:\n"
                "    _out = result\n"
                "elif 'output' in dir() and output is not None:\n"
                "    _out = output\n"
                "else:\n"
                "    _out = None\n"
                "if _out is not None:\n"
                "    print(json.dumps(_out, default=str))\n"
            )

            # Write to temp file
            fd = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            )
            tmp_path = fd.name
            fd.write(wrapper)
            fd.close()

            # Run in subprocess with 30s timeout
            import sys

            proc = await asyncio.create_subprocess_exec(
                sys.executable, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Code execution timed out (30s limit)",
                    duration_ms=_ms_since(t0),
                )

            if proc.returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Code execution error: {stderr_text[:500]}",
                    duration_ms=_ms_since(t0),
                )

            stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()

            # Parse stdout as JSON if possible, else use raw string
            output: Any = None
            if stdout_text:
                try:
                    output = json.loads(stdout_text)
                except (json.JSONDecodeError, ValueError):
                    output = stdout_text

            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=str(output)[:500] if output is not None else "",
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("CodeExecution node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Code execution error: {exc}",
                duration_ms=_ms_since(t0),
            )
        finally:
            if tmp_path:
                import os

                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# 13. Iterator node
# ---------------------------------------------------------------------------


class IteratorExecutor:
    """Validate and prepare a list for iteration by downstream nodes.

    The executor resolves the ``list_variable`` from the store, validates
    it is a list (or a JSON string that parses to a list), and stores it
    as the node's output along with metadata.  The actual iteration loop
    (setting ``current_item`` / ``current_index`` per iteration) is
    handled by the engine — this executor just validates and prepares.
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "array", "description": "The resolved list of items to iterate over"},
            {"name": "count", "type": "integer", "description": "Number of items in the list"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            list_variable = node.data.get("list_variable", "")
            iterator_variable = node.data.get("iterator_variable", "current_item")
            index_variable = node.data.get("index_variable", "current_index")
            max_iterations = node.data.get("max_iterations", 100)

            if not list_variable:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Iterator node has no list_variable configured",
                    duration_ms=_ms_since(t0),
                )

            # Resolve the list variable — support both {{ref}} and direct key
            if "{{" in list_variable:
                raw_value = await store.interpolate(list_variable)
            else:
                raw_value = await store.get(list_variable)

            # Parse as list: if raw_value is a string, try JSON parse
            items: list[Any]
            if isinstance(raw_value, list):
                items = raw_value
            elif isinstance(raw_value, str):
                raw_value = raw_value.strip()
                if not raw_value:
                    items = []
                else:
                    try:
                        parsed = json.loads(raw_value)
                        if isinstance(parsed, list):
                            items = parsed
                        else:
                            return NodeResult(
                                node_id=node.id,
                                status=NodeStatus.FAILED,
                                error=f"Iterator list_variable resolved to non-list JSON: {type(parsed).__name__}",
                                duration_ms=_ms_since(t0),
                            )
                    except (json.JSONDecodeError, ValueError):
                        return NodeResult(
                            node_id=node.id,
                            status=NodeStatus.FAILED,
                            error=f"Iterator list_variable is not valid JSON list: {raw_value[:100]}",
                            duration_ms=_ms_since(t0),
                        )
            elif raw_value is None:
                items = []
            else:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"Iterator list_variable resolved to unsupported type: {type(raw_value).__name__}",
                    duration_ms=_ms_since(t0),
                )

            # Enforce max_iterations limit
            if len(items) > max_iterations:
                items = items[:max_iterations]

            # Store the list and metadata for the engine and downstream nodes
            await store.set(f"{node.id}.output", items)
            await store.set(f"{node.id}.count", len(items))
            await store.set(f"{node.id}.iterator_variable", iterator_variable)
            await store.set(f"{node.id}.index_variable", index_variable)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=items,
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("Iterator node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Iterator error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# 14. VariableAggregator node
# ---------------------------------------------------------------------------


class VariableAggregatorExecutor:
    """Merge outputs from multiple upstream nodes into a single variable.

    Supports four aggregation modes:
    - ``list``:  collect all resolved values into an array.
    - ``concat``:  concatenate string representations with a separator.
    - ``merge``:  deep-merge dict values (later dicts override earlier).
    - ``first_non_empty``:  return the first non-null/non-empty value.
    """

    @staticmethod
    def output_schema() -> list[dict[str, str]]:
        return [
            {"name": "output", "type": "any", "description": "Aggregated result from multiple variables"},
        ]

    async def execute(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
    ) -> NodeResult:
        t0 = time.time()
        try:
            variables: list[str] = node.data.get("variables", [])
            mode: str = node.data.get("mode", "list")
            separator: str = node.data.get("separator", "\n")

            if not variables:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="VariableAggregator has no variables configured",
                    duration_ms=_ms_since(t0),
                )

            # Resolve each variable reference
            resolved: list[Any] = []
            for var_ref in variables:
                if isinstance(var_ref, str) and "{{" in var_ref:
                    value = await store.interpolate(var_ref)
                    # interpolate returns the placeholder as-is if unresolved
                    if value == var_ref:
                        value = None
                elif isinstance(var_ref, str):
                    value = await store.get(var_ref)
                else:
                    value = var_ref
                resolved.append(value)

            # Aggregate based on mode
            output: Any
            if mode == "list":
                output = resolved

            elif mode == "concat":
                parts: list[str] = []
                for val in resolved:
                    if val is not None:
                        parts.append(str(val))
                output = separator.join(parts)

            elif mode == "merge":
                merged: dict[str, Any] = {}
                for val in resolved:
                    if isinstance(val, dict):
                        merged.update(val)
                output = merged

            elif mode == "first_non_empty":
                output = None
                for val in resolved:
                    if val is not None and val != "" and val != [] and val != {}:
                        output = val
                        break

            else:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=f"VariableAggregator unknown mode: {mode}",
                    duration_ms=_ms_since(t0),
                )

            await store.set(f"{node.id}.output", output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output,
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            logger.exception("VariableAggregator node %s failed", node.id)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"VariableAggregator error: {exc}",
                duration_ms=_ms_since(t0),
            )


# ---------------------------------------------------------------------------
# Registry — map NodeType to executor class
# ---------------------------------------------------------------------------

EXECUTOR_REGISTRY: dict[NodeType, type] = {
    NodeType.START: StartExecutor,
    NodeType.END: EndExecutor,
    NodeType.LLM: LLMExecutor,
    NodeType.CONDITION_BRANCH: ConditionBranchExecutor,
    NodeType.QUESTION_CLASSIFIER: QuestionClassifierExecutor,
    NodeType.AGENT: AgentExecutor,
    NodeType.KNOWLEDGE_RETRIEVAL: KnowledgeRetrievalExecutor,
    NodeType.CONNECTOR: ConnectorExecutor,
    NodeType.HTTP_REQUEST: HTTPRequestExecutor,
    NodeType.VARIABLE_ASSIGN: VariableAssignExecutor,
    NodeType.TEMPLATE_TRANSFORM: TemplateTransformExecutor,
    NodeType.CODE_EXECUTION: CodeExecutionExecutor,
    NodeType.ITERATOR: IteratorExecutor,
    NodeType.VARIABLE_AGGREGATOR: VariableAggregatorExecutor,
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
