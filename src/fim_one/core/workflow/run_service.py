"""The one place a workflow run is started.

Every entry point (interactive SSE run, batch run, trigger key, schedule,
the ``run_workflow`` agent tool, and single-node test) goes through
:class:`WorkflowRunService`. The service decides, once:

- **who the run is for** (:func:`resolve_principal`): the author for trigger
  keys and schedules; the caller for everything else. The principal's
  identity is the one used for credentials, visibility and quota, and the
  run record is attributed to it.
- **whether it may start** (:class:`RunGate`): account state, token quota and
  plan entitlement. The default gate wraps chat's existing checks; the C1
  assembly layer replaces it without changing this module's callers.
- **what the run gets**: the author's env vars only when the principal is the
  author, a full :class:`ExecutionContext` (approval nodes persist and wait),
  the workflow's own run-time limit.

It then creates the ``WorkflowRun`` record, drives the engine, accumulates
node results and token usage, persists the result, releases the rate-limit
slot and delivers the webhook.

Decisions recorded in ``dev/T10-workflow-refactor.md`` §9 (2026-11-04).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .node_gates import (
    HUMAN_INTERVENTION_DISABLED,
    HUMAN_INTERVENTION_DISABLED_DETAIL,
    has_disabled_node,
)
from .rate_limiter import WorkflowRateLimiter
from .types import ExecutionContext, NodeResult, NodeStatus, NodeType, WorkflowNodeDef

logger = logging.getLogger(__name__)

DEFAULT_RUN_TIMEOUT_SECONDS = 600


class RunMode(StrEnum):
    """Entry point that started a run; stored in ``WorkflowRun.mode``."""

    INTERACTIVE = "interactive"
    BATCH = "batch"
    TRIGGER = "trigger"
    SCHEDULE = "schedule"
    TOOL = "tool"
    TEST_NODE = "test_node"


# Unattended entry points run as the workflow's author; the rest as the caller.
AUTHOR_MODES = frozenset({RunMode.TRIGGER, RunMode.SCHEDULE})
# Entry points that only run workflows the author has switched on.
ACTIVE_ONLY_MODES = frozenset({RunMode.TRIGGER, RunMode.SCHEDULE, RunMode.TOOL})
# Entry points with no way to wait for a human decision.
NO_APPROVAL_MODES = frozenset({RunMode.TOOL})


class RunRefusedError(Exception):
    """A run was refused before it started.

    ``error_code`` and ``status_code`` follow the API's ``AppError``
    conventions so HTTP entry points can re-raise them unchanged.
    """

    def __init__(self, error_code: str, status_code: int, detail: str | None = None) -> None:
        super().__init__(detail or error_code)
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail or error_code.replace("_", " ").capitalize()


@dataclass(frozen=True)
class Principal:
    """The identity a run executes as."""

    user_id: str
    author_id: str

    @property
    def is_author(self) -> bool:
        return self.user_id == self.author_id


def resolve_principal(workflow: Any, caller_id: str | None, mode: RunMode) -> Principal:
    """Who a run of *workflow* started through *mode* by *caller_id* runs as.

    Trigger keys and schedules have no caller and run as the author. Every
    other entry point runs as the caller, including a shared workflow run by
    someone who is not its author (decision 1, 2026-11-04).
    """
    if mode in AUTHOR_MODES:
        return Principal(user_id=workflow.user_id, author_id=workflow.user_id)
    if not caller_id:
        raise RunRefusedError(
            "run_identity_missing", 403, "A workflow run needs a user identity"
        )
    return Principal(user_id=caller_id, author_id=workflow.user_id)


def author_env_vars(workflow: Any, principal: Principal) -> dict[str, str]:
    """Decrypted workflow env vars, for runs that execute as the author only.

    A run as anyone else gets none: the author's secrets would otherwise be
    interpolated into outputs that person reads.
    """
    if not principal.is_author:
        return {}
    return decrypt_workflow_env(workflow)


def decrypt_workflow_env(workflow: Any) -> dict[str, str]:
    """Decrypt ``workflow.env_vars_blob``; ``{}`` when unset or unreadable."""
    blob = getattr(workflow, "env_vars_blob", None)
    if not blob:
        return {}
    try:
        from fim_one.core.security.encryption import decrypt_credential

        return dict(decrypt_credential(blob))
    except Exception:
        logger.warning("Failed to decrypt workflow env vars for %s", workflow.id)
        return {}


def run_timeout_ms(workflow: Any) -> int:
    """Whole-run time limit: the workflow's own setting, else 10 minutes."""
    seconds = getattr(workflow, "max_run_duration_seconds", None)
    return (seconds or DEFAULT_RUN_TIMEOUT_SECONDS) * 1000


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


class RunGate(Protocol):
    """Decides whether *principal* may start a run of a workflow.

    Raises :class:`RunRefusedError` to refuse. The C1 assembly layer will
    provide the production implementation; until then
    :class:`ChatRunGate` wraps chat's existing checks.
    """

    async def check(self, principal: Principal) -> None: ...


class ChatRunGate:
    """Default gate: active accounts, then chat's quota and plan check."""

    async def check(self, principal: Principal) -> None:
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import User

        async with create_session() as db:
            rows = await db.execute(
                select(User.id, User.is_active).where(
                    User.id.in_({principal.user_id, principal.author_id})
                )
            )
            active = {uid: bool(is_active) for uid, is_active in rows.all()}
        # A disabled author's workflows stop running for everyone.
        if not active.get(principal.author_id, False):
            raise RunRefusedError(
                "workflow_owner_inactive", 403, "The workflow owner's account is disabled"
            )
        if not active.get(principal.user_id, False):
            raise RunRefusedError("account_disabled", 403, "Account disabled")

        from fim_one.web.api.chat import _check_token_quota
        from fim_one.web.exceptions import AppError

        try:
            await _check_token_quota(principal.user_id)
        except AppError as exc:
            raise RunRefusedError(exc.error_code, exc.status_code, str(exc.detail)) from None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

_NODE_EVENTS = frozenset({"node_started", "node_completed", "node_failed", "node_skipped"})


@dataclass
class RunOutcome:
    """Final state of a run, as persisted."""

    run_id: str
    status: str = "completed"
    outputs: dict[str, Any] = field(default_factory=dict)
    node_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    total_tokens: int = 0


class StartedRun:
    """A run that passed every gate and has a ``WorkflowRun`` record.

    The engine runs in a background task, so the run finishes, persists and
    releases its slot whether or not anyone is reading :meth:`events`.
    """

    _END: tuple[str, dict[str, Any]] = ("__end__", {})

    def __init__(
        self,
        service: WorkflowRunService,
        *,
        run_id: str,
        workflow: Any,
        principal: Principal,
        mode: RunMode,
        parsed: Any,
        inputs: dict[str, Any],
        context: ExecutionContext,
        engine: Any,
        cancel_event: asyncio.Event,
        holds_slot: bool,
    ) -> None:
        self.run_id = run_id
        self.workflow = workflow
        self.principal = principal
        self.mode = mode
        self.cancel_event = cancel_event
        self.outcome = RunOutcome(run_id=run_id)
        self._service = service
        self._parsed = parsed
        self._inputs = inputs
        self._context = context
        self._engine = engine
        self._holds_slot = holds_slot
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._driver: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._driver is None:
            self._driver = asyncio.create_task(self._drive())

    @property
    def done(self) -> bool:
        """True once the run has been persisted and its slot released."""
        return self._driver is not None and self._driver.done()

    def cancel(self) -> None:
        """Ask the engine to stop; the run is persisted as cancelled."""
        self.cancel_event.set()

    async def wait(self) -> RunOutcome:
        """Run to the end without reading events; return the outcome."""
        self.start()
        assert self._driver is not None
        await asyncio.shield(self._driver)
        return self.outcome

    async def events(
        self, keepalive: float | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Engine events as ``(name, data)``.

        With *keepalive* set, yields ``("keepalive", {})`` after that many
        seconds without an event, so a stream can keep its connection open.
        """
        self.start()
        while True:
            try:
                if keepalive is None:
                    item = await self._queue.get()
                else:
                    item = await asyncio.wait_for(self._queue.get(), timeout=keepalive)
            except TimeoutError:
                yield "keepalive", {}
                continue
            if item[0] == self._END[0]:
                return
            yield item

    async def _drive(self) -> None:
        start = time.time()
        out = self.outcome
        try:
            async for name, data in self._engine.execute_streaming(
                self._parsed, self._inputs, context=self._context
            ):
                if name in _NODE_EVENTS:
                    nid = data.get("node_id", "")
                    out.node_results[nid] = {**(out.node_results.get(nid) or {}), **data}
                elif name == "run_completed":
                    out.outputs = data.get("outputs", {}) or {}
                    out.status = data.get("status", "completed")
                    out.error = data.get("error") if out.status != "completed" else None
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                elif name == "run_failed":
                    out.status = "failed"
                    out.error = data.get("error")
                    out.total_tokens = int(data.get("total_tokens", 0) or 0)
                await self._queue.put((name, data))
        except asyncio.CancelledError:
            out.status = "cancelled"
            out.error = "Execution cancelled"
        except Exception as exc:
            out.status = "failed"
            out.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Workflow run %s failed", self.run_id)
            await self._queue.put(
                ("run_failed", {"run_id": self.run_id, "status": "failed", "error": out.error})
            )
        finally:
            out.duration_ms = int((time.time() - start) * 1000)
            # Each step runs even if an earlier one raises; the end marker
            # always goes out last so no reader of events() blocks forever.
            try:
                try:
                    if self._holds_slot:
                        await self._service.rate_limiter.record_run_end(
                            self.principal.user_id, self.run_id
                        )
                finally:
                    try:
                        await self._service._persist(out, started_at=start)
                    finally:
                        self._service._active_runs.pop(self.run_id, None)
                        try:
                            self._service._fire_webhook(
                                self.workflow, out, self.mode, self.principal
                            )
                        except Exception:
                            logger.exception("Webhook dispatch failed for %s", self.run_id)
            finally:
                self._queue.put_nowait(self._END)


class WorkflowRunService:
    """Starts workflow runs for every entry point. See the module docstring."""

    def __init__(
        self,
        *,
        gate: RunGate | None = None,
        rate_limiter: WorkflowRateLimiter | None = None,
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.gate: RunGate = gate or ChatRunGate()
        self.rate_limiter = rate_limiter or WorkflowRateLimiter()
        self._engine_factory = engine_factory
        # Runs executing in this process, by run id, so any entry point's run
        # can be cancelled (the engine stops; the run is persisted cancelled).
        self._active_runs: dict[str, StartedRun] = {}

    def cancel_run(self, run_id: str) -> bool:
        """Signal a run executing in this process to stop; False if not here."""
        started = self._active_runs.get(run_id)
        if started is None:
            return False
        started.cancel()
        return True

    # -- checks ---------------------------------------------------------------

    async def authorize(
        self,
        workflow: Any,
        caller_id: str | None,
        mode: RunMode,
        *,
        check_rate: bool = False,
    ) -> tuple[Principal, Any]:
        """Run every pre-start check; return ``(principal, parsed_blueprint)``.

        Raises :class:`RunRefusedError` on the first failed check.
        """
        principal = resolve_principal(workflow, caller_id, mode)
        if mode in ACTIVE_ONLY_MODES and not workflow.is_active:
            raise RunRefusedError("workflow_inactive", 403, "Workflow is inactive")
        blueprint = workflow.blueprint
        if not blueprint or not blueprint.get("nodes"):
            raise RunRefusedError("blueprint_empty", 400)

        await self.gate.check(principal)

        from .parser import BlueprintValidationError, parse_blueprint

        try:
            parsed = parse_blueprint(blueprint)
        except BlueprintValidationError as exc:
            raise RunRefusedError(f"invalid_blueprint: {exc}", 400) from None

        if has_disabled_node(parsed.nodes):
            # Stored before the switch was turned off: refuse the whole run
            # rather than skip the step.
            raise RunRefusedError(
                HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
            )

        if mode in NO_APPROVAL_MODES and any(
            node.type == NodeType.HUMAN_INTERVENTION for node in parsed.nodes
        ):
            raise RunRefusedError(
                "workflow_has_approval_step",
                409,
                "The workflow contains a human-approval step and cannot run here",
            )

        if check_rate:
            allowed, rate_error = await self.rate_limiter.check_rate_limit(principal.user_id)
            if not allowed:
                raise RunRefusedError("workflow_rate_limited", 429, rate_error)
        return principal, parsed

    # -- runs -----------------------------------------------------------------

    async def start_run(
        self,
        workflow: Any,
        *,
        caller_id: str | None,
        inputs: dict[str, Any] | None,
        mode: RunMode,
        cancel_event: asyncio.Event | None = None,
        depth: int = 0,
        authorized: tuple[Principal, Any] | None = None,
        acquire_slot: bool = True,
        single_flight: bool = False,
        record_refusal: bool = False,
    ) -> StartedRun:
        """Check, record and start a run; the engine starts immediately.

        *authorized* skips the checks when the caller already ran
        :meth:`authorize` (a batch checks once for all its items).
        *single_flight* refuses while another run of the workflow is pending
        or running. *record_refusal* writes a failed ``WorkflowRun`` when a
        check refuses, for entry points with nobody to tell (schedules).
        """
        inputs = inputs or {}
        try:
            principal, parsed = authorized or await self.authorize(workflow, caller_id, mode)
            if single_flight:
                await self._refuse_if_running(workflow.id)
        except RunRefusedError as exc:
            if record_refusal:
                await self._record_refusal(workflow, caller_id, mode, inputs, exc)
            raise

        run_id = str(uuid.uuid4())
        if acquire_slot:
            allowed, rate_error = await self.rate_limiter.acquire(principal.user_id, run_id)
            if not allowed:
                raise RunRefusedError("workflow_rate_limited", 429, rate_error)

        record_created = False
        try:
            await self._create_run_record(run_id, workflow, principal, mode, inputs)
            record_created = True

            from fim_one.db import create_session

            cancel_event = cancel_event or asyncio.Event()
            env_vars = author_env_vars(workflow, principal)
            context = ExecutionContext(
                run_id=run_id,
                user_id=principal.user_id,
                workflow_id=workflow.id,
                env_vars=env_vars,
                db_session_factory=create_session,
                depth=depth,
                cancel_event=cancel_event,
            )
            engine = self._make_engine(
                max_concurrency=5,
                cancel_event=cancel_event,
                env_vars=env_vars,
                run_id=run_id,
                user_id=principal.user_id,
                workflow_id=workflow.id,
                workflow_timeout_ms=run_timeout_ms(workflow),
            )
            started = StartedRun(
                self,
                run_id=run_id,
                workflow=workflow,
                principal=principal,
                mode=mode,
                parsed=parsed,
                inputs=inputs,
                context=context,
                engine=engine,
                cancel_event=cancel_event,
                holds_slot=acquire_slot,
            )
            self._active_runs[run_id] = started
            # Start now, not on first read: the run must persist and release
            # its slot even if the caller goes away before reading events.
            started.start()
            return started
        except BaseException:
            if acquire_slot:
                await asyncio.shield(
                    self.rate_limiter.record_run_end(principal.user_id, run_id)
                )
            if record_created:
                await asyncio.shield(self._mark_failed(run_id, "Run could not start"))
            raise

    async def run_to_completion(self, workflow: Any, **kwargs: Any) -> RunOutcome:
        """:meth:`start_run`, then wait for the outcome."""
        started = await self.start_run(workflow, **kwargs)
        return await started.wait()

    async def test_node(
        self,
        workflow: Any,
        *,
        caller_id: str,
        node: WorkflowNodeDef,
        variables: dict[str, Any],
        env_overrides: dict[str, str],
    ) -> tuple[NodeResult, dict[str, Any]]:
        """Execute one node in isolation; return its result and the variables.

        Same principal, gate and env rules as a full run. There is no
        ``db_session_factory``, so an approval node is auto-approved. Token
        usage is recorded as a ``test_node`` run so it counts toward quota.
        """
        from fim_one.core.model.usage import UsageTracker

        from .nodes import get_executor
        from .variable_store import VariableStore

        principal = resolve_principal(workflow, caller_id, RunMode.TEST_NODE)
        if has_disabled_node([node]):
            raise RunRefusedError(
                HUMAN_INTERVENTION_DISABLED, 409, HUMAN_INTERVENTION_DISABLED_DETAIL
            )
        await self.gate.check(principal)

        env_vars = {**author_env_vars(workflow, principal), **env_overrides}
        store = VariableStore(env_vars=env_vars)
        for key, value in variables.items():
            await store.set(key, value)

        usage_tracker = UsageTracker()
        context = ExecutionContext(
            run_id=f"test-{uuid.uuid4()}",
            user_id=principal.user_id,
            workflow_id=workflow.id,
            env_vars=env_vars,
            usage_tracker=usage_tracker,
        )
        executor = get_executor(node.type)

        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                executor.execute(node, store, context), timeout=node.timeout_ms / 1000
            )
        except TimeoutError:
            result = NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"Node execution timed out after {node.timeout_ms / 1000:.0f} seconds",
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            logger.warning(
                "Test-node execution error for node %s in workflow %s: %s",
                node.id,
                workflow.id,
                exc,
            )
            result = NodeResult(
                node_id=node.id,
                status=NodeStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.time() - t0) * 1000),
            )

        total_tokens = int(usage_tracker.get_summary().total_tokens or 0)
        if total_tokens > 0:
            now = datetime.now(UTC)
            await self._insert_run(
                workflow_id=workflow.id,
                user_id=principal.user_id,
                blueprint_snapshot={"nodes": [], "edges": [], "test_node": node.id},
                inputs=None,
                status="completed" if result.status == NodeStatus.COMPLETED else "failed",
                mode=RunMode.TEST_NODE,
                started_at=now,
                completed_at=now,
                duration_ms=result.duration_ms,
                total_tokens=total_tokens,
            )
        return result, await store.snapshot()

    # -- internals ------------------------------------------------------------

    def _make_engine(self, **kwargs: Any) -> Any:
        if self._engine_factory is not None:
            return self._engine_factory(**kwargs)
        from .engine import WorkflowEngine

        return WorkflowEngine(**kwargs)

    async def _refuse_if_running(self, workflow_id: str) -> None:
        from sqlalchemy import func, select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        async with create_session() as db:
            running = (
                await db.execute(
                    select(func.count()).where(
                        WorkflowRun.workflow_id == workflow_id,
                        WorkflowRun.status.in_(["pending", "running"]),
                    )
                )
            ).scalar_one()
        if running > 0:
            raise RunRefusedError(
                "workflow_already_running",
                429,
                "Workflow already has a running execution. Please wait for it to complete.",
            )

    async def _insert_run(self, **fields: Any) -> None:
        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        async with create_session() as db:
            db.add(WorkflowRun(**fields))
            await db.commit()

    async def _create_run_record(
        self,
        run_id: str,
        workflow: Any,
        principal: Principal,
        mode: RunMode,
        inputs: dict[str, Any],
    ) -> None:
        await self._insert_run(
            id=run_id,
            workflow_id=workflow.id,
            user_id=principal.user_id,
            blueprint_snapshot=workflow.blueprint,
            inputs=inputs,
            status="running",
            mode=mode.value,
            started_at=datetime.now(UTC),
        )

    async def _mark_failed(self, run_id: str, error: str) -> None:
        await self._persist(
            RunOutcome(run_id=run_id, status="failed", error=error), started_at=time.time()
        )

    async def _record_refusal(
        self,
        workflow: Any,
        caller_id: str | None,
        mode: RunMode,
        inputs: dict[str, Any],
        exc: RunRefusedError,
    ) -> None:
        user_id = workflow.user_id if mode in AUTHOR_MODES else caller_id
        if not user_id:
            return
        now = datetime.now(UTC)
        try:
            await self._insert_run(
                workflow_id=workflow.id,
                user_id=user_id,
                blueprint_snapshot=workflow.blueprint or {},
                inputs=inputs,
                status="failed",
                mode=mode.value,
                started_at=now,
                completed_at=now,
                duration_ms=0,
                error=exc.error_code,
            )
        except Exception:
            logger.exception("Failed to record refused %s run of %s", mode, workflow.id)

    async def _persist(self, out: RunOutcome, *, started_at: float) -> None:
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import WorkflowRun

        try:
            async with create_session() as db:
                run = (
                    await db.execute(select(WorkflowRun).where(WorkflowRun.id == out.run_id))
                ).scalar_one_or_none()
                if run is not None:
                    run.status = out.status
                    run.outputs = out.outputs or None
                    run.node_results = out.node_results or None
                    run.started_at = datetime.fromtimestamp(started_at, tz=UTC)
                    run.completed_at = datetime.now(UTC)
                    run.duration_ms = out.duration_ms
                    run.error = out.error
                    run.total_tokens = out.total_tokens
                    await db.commit()
        except Exception:
            logger.exception("Failed to persist workflow run %s", out.run_id)

    def _fire_webhook(
        self, workflow: Any, out: RunOutcome, mode: RunMode, principal: Principal
    ) -> None:
        """POST the run result to the author's webhook.

        The webhook belongs to the author, so a run by anyone else is
        reported without its run id, outputs or error: only the event,
        workflow, status and duration (decision 2, 2026-11-04).
        """
        url = getattr(workflow, "webhook_url", None)
        if not url or out.status not in ("completed", "failed"):
            return
        payload: dict[str, Any] = {
            "event": "run_completed" if out.status == "completed" else "run_failed",
            "workflow_id": workflow.id,
            "status": out.status,
            "duration_ms": out.duration_ms,
        }
        if principal.is_author:
            payload.update(
                run_id=out.run_id,
                outputs=out.outputs or None,
                error=out.error,
                completed_at=datetime.now(UTC).isoformat(),
            )
        if mode == RunMode.SCHEDULE:
            payload["trigger"] = "scheduled"

        from fim_one.core.utils import spawn_background

        spawn_background(deliver_webhook(url, payload))


async def deliver_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """POST a run result to the workflow's webhook URL; log, never raise."""
    from fim_one.core.security import get_safe_async_client

    try:
        async with get_safe_async_client(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "fim-one",
                },
            )
            logger.info("Webhook delivered to %s — status %d", webhook_url, resp.status_code)
    except Exception:
        logger.exception("Webhook delivery failed for %s", webhook_url)


_default_service: WorkflowRunService | None = None


def get_run_service() -> WorkflowRunService:
    """The process-wide service; its rate limiter is shared by every entry point."""
    global _default_service
    if _default_service is None:
        _default_service = WorkflowRunService()
    return _default_service
