"""Tests for workflow run retention/cleanup system."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from fim_one.core.workflow.run_cleanup import WorkflowRunCleaner
from fim_one.db.base import Base
from fim_one.db.models.workflow import Workflow, WorkflowRun


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async engine
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield an async session backed by an in-memory SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


def _make_workflow(
    *,
    user_id: str = "user-1",
    run_retention_days: int | None = None,
) -> Workflow:
    """Create a Workflow instance with sensible defaults."""
    return Workflow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name="Test Workflow",
        blueprint={"nodes": [], "edges": [], "viewport": {}},
        status="draft",
        is_active=True,
        run_retention_days=run_retention_days,
    )


def _make_run(
    workflow_id: str,
    *,
    user_id: str = "user-1",
    status: str = "completed",
    created_at: datetime | None = None,
) -> WorkflowRun:
    """Create a WorkflowRun instance with sensible defaults."""
    run = WorkflowRun(
        id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        user_id=user_id,
        blueprint_snapshot={"nodes": [], "edges": [], "viewport": {}},
        status=status,
    )
    if created_at is not None:
        run.created_at = created_at
    return run


# ---------------------------------------------------------------------------
# Tests — age-based cleanup
# ---------------------------------------------------------------------------


class TestAgeBasedCleanup:
    """Runs older than max_age_days should be deleted."""

    @pytest.mark.asyncio
    async def test_old_runs_deleted_by_age(self, db: AsyncSession) -> None:
        """Runs older than max_age_days are removed."""
        wf = _make_workflow()
        db.add(wf)

        old_run = _make_run(
            wf.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=40),
        )
        recent_run = _make_run(
            wf.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=5),
        )
        db.add_all([old_run, recent_run])
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=1000)
        deleted = await cleaner.cleanup(db)

        assert deleted == 1

        result = await db.execute(select(WorkflowRun))
        remaining = result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == recent_run.id

    @pytest.mark.asyncio
    async def test_active_runs_never_deleted(self, db: AsyncSession) -> None:
        """Runs with status pending/running should not be deleted even if old."""
        wf = _make_workflow()
        db.add(wf)

        for status in ("pending", "running"):
            run = _make_run(
                wf.id,
                status=status,
                created_at=datetime.now(UTC) - timedelta(days=100),
            )
            db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=1, max_runs_per_workflow=1000)
        deleted = await cleaner.cleanup(db)

        assert deleted == 0

        result = await db.execute(select(WorkflowRun))
        remaining = result.scalars().all()
        assert len(remaining) == 2


# ---------------------------------------------------------------------------
# Tests — per-workflow max runs
# ---------------------------------------------------------------------------


class TestMaxRunsCleanup:
    """Excess runs beyond max_runs_per_workflow should be trimmed."""

    @pytest.mark.asyncio
    async def test_excess_runs_deleted(self, db: AsyncSession) -> None:
        """When a workflow has more than max_runs, oldest are removed."""
        wf = _make_workflow()
        db.add(wf)

        runs = []
        for i in range(5):
            run = _make_run(
                wf.id,
                status="completed",
                created_at=datetime.now(UTC) - timedelta(hours=5 - i),
            )
            runs.append(run)
            db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=9999, max_runs_per_workflow=3)
        deleted = await cleaner.cleanup(db)

        assert deleted == 2

        result = await db.execute(
            select(WorkflowRun).order_by(WorkflowRun.created_at.desc())
        )
        remaining = result.scalars().all()
        assert len(remaining) == 3
        # The 3 newest should remain
        remaining_ids = {r.id for r in remaining}
        for r in runs[2:]:
            assert r.id in remaining_ids

    @pytest.mark.asyncio
    async def test_active_runs_excluded_from_excess_count(
        self, db: AsyncSession
    ) -> None:
        """Active (pending/running) runs are not counted toward the excess limit."""
        wf = _make_workflow()
        db.add(wf)

        # 3 completed runs + 2 running runs = 5 total
        completed_runs = []
        for i in range(3):
            run = _make_run(
                wf.id,
                status="completed",
                created_at=datetime.now(UTC) - timedelta(hours=10 - i),
            )
            completed_runs.append(run)
            db.add(run)

        running_runs = []
        for i in range(2):
            run = _make_run(
                wf.id,
                status="running",
                created_at=datetime.now(UTC) - timedelta(hours=20 + i),
            )
            running_runs.append(run)
            db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=9999, max_runs_per_workflow=2)
        deleted = await cleaner.cleanup(db)

        # Only 1 completed run should be deleted (3 completed - 2 max = 1 excess)
        assert deleted == 1

        result = await db.execute(select(WorkflowRun))
        remaining = result.scalars().all()
        # 2 completed + 2 running = 4
        assert len(remaining) == 4


# ---------------------------------------------------------------------------
# Tests — per-workflow custom retention
# ---------------------------------------------------------------------------


class TestCustomRetention:
    """Workflows with run_retention_days override the global default."""

    @pytest.mark.asyncio
    async def test_custom_retention_overrides_global(
        self, db: AsyncSession
    ) -> None:
        """A workflow with run_retention_days=7 should delete runs older than 7 days,
        even though the global default is 30."""
        wf_custom = _make_workflow(run_retention_days=7)
        wf_default = _make_workflow()
        db.add_all([wf_custom, wf_default])

        # Run at 15 days old: within global 30d but outside custom 7d
        run_custom = _make_run(
            wf_custom.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=15),
        )
        run_default = _make_run(
            wf_default.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=15),
        )
        db.add_all([run_custom, run_default])
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=1000)
        deleted = await cleaner.cleanup(db)

        assert deleted == 1

        result = await db.execute(select(WorkflowRun))
        remaining = result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == run_default.id

    @pytest.mark.asyncio
    async def test_custom_retention_longer_than_global(
        self, db: AsyncSession
    ) -> None:
        """A workflow with run_retention_days=90 keeps runs that global would delete."""
        wf = _make_workflow(run_retention_days=90)
        db.add(wf)

        run = _make_run(
            wf.id,
            status="failed",
            created_at=datetime.now(UTC) - timedelta(days=45),
        )
        db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=1000)
        deleted = await cleaner.cleanup(db)

        assert deleted == 0


# ---------------------------------------------------------------------------
# Tests — combined cleanup
# ---------------------------------------------------------------------------


class TestCombinedCleanup:
    """Test that both age and count cleanup work together."""

    @pytest.mark.asyncio
    async def test_both_strategies_applied(self, db: AsyncSession) -> None:
        """Age-based and count-based cleanup run in sequence."""
        wf = _make_workflow()
        db.add(wf)

        # 1 very old run (will be removed by age)
        old_run = _make_run(
            wf.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=60),
        )
        db.add(old_run)

        # 5 recent runs (will exceed max_runs=3 after old one removed)
        recent_runs = []
        for i in range(5):
            run = _make_run(
                wf.id,
                status="completed",
                created_at=datetime.now(UTC) - timedelta(hours=5 - i),
            )
            recent_runs.append(run)
            db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=3)
        deleted = await cleaner.cleanup(db)

        # 1 by age + 2 by count = 3
        assert deleted == 3

        result = await db.execute(select(WorkflowRun))
        remaining = result.scalars().all()
        assert len(remaining) == 3

    @pytest.mark.asyncio
    async def test_cleanup_with_override_params(self, db: AsyncSession) -> None:
        """Cleanup method accepts override parameters."""
        wf = _make_workflow()
        db.add(wf)

        run = _make_run(
            wf.id,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        db.add(run)
        await db.commit()

        cleaner = WorkflowRunCleaner(max_age_days=30, max_runs_per_workflow=1000)

        # With default settings, the 10-day-old run should NOT be deleted
        deleted = await cleaner.cleanup(db, max_age_days=30)
        assert deleted == 0

        # Override to 5 days — now it should be deleted
        deleted = await cleaner.cleanup(db, max_age_days=5)
        assert deleted == 1


# ---------------------------------------------------------------------------
# Tests — admin endpoint (unit-level)
# ---------------------------------------------------------------------------


class TestAdminEndpoint:
    """Verify the admin endpoint schema and cleaner integration."""

    def test_cleanup_request_schema_defaults(self) -> None:
        from fim_one.web.api.admin_workflows import CleanupRunsRequest

        req = CleanupRunsRequest()
        assert req.max_age_days is None
        assert req.max_runs_per_workflow is None

    def test_cleanup_request_schema_with_values(self) -> None:
        from fim_one.web.api.admin_workflows import CleanupRunsRequest

        req = CleanupRunsRequest(max_age_days=7, max_runs_per_workflow=50)
        assert req.max_age_days == 7
        assert req.max_runs_per_workflow == 50

    def test_cleanup_response_schema(self) -> None:
        from fim_one.web.api.admin_workflows import CleanupRunsResponse

        resp = CleanupRunsResponse(deleted_count=42)
        assert resp.deleted_count == 42
