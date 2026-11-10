"""Node executors — one class per workflow node type.

Each executor implements the ``NodeExecutor`` protocol: given a node definition,
a variable store, and an execution context, it performs the node's action and
returns a ``NodeResult``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import secrets
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .types import ExecutionContext, NodeResult, NodeStatus, NodeType, WorkflowNodeDef
from .variable_store import VARIABLE_REFERENCE, VariableStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_one.core.assembly.principal import CallContext
    from fim_one.core.tool.base import BaseTool
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


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TEMPLATE_REF = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_MISSING = object()


async def _store_output(node: WorkflowNodeDef, store: VariableStore, value: Any) -> None:
    """Store a node's result as ``{node_id}.output`` and its output variable.

    The editor lets every node name an ``output_variable`` and its variable
    picker inserts ``{{node_id.<output_variable>}}``, so the value must be
    readable under that name too, not only under ``.output``.
    """
    await store.set(f"{node.id}.output", value)
    output_variable = node.data.get("output_variable")
    if (
        isinstance(output_variable, str)
        and output_variable != "output"
        and _IDENTIFIER.fullmatch(output_variable)
    ):
        await store.set(f"{node.id}.{output_variable}", value)


def _lookup_variable(snapshot: dict[str, Any], ref: str) -> Any:
    """Resolve a variable reference the way a user writes it in the editor.

    Accepts ``{{node.var}}``, ``node.var``, ``input.var``, a nested path into a
    dict/list value (``human_1.output.status``, ``node.items.0``) and a bare
    short name (``score``) that matches the last segment of one stored key.
    Returns ``_MISSING`` when nothing matches.
    """
    ref = ref.strip()
    match = _TEMPLATE_REF.fullmatch(ref)
    if match:
        ref = match.group(1).strip()
    if not ref:
        return _MISSING
    if ref in snapshot:
        return snapshot[ref]

    parts = ref.split(".")
    # Longest stored prefix first, then walk the rest into the value.
    for cut in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:cut])
        if prefix not in snapshot:
            continue
        value: Any = snapshot[prefix]
        for part in parts[cut:]:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                return _MISSING
        return value

    if len(parts) == 1:
        # Short name or the underscore alias (``input_score``) that
        # expression-mode conditions have always accepted.
        flattened = _flatten_eval_names(snapshot)
        if ref in flattened:
            return flattened[ref]
    return _MISSING


def _as_text(value: Any) -> str:
    """String form used for equality and ``contains`` comparisons."""
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _as_number(value: Any) -> float | None:
    """Numeric form of *value*, or ``None`` (booleans are not numbers here)."""
    if isinstance(value, bool) or value is None or value is _MISSING:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_empty(value: Any) -> bool:
    if value is None or value is _MISSING:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    """Evaluate one structured condition (variable / operator / value).

    Numbers are compared numerically when both sides parse as numbers, so a
    form input of ``"80"`` still satisfies ``> 50``; everything else is
    compared as text.
    """
    if operator == "is_empty":
        return _is_empty(actual)
    if operator == "is_not_empty":
        return not _is_empty(actual)
    if operator in ("contains", "not_contains"):
        needle = _as_text(expected)
        if isinstance(actual, (list, tuple, set)):
            found = any(_as_text(item) == needle for item in actual)
        else:
            found = needle in _as_text(actual)
        return found if operator == "contains" else not found

    actual_num = _as_number(actual)
    expected_num = _as_number(expected)
    both_numeric = actual_num is not None and expected_num is not None
    if operator in ("==", "!="):
        if both_numeric:
            equal = actual_num == expected_num
        else:
            equal = _as_text(actual) == _as_text(expected)
        return equal if operator == "==" else not equal
    if operator in (">", "<", ">=", "<="):
        if not both_numeric:
            return False
        assert actual_num is not None and expected_num is not None
        if operator == ">":
            return actual_num > expected_num
        if operator == "<":
            return actual_num < expected_num
        if operator == ">=":
            return actual_num >= expected_num
        return actual_num <= expected_num
    raise ValueError(f"Unsupported condition operator: {operator!r}")


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
                        # A bare variable name reads that variable; anything
                        # that isn't one is a constant ("Request rejected.").
                        if await store.has(var_ref):
                            outputs[key] = await store.get(var_ref)
                        else:
                            outputs[key] = var_ref
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
            prompt_template = node.data.get("prompt_template", "")
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

            # Meter token usage so unattended (webhook/cron) runs are billed to
            # the workflow owner instead of being a free LLM spigot.
            if context.usage_tracker is not None and getattr(result, "usage", None):
                await context.usage_tracker.record(
                    result.usage, model=getattr(llm, "model_id", None)
                )

            await _store_output(node, store, output)

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
                            node, [cond], store, default_handle, context,
                        )
                        if llm_handle is not None:
                            active_handle = llm_handle
                            break
                        continue

                    if not expr and variable:
                        # Structured fields: resolve the variable directly
                        # rather than splicing it into a Python expression,
                        # where a dotted reference such as ``input.score`` or
                        # ``{{llm_1.output}}`` cannot be evaluated.
                        if not handle:
                            continue
                        actual = _lookup_variable(snapshot, str(variable))
                        try:
                            matched = _compare(actual, str(operator), value)
                        except ValueError as e:
                            logger.warning(
                                "Condition %s on node %s skipped: %s",
                                cond_id, node.id, e,
                            )
                            continue
                        if matched:
                            active_handle = handle
                            break
                        continue

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
                    node, conditions, store, default_handle, context,
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
        context: ExecutionContext | None = None,
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

        # Meter the classification call like the LLM node does.
        if (
            context is not None
            and context.usage_tracker is not None
            and getattr(result, "usage", None)
        ):
            await context.usage_tracker.record(
                result.usage, model=getattr(llm, "model_id", None)
            )

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
        if (failed := _missing_identity(node, context, t0)) is not None:
            return failed
        runtime = None
        try:
            from fim_one.core.agent import ReActAgent
            from fim_one.core.assembly.errors import AssemblyRefusal
            from fim_one.core.assembly.runtime import get_agent_runtime_factory

            agent_id = node.data.get("agent_id", "")
            query_template = node.data.get("prompt_template", "")

            query = await store.interpolate(query_template) if query_template else ""

            if not query:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Agent node has no query",
                    duration_ms=_ms_since(t0),
                )

            # The agent's whole runtime, from the same assembly a chat with
            # it gets: its model and instructions, its bound connectors,
            # knowledge bases and MCP servers (connected), and its
            # enforcement hooks. The agent is judged by the workflow
            # author's view; an agent that is not usable fails the step. A
            # step with no agent selected gets the bare runtime the factory
            # defines for it. The agent's shell, code and file tools work in
            # the run's own directory, never one shared with another run.
            try:
                runtime = await get_agent_runtime_factory().build(
                    _step_call_context(node, context),
                    agent_id or None,
                    conversation_id=None,
                    sandbox_id=context.sandbox_id,
                )
            except AssemblyRefusal as refused:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=refused.detail,
                    duration_ms=_ms_since(t0),
                )

            agent = ReActAgent(
                llm=runtime.llm,
                tools=runtime.tools,
                extra_instructions=runtime.instructions,
                max_iterations=10,
                agent_id=agent_id or None,
                user_id=context.user_id or None,
                hook_registry=runtime.hooks,
            )

            agent_result = await agent.run(query)
            output = agent_result.answer

            # Meter the agent's token usage to the workflow owner (same reason
            # as the LLM node — unattended runs must not be free LLM usage).
            if context.usage_tracker is not None and agent_result.usage is not None:
                _u = agent_result.usage
                await context.usage_tracker.record(
                    {
                        "prompt_tokens": _u.prompt_tokens,
                        "completion_tokens": _u.completion_tokens,
                        "total_tokens": _u.total_tokens,
                        "cache_read_input_tokens": _u.cache_read_input_tokens,
                        "cache_creation_input_tokens": _u.cache_creation_input_tokens,
                    }
                )

            await _store_output(node, store, output)

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
        finally:
            # The agent's MCP connections, in the task that opened them.
            if runtime is not None:
                try:
                    await runtime.aclose()
                except Exception:
                    logger.warning(
                        "Failed to close the agent runtime of node %s", node.id, exc_info=True
                    )


# ---------------------------------------------------------------------------
# 7. KnowledgeRetrieval node
# ---------------------------------------------------------------------------


def _missing_identity(
    node: WorkflowNodeDef, context: ExecutionContext, t0: float
) -> NodeResult | None:
    """Fail a node that resolves resources when the run has no user identity.

    Visibility and credential checks are made against ``context.user_id``.
    Without one there is nothing to check against, so the node fails rather
    than skipping the checks.
    """
    if context.user_id:
        return None
    return NodeResult(
        node_id=node.id,
        status=NodeStatus.FAILED,
        error="Workflow run has no user identity; cannot resolve resources",
        duration_ms=_ms_since(t0),
    )


def _step_call_context(node: WorkflowNodeDef, context: ExecutionContext) -> CallContext:
    """The call context of a step: the run's, at this node.

    The run service gives the run's context: the principal the run executes
    as, the workflow as the binding (so its author's view decides what the
    step may use), and the entry the run came through. A context built by
    hand has none, and the step acts as ``context.user_id`` directly.
    """
    import dataclasses

    from fim_one.core.assembly.principal import CallContext, provisional_principal

    if context.call_context is not None:
        return dataclasses.replace(context.call_context, node_id=node.id)
    return CallContext(
        principal=provisional_principal(context.user_id),
        binding=None,
        entry="workflow_node",
        workflow_run_id=context.run_id,
        node_id=node.id,
    )


async def _interpolated_parameters(
    store: VariableStore, template: dict[str, Any], schema: Any = None
) -> dict[str, Any]:
    """The step's parameters with ``{{variable}}`` references filled in.

    Every text in *template* is a template, at any depth: the values of an
    object and the items of a list are filled in like the top-level ones.
    A filled-in value is data and is never read as a template again.

    Filling a reference in yields text. With *schema* (a tool's JSON schema
    for its arguments) a text that was a template is then given the type
    the schema declares at its place, when the text is of that type:

    - ``integer``, ``number``, ``boolean``: read from the text;
    - ``object``, ``array``: the template is read as JSON first and the
      references are filled into the parsed structure, so a value can
      become a string, a number or a whole sub-tree in it, and cannot add
      keys or items next to itself. A template that is one reference takes
      the variable's value when it is of the declared type (or JSON text
      of it).

    Anything else is passed as text, for the tool to judge. A value written
    without a reference is never touched.
    """
    filled = await _filled(store, template, schema if isinstance(schema, dict) else None)
    return filled if isinstance(filled, dict) else {}


def _declared_types(schema: dict[str, Any] | None) -> list[str]:
    declared = schema.get("type") if schema else None
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [kind for kind in declared if isinstance(kind, str)]
    return []


def _schema_of(schema: dict[str, Any] | None, part: str, key: str | None = None) -> Any:
    """The schema of one property (``properties``) or of the items (``items``)."""
    inner = schema.get(part) if schema else None
    if key is not None:
        inner = inner.get(key) if isinstance(inner, dict) else None
    return inner if isinstance(inner, dict) else None


async def _filled(store: VariableStore, written: Any, schema: dict[str, Any] | None) -> Any:
    if isinstance(written, str):
        if "{{" not in written:
            return written
        kinds = _declared_types(schema)
        kind = None if "string" in kinds else next((k for k in kinds if k != "null"), None)
        if kind in ("object", "array"):
            structure = await _filled_json(store, written, schema, kind)
            if structure is not _UNFIT:
                return structure
            return await store.interpolate(written)
        # The result is text. If the template itself is a JSON object or
        # list (a request body typed into a text parameter, an argument the
        # tool does not describe), whoever receives it will parse it: each
        # reference is replaced, where it stands, by JSON of its value, so
        # that a value cannot add keys there either.
        text = await _filled_json_text(store, written)
        if text is not None:
            return text
        if "string" in kinds:
            # Text is one of the types the tool takes: it stays text.
            return await store.interpolate(written)
        return _fit_text(await store.interpolate(written), kind)
    if isinstance(written, dict):
        return {
            key: await _filled(store, value, _schema_of(schema, "properties", key))
            for key, value in written.items()
        }
    if isinstance(written, list):
        items = _schema_of(schema, "items")
        return [await _filled(store, value, items) for value in written]
    return written


def _fit_text(text: str, kind: str | None) -> Any:
    """Filled-in *text* as a number or a boolean when *kind* asks and it is one."""
    stripped = text.strip()
    if kind == "integer":
        whole = _integer_from_text(stripped)
        return text if whole is None else whole
    if kind == "number":
        number = _number_from_text(stripped)
        return text if number is None else number
    if kind == "boolean" and stripped.lower() in ("true", "false"):
        return stripped.lower() == "true"
    return text


_UNFIT = object()


def _not_json(constant: str) -> Any:
    raise ValueError(f"{constant} is not JSON")


def _finite(number: str) -> float:
    value = float(number)
    if not math.isfinite(value):
        # 1e999 reads as infinity, which no JSON number is.
        raise ValueError(f"{number} is out of range")
    return value


def _json_value(text: str) -> Any:
    """The value *text* is the JSON of, or ``_UNFIT``.

    ``NaN`` and ``Infinity`` are not JSON, and neither is a number too
    large to be one once read (``1e999``): such text stays text.
    """
    try:
        return json.loads(text, parse_constant=_not_json, parse_float=_finite)
    except (ValueError, RecursionError):
        return _UNFIT


def _bare_value(value: Any, name: str, schema: dict[str, Any] | None) -> Any:
    """What a reference outside a JSON string (``{"n": {{v}}}``) puts in its place.

    A value that is not text is itself. Text is what the template's author
    would have got by filling it in and parsing the result, as far as that
    is one value: at a place declared as text it stays text; at one
    declared as a number or a boolean it is read strictly as that; at any
    other place (undeclared, an object, a list) it is read as JSON, so the
    text ``5`` is 5, ``true`` is true, ``null`` is null and
    ``{"k": [1, 2]}`` is that object, and text that is not JSON (or is not
    the declared object or list) stays a string. Whatever it becomes, it
    is one value at this place and cannot add keys or items next to it.
    A quoted reference (``"{{v}}"``) is not handled here: it is always a
    string.
    """
    if value is _UNFIT:
        # A variable nothing has set stays visible, as in a text.
        return f"{{{{{name}}}}}"
    if not isinstance(value, str):
        return value
    kinds = _declared_types(schema)
    if "string" in kinds:
        return value
    kind = next((k for k in kinds if k != "null"), None)
    if kind in ("integer", "number", "boolean"):
        return _fit_text(value, kind)
    read = _json_value(value)
    if read is _UNFIT:
        return value
    if kind == "object":
        return read if isinstance(read, dict) else value
    if kind == "array":
        return read if isinstance(read, list) else value
    return read


def _masked_json_template(written: str) -> tuple[str, str, list[tuple[int, int, str, bool]]]:
    """*written* with each reference outside a JSON string replaced by a marker string.

    Returns the masked text, the marker's prefix (marker ``i`` is the
    string ``"<prefix>:<i>"``, numbered over the references outside
    strings) and every reference as ``(start, end, name, in_string)``.
    """
    marker = secrets.token_hex(8)
    references: list[tuple[int, int, str, bool]] = []
    text: list[str] = []
    bare = 0
    in_string = False
    i = 0
    while i < len(written):
        ch = written[i]
        if ch == "{" and (ref := VARIABLE_REFERENCE.match(written, i)):
            references.append((i, ref.end(), ref.group(1), in_string))
            if in_string:
                text.append(ref.group(0))
            else:
                text.append(json.dumps(f"{marker}:{bare}"))
                bare += 1
            i = ref.end()
            continue
        if in_string:
            if ch == "\\":
                if ref := VARIABLE_REFERENCE.match(written, i + 1):
                    # ``"\{{v}}"``: a backslash and then the value. Left as
                    # written it would escape the value's first character
                    # ("\n...") and the rest of the value would be read as
                    # JSON. It is taken for a backslash character.
                    references.append((i, ref.end(), ref.group(1), True))
                    text.append("\\\\" + ref.group(0))
                    i = ref.end()
                    continue
                text.append(written[i : i + 2])
                i += 2
                continue
            in_string = ch != '"'
        elif ch == '"':
            in_string = True
        text.append(ch)
        i += 1
    return "".join(text), marker, references


_JSON_WHITE_SPACE = " \t\n\r"


async def _filled_json_text(store: VariableStore, written: str) -> str | None:
    """JSON written into a text parameter, with its references filled in where they stand.

    ``None`` when *written*, its references masked, is not a JSON object
    or list. Otherwise the author's text, byte for byte (its numbers, key
    order, duplicate keys, escapes and white space), with each reference
    replaced by JSON: outside a string, one JSON value (see
    :func:`_bare_value`; text that is JSON is put in as written); inside a
    string, the value's text escaped for a JSON string. Nothing a value
    holds can end a string or add a key, and ``NaN`` and ``Infinity`` are
    never written.
    """
    masked, _marker, references = _masked_json_template(written)
    if not isinstance(_json_value(masked), (dict, list)):
        return None
    out: list[str] = []
    at = 0
    for start, end, name, in_string in references:
        out.append(written[at:start])
        at = end
        value = await store.resolve(name, _UNFIT)
        if in_string:
            # A backslash right before the reference (see the scanner).
            reference = written[start:end]
            if reference.startswith("\\"):
                out.append("\\\\")
                reference = reference[1:]
            if value is None or value is _UNFIT:
                # As in any text: a reference with no value stays visible.
                out.append(reference)
                continue
            text = value if isinstance(value, str) else _json_text(value)
            out.append(json.dumps(text, ensure_ascii=False)[1:-1])
        elif isinstance(value, str) and _json_value(value) is not _UNFIT:
            out.append(value.strip(_JSON_WHITE_SPACE))
        else:
            out.append(_json_text(_bare_value(value, name, None)))
    out.append(written[at:])
    return "".join(out)


def _json_text(value: Any) -> str:
    """*value* as JSON; what JSON cannot say (``NaN``, an odd object) as a string."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


async def _filled_json(
    store: VariableStore, written: str, schema: dict[str, Any] | None, kind: str
) -> Any:
    """A template for an object or a list, filled in after it is parsed.

    Filling the references into the JSON text and parsing the result would
    let a value close a string and add keys of its own. Here each reference
    outside a JSON string is first replaced by a marker string, the text is
    parsed, and the values are then put in place of their markers; a
    reference inside a string is filled in as part of that string. Returns
    ``_UNFIT`` when the template is not JSON of the declared *kind*.
    """
    wanted: tuple[type, ...] = (dict,) if kind == "object" else (list,)
    masked, marker, references = _masked_json_template(written)
    names = [name for _, _, name, in_string in references if not in_string]
    parsed = _json_value(masked)
    if parsed is _UNFIT:
        return _UNFIT
    values = [await store.resolve(name, _UNFIT) for name in names]

    def marked(node: Any) -> int | None:
        if isinstance(node, str) and node.startswith(f"{marker}:"):
            return int(node[len(marker) + 1 :])
        return None

    if (whole := marked(parsed)) is not None:
        # The template is one reference: the variable's value, or the JSON
        # it is the text of.
        value = values[whole]
        if isinstance(value, str):
            value = _json_value(value)
        return value if isinstance(value, wanted) else _UNFIT

    async def placed(node: Any, node_schema: dict[str, Any] | None) -> Any:
        if (index := marked(node)) is not None:
            return _bare_value(values[index], names[index], node_schema)
        if isinstance(node, str):
            return await _filled(store, node, node_schema)
        if isinstance(node, dict):
            return {
                key: await placed(value, _schema_of(node_schema, "properties", key))
                for key, value in node.items()
            }
        if isinstance(node, list):
            items = _schema_of(node_schema, "items")
            return [await placed(value, items) for value in node]
        return node

    if not isinstance(parsed, wanted):
        return _UNFIT
    return await placed(parsed, schema)


# A JSON number, with an optional leading "+": ASCII digits only, no "_"
# separators, no "nan" or "inf" (all of which int() and float() accept).
_NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)
# Beyond this many digits a "whole number" is not something a tool counts with.
_MAX_INTEGER_DIGITS = 40


def _number_from_text(text: str) -> float | None:
    """*text* as a finite float when it is written as a plain number."""
    if not _NUMBER_TEXT.fullmatch(text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _integer_from_text(text: str) -> int | None:
    """*text* as an int when it is a whole number: ``"3"``, ``"3.0"``, ``"3e2"``."""
    if not _NUMBER_TEXT.fullmatch(text):
        return None
    import decimal

    try:
        value = decimal.Decimal(text)
    except decimal.InvalidOperation:
        return None
    if value.adjusted() >= _MAX_INTEGER_DIGITS or value != value.to_integral_value():
        return None
    return int(value)


class _StepFailed(Exception):
    """A step cannot go on; ``detail`` is the text of its failure."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def _action_tool(
    db: AsyncSession, call_context: CallContext, connector: Any, action_id: str
) -> tuple[BaseTool, Any]:
    """The tool that runs one action of *connector* for this step, and the action.

    The runner's credential, the refusal when they have none, and the call
    record all come from core/assembly: the same rule and the same record as
    a call made from chat. The action is ``None`` for a database
    connector, whose operations are not actions and carry no
    "requires confirmation" flag.
    """
    from fim_one.core.assembly.errors import AssemblyRefusal
    from fim_one.core.assembly.toolset import connector_tools

    if connector.type == "database":
        return await _database_operation_tool(db, call_context, connector, action_id), None

    action = next((a for a in connector.actions or [] if a.id == action_id), None)
    if not action:
        raise _StepFailed(f"Action '{action_id}' not found")

    try:
        (adapter,) = await connector_tools(
            db,
            call_context,
            [connector],
            mode="legacy",
            strict=True,
            action_ids=[action_id],
        )
    except AssemblyRefusal as refused:
        raise _StepFailed(refused.detail) from None
    return adapter, action


# How long a connector action's confirmation request stays answerable.
ACTION_CONFIRMATION_TIMEOUT_HOURS = 24.0


async def _confirmed(
    node: WorkflowNodeDef,
    context: ExecutionContext,
    call_context: CallContext,
    connector: Any,
    action: Any,
    params: dict[str, Any],
    t0: float,
) -> NodeResult | None:
    """Put an action flagged "requires confirmation" before a person.

    ``None`` once it is approved: the action may run. Otherwise the step's
    result: ``WAITING`` when the request was just made or is still open
    (the run is suspended and this step is run again once it is decided),
    ``FAILED`` when it was rejected, ran out of time, or nobody can be
    asked here.

    The request is a ``kind="confirmation"`` one bound to the workflow and
    decided by the person the run belongs to: whoever started it, or the
    workflow's owner for a trigger key or a schedule (administrators may
    always decide). The same rule and the same service as a flagged tool
    call in chat.
    """
    from fim_one.core.assembly.confirmation import ConfirmationService, ConfirmationSpec
    from fim_one.core.assembly.errors import AssemblyRefusal

    def failed(error: str, *, answered: bool = False) -> NodeResult:
        return NodeResult(
            node_id=node.id,
            status=NodeStatus.FAILED,
            error=error,
            duration_ms=_ms_since(t0),
            # A person's "no", or their silence, is not retried.
            no_retry=answered,
        )

    asked = context.suspensions.get(node.id)
    if asked is None:
        if context.db_session_factory is None or not context.can_suspend:
            return failed(
                f"Action '{action.name}' requires confirmation, and there is nobody to ask "
                "here: a single-step test and a workflow run by an agent as a tool cannot "
                "wait for a person. Run the workflow itself to be asked."
            )
        binding = call_context.binding
        principal = call_context.principal
        spec = ConfirmationSpec(
            kind="confirmation",
            initiator=principal,
            binding=binding,
            org_id=binding.org_id if binding is not None else None,
            title=f"Confirm: {connector.name} / {action.name}"[:500],
            description="A workflow step is about to run an action that requires confirmation.",
            payload={
                "tool_name": f"{connector.name}.{action.name}",
                "tool_args": params,
                "connector_id": connector.id,
                "action_id": action.id,
            },
            eligible_scope="initiator",
            eligible_user_id=principal.user_id,
            delivery="inline_only",
            channel_id=None,
            timeout_seconds=ACTION_CONFIRMATION_TIMEOUT_HOURS * 3600,
            workflow_run_id=context.run_id,
            node_id=node.id,
        )
        try:
            request = await ConfirmationService(context.db_session_factory).request(spec)
        except AssemblyRefusal as refusal:
            return failed(refusal.detail)
        return NodeResult(
            node_id=node.id,
            status=NodeStatus.WAITING,
            suspension=_suspension_of(request),
            duration_ms=_ms_since(t0),
        )

    if context.db_session_factory is None:
        return failed(f"Action '{action.name}' requires confirmation and its request is gone.")
    outcome, record = await _awaited_decision(context, asked)
    if outcome == "pending":
        return NodeResult(
            node_id=node.id, status=NodeStatus.WAITING, suspension=asked, duration_ms=_ms_since(t0)
        )
    if outcome == "approve":
        return None
    if outcome == "reject":
        note = getattr(record, "decision_note", None)
        return failed(
            f"Action '{action.name}' was not run: the confirmation was rejected"
            + (f" ({note})" if note else "")
            + ".",
            answered=True,
        )
    return failed(
        f"Action '{action.name}' was not run: nobody confirmed it within "
        f"{ACTION_CONFIRMATION_TIMEOUT_HOURS:g} hours.",
        answered=True,
    )


async def _database_operation_tool(
    db: AsyncSession, call_context: CallContext, connector: Any, operation: str
) -> BaseTool:
    """The tool that runs one operation of a database connector for this step.

    A database connector has no actions of its own: the step's ``action_id``
    names one of the operations a chat with the connector gets
    (``list_tables``, ``describe_table``, ``query``), and the tool is the
    one chat would be given, built in core/assembly: the owner's connection,
    the connector's read-only switch, row limit and timeout, the columns
    marked as personal data masked, and the call recorded.
    """
    from fim_one.core.assembly.errors import AssemblyRefusal
    from fim_one.core.assembly.toolset import (
        DATABASE_OPERATIONS,
        ToolsetError,
        database_tools,
    )

    if operation not in DATABASE_OPERATIONS:
        raise _StepFailed(
            f"Database connector '{connector.name}' has no operation '{operation}'. "
            f"Choose one of: {', '.join(DATABASE_OPERATIONS)}"
        )
    try:
        (tool,) = await database_tools(
            db, call_context, [connector], mode="legacy", strict=True, operations=[operation]
        )
    except (AssemblyRefusal, ToolsetError) as refused:
        raise _StepFailed(refused.detail) from None
    return tool


_NO_VALUE = object()


async def _run_bound_query(tool: BaseTool, sql: str, store: VariableStore) -> Any:
    """Run a database query step whose SQL has ``{{variable}}`` references.

    The references are bound as parameters by the query tool
    (``run_with_variables``), with each variable in its own type. A variable
    nothing has set fails the step, as does a tool that cannot bind.
    """
    bind = getattr(tool, "run_with_variables", None)
    if bind is None:
        raise _StepFailed("This database tool cannot run SQL with {{variable}} references")

    async def value_of(name: str) -> Any:
        value = await store.resolve(name, _NO_VALUE)
        if value is _NO_VALUE:
            raise _StepFailed(
                f"The SQL refers to {{{{{name}}}}}, which has no value in this run"
            )
        return value

    return await bind(sql, value_of)


# How a database tool reports a query it did not run or that failed. The
# model reads these in chat; a step has no reader, so it fails.
_DATABASE_TOOL_ERRORS = ("[SQL Safety Error]", "[Timeout]", "[Error]")


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
        if (failed := _missing_identity(node, context, t0)) is not None:
            return failed
        try:
            from fim_one.web.deps import get_kb_manager

            from fim_one.core.assembly.visibility import ResourceKind, authorize_use
            from fim_one.db import create_session

            kb_ids: list[str] = list(node.data.get("kb_ids", []) or [])

            # The workflow is the binding: a knowledge base is read while the
            # workflow's author owns it and it is switched on, whoever runs
            # the workflow (the same delegation as an agent's bound knowledge
            # bases). One that is not usable fails the step.
            kb_owners: dict[str, str] = {}
            if kb_ids:
                async with create_session() as db:
                    decision = await authorize_use(
                        db,
                        _step_call_context(node, context),
                        ResourceKind.KNOWLEDGE_BASE,
                        kb_ids,
                    )
                for kb_id in kb_ids:
                    if kb_id in decision.refused:
                        return NodeResult(
                            node_id=node.id,
                            status=NodeStatus.FAILED,
                            error=decision.refused[kb_id].detail,
                            duration_ms=_ms_since(t0),
                        )
                # Content lives under the owner's namespace.
                kb_owners = {kb.id: kb.user_id for kb in decision.usable}
                kb_ids = [kb_id for kb_id in kb_ids if kb_id in kb_owners]
            query_template = node.data.get("query_template", "")
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
                        query, kb_id=kb_id, user_id=kb_owners[kb_id], top_k=top_k
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
            await _store_output(node, store, combined)
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
    """Run one action of an API connector, or one operation of a database connector.

    Node data: ``connector_id``, ``action_id`` and ``parameters``. For an
    API connector ``action_id`` is the id of one of its actions and
    ``parameters`` are the action's. For a database connector ``action_id``
    is ``list_tables``, ``describe_table`` (``parameters.table_name``) or
    ``query`` (``parameters.sql``).

    An action flagged "requires confirmation" is put before a person first
    (:func:`_confirmed`): the run waits, and the action runs once approved.
    """

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
        if (failed := _missing_identity(node, context, t0)) is not None:
            return failed
        try:
            from fim_one.db import create_session

            connector_id = node.data.get("connector_id", "")
            action_id = node.data.get("action_id", "")
            params_template = node.data.get("parameters", {})

            if not connector_id or not action_id:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error="Connector node requires connector_id and action_id",
                    duration_ms=_ms_since(t0),
                )

            params = await _interpolated_parameters(store, params_template)

            async with create_session() as db:
                from sqlalchemy.orm import selectinload

                # The workflow is the binding: the connector is usable while
                # the workflow's author can see it (own or subscribed) and it
                # is switched on, whoever runs the workflow. The credential
                # is the runner's.
                from fim_one.core.assembly.visibility import (
                    ResourceKind,
                    authorize_use,
                )
                from fim_one.db.models.connector import Connector
                from fim_one.db.models.database_schema import DatabaseSchema

                call_context = _step_call_context(node, context)
                _decision = await authorize_use(
                    db,
                    call_context,
                    ResourceKind.CONNECTOR,
                    [connector_id],
                    options=(
                        selectinload(Connector.actions),
                        selectinload(Connector.database_schemas).selectinload(
                            DatabaseSchema.columns
                        ),
                    ),
                )
                # A database connector is its owner's alone (``owner_only``):
                # anyone else running the workflow is refused here.
                _usable = _decision.usable
                if not _usable:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=_decision.refused[connector_id].detail,
                        duration_ms=_ms_since(t0),
                    )
                try:
                    adapter, action = await _action_tool(
                        db, call_context, _usable[0], action_id
                    )
                except _StepFailed as failed_step:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=failed_step.detail,
                        duration_ms=_ms_since(t0),
                    )
                connector = _usable[0]

            if action is not None and action.requires_confirmation:
                # Asked after the checks above, so nobody is asked to
                # confirm a call that would be refused anyway.
                unconfirmed = await _confirmed(
                    node, context, call_context, connector, action, params, t0
                )
                if unconfirmed is not None:
                    return unconfirmed

            sql_template = params_template.get("sql")
            if (
                adapter.category == "database"
                and action_id == "query"
                and isinstance(sql_template, str)
                and "{{" in sql_template
            ):
                # The values are not written into the SQL text: each
                # reference is bound as a parameter by the query tool.
                try:
                    output = await _run_bound_query(adapter, sql_template, store)
                except _StepFailed as failed_step:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=failed_step.detail,
                        duration_ms=_ms_since(t0),
                    )
            else:
                output = await adapter.run(**params)
            if (
                adapter.category == "database"
                and isinstance(output, str)
                and output.startswith(_DATABASE_TOOL_ERRORS)
            ):
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=output,
                    duration_ms=_ms_since(t0),
                )
            await _store_output(node, store, output)

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
    """Stop the run until a human has approved.

    Asks :class:`~fim_one.core.assembly.confirmation.ConfirmationService`
    for a ``kind="workflow_approval"`` request and returns ``WAITING``
    with the request's id: nothing polls. The engine suspends the run, and
    when the request is decided or its time is up the run service
    continues the run, which runs this step again to read the outcome
    (:func:`_awaited_decision`). An assigned approval belongs to its
    assignee, who must be an active user in the run's organization; an
    unassigned one belongs to the user the run is attributed to.

    Node data shape::

        {
            "title": "Review content before publishing",
            "description": "Please review the generated content",
            "assignee": "{{manager_id}}",       # optional: user id, username or email; interpolated
            "timeout_hours": 24,                 # default 24
            "output_variable": "approval_result"
        }
    """

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

        # Run again after the run was suspended: read the outcome of the
        # request made the first time.
        asked = context.suspensions.get(node.id)
        if asked is not None and context.db_session_factory is not None:
            return await self._outcome(node, store, context, asked, timeout_hours, t0)

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

        from fim_one.core.assembly.confirmation import ConfirmationService, ConfirmationSpec
        from fim_one.core.assembly.errors import AssemblyRefusal
        from fim_one.core.assembly.principal import AuthMethod, Binding, resolve_principal

        # The run's identity and the workflow it belongs to: from the run's
        # call context when the run service started the run. The principal
        # is resolved here because an approval reads its organizations.
        run_ctx = context.call_context
        binding: Binding | None = run_ctx.binding if run_ctx is not None else None
        async with db_session_factory() as db:
            initiator = await resolve_principal(
                db,
                user_id=context.user_id,
                auth=run_ctx.principal.auth if run_ctx is not None else AuthMethod.SESSION,
            )
            if run_ctx is None:
                from sqlalchemy import select

                from fim_one.db.models.workflow import Workflow

                workflow = (
                    await db.execute(
                        select(Workflow.user_id, Workflow.org_id).where(
                            Workflow.id == context.workflow_id
                        )
                    )
                ).one_or_none()
                if workflow is not None:
                    binding = Binding(
                        kind="workflow",
                        id=context.workflow_id,
                        owner_id=workflow[0],
                        org_id=workflow[1],
                    )

        service = ConfirmationService(db_session_factory)
        spec = ConfirmationSpec(
            kind="workflow_approval",
            initiator=initiator,
            binding=binding,
            org_id=binding.org_id if binding is not None else None,
            title=title,
            description=description,
            payload={"timeout_hours": timeout_hours, "assignee": assignee or None},
            eligible_scope="assignee" if assignee else "initiator",
            eligible_user_id=assignee or initiator.user_id,
            delivery="inline_only",
            channel_id=None,
            timeout_seconds=timeout_hours * 3600,
            workflow_run_id=context.run_id,
            node_id=node.id,
        )
        try:
            request = await service.request(spec)
        except AssemblyRefusal as refusal:
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=refusal.detail,
                duration_ms=_ms_since(t0),
            )
        approval_id = str(request.id)
        logger.info(
            "HumanIntervention node %s created approval %s (timeout=%.1fh)",
            node.id,
            approval_id,
            timeout_hours,
        )
        return NodeResult(
            node_id=node.id,
            status=NodeStatus.WAITING,
            suspension=_suspension_of(request),
            duration_ms=_ms_since(t0),
        )

    async def _outcome(
        self,
        node: WorkflowNodeDef,
        store: VariableStore,
        context: ExecutionContext,
        asked: dict[str, Any],
        timeout_hours: float,
        t0: float,
    ) -> NodeResult:
        """The step's result once its approval request is no longer open."""
        outcome, record = await _awaited_decision(context, asked)
        approval_id = str(asked.get("confirmation_id") or "")
        if outcome == "pending":
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.WAITING,
                suspension=asked,
                duration_ms=_ms_since(t0),
            )
        if record is None:
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error="Approval record not found (deleted externally?)",
                duration_ms=_ms_since(t0),
            )

        if outcome == "approve":
            output = {
                "status": "approved",
                "approval_id": approval_id,
                "decision_by": record.decided_by_user_id,
                "decision_note": record.decision_note,
            }
            # Same names as the headless path above, so downstream
            # references behave identically with and without a DB.
            output_variable = node.data.get("output_variable", "approval_result")
            await _store_output(node, store, output)
            if isinstance(output_variable, str) and output_variable:
                await store.set(output_variable, output)
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output,
                duration_ms=_ms_since(t0),
            )

        if outcome == "reject":
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Approval rejected: {record.decision_note or 'No reason given'}",
                duration_ms=_ms_since(t0),
                no_retry=True,
            )

        return NodeResult(
            node_id=node.id,
            status=NodeStatus.FAILED,
            error=f"Approval timed out after {timeout_hours}h",
            duration_ms=_ms_since(t0),
            no_retry=True,
        )


def _suspension_of(request: Any) -> dict[str, Any]:
    """What a step that asked for a decision is waiting on (kept with the run)."""
    expires_at = getattr(request, "expires_at", None)
    return {
        "confirmation_id": str(request.id),
        "kind": str(getattr(request, "kind", "") or ""),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }


async def _awaited_decision(context: ExecutionContext, asked: dict[str, Any]) -> tuple[str, Any]:
    """How the request a step is waiting on stands: ``(outcome, row)``.

    ``pending`` while it can still be decided; ``approve`` / ``reject``
    once decided; ``expired`` when its time is up (a request still open
    past its deadline is closed here), when the run's ending closed it, or
    when it is gone (``row`` is then ``None``).
    """
    from datetime import UTC, datetime

    from fim_one.core.assembly.confirmation import ConfirmationService

    assert context.db_session_factory is not None
    service = ConfirmationService(context.db_session_factory)
    confirmation_id = str(asked.get("confirmation_id") or "")
    record = await service.get(confirmation_id) if confirmation_id else None
    if record is None:
        return "expired", None
    if record.status in ("approved", "approve"):
        return "approve", record
    if record.status in ("rejected", "reject"):
        return "reject", record
    if record.status != "pending":
        return "expired", record
    deadline = record.expires_at
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if datetime.now(UTC) >= deadline:
            # ``wait`` with no time between polls: it sees the deadline has
            # passed, closes the request and says so.
            outcome = await service.wait(confirmation_id, poll_seconds=0)
            return outcome, await service.get(confirmation_id)
    return "pending", record


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
        if (failed := _missing_identity(node, context, t0)) is not None:
            return failed
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

            # Load MCP server config from DB
            from fim_one.db import create_session

            async with create_session() as db:
                # The workflow is the binding, as for the connector step
                # above: usable while the workflow's author can see the
                # server and it is switched on. The credential it is opened
                # with is the runner's.
                from fim_one.core.assembly.visibility import (
                    ResourceKind,
                    authorize_use,
                )

                _decision = await authorize_use(
                    db, _step_call_context(node, context), ResourceKind.MCP_SERVER, [server_id]
                )
                if not _decision.usable:
                    return NodeResult(
                        node_id=node.id,
                        status=NodeStatus.FAILED,
                        error=_decision.refused[server_id].detail,
                        duration_ms=_ms_since(t0),
                    )
                server = _decision.usable[0]

            # Whose credential the server is opened with, or why the runner
            # may not open it, is decided in core/assembly.
            from fim_one.core.assembly.errors import AssemblyRefusal
            from fim_one.core.assembly.toolset import ToolsetError, mcp_tools

            try:
                tools, mcp_client = await mcp_tools(
                    _step_call_context(node, context), [server], mode="legacy", strict=True
                )
            except (AssemblyRefusal, ToolsetError) as refused:
                return NodeResult(
                    node_id=node.id,
                    status=NodeStatus.FAILED,
                    error=refused.detail,
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

            # The arguments, filled in and in the types the tool declares.
            params = await _interpolated_parameters(
                store, params_template, getattr(target_tool, "parameters_schema", None)
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
            await _store_output(node, store, output)
            if output_variable:
                await store.set(output_variable, output)

            return NodeResult(
                node_id=node.id,
                status=NodeStatus.COMPLETED,
                output=output[:500] if isinstance(output, str) else str(output)[:500],
                duration_ms=_ms_since(t0),
            )
        except Exception as exc:
            # The server is opened at its owner's URL, which the runner is
            # never shown and which can carry a credential; a connection
            # error quotes it. Neither the log nor the step's error keeps
            # that part.
            from fim_one.core.assembly.mcp_address import (
                strip_url_secrets,
                traceback_without_url_secrets,
            )

            logger.error("MCP node %s failed\n%s", node.id, traceback_without_url_secrets(exc))
            return NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=strip_url_secrets(f"MCP tool error: {exc}"),
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
