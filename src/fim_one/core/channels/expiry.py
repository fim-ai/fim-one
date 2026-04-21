"""Periodic sweeper for stale ``ConfirmationRequest`` rows.

A confirmation card can sit in a Feishu group for an arbitrarily long
time — the normal consumer, ``FeishuGateHook.wait_for_decision``,
polls with its own timeout, but rows produced by the Approval
Playground (or by a hook whose agent aborted before polling completed)
stay ``pending`` forever.  That leaves clickable buttons in the chat
and, worse, lets a click days later mutate agent state that has long
since been torn down.

This module defines ``ConfirmationRequestExpirer`` — a background task
launched from the FastAPI lifespan — which walks the
``confirmation_requests`` table every ``sweep_interval_seconds`` and
flips any ``status='pending'`` row older than ``max_age_minutes`` to
``status='expired'``, stamping ``responded_at`` with the current
time.  The existing ``/callback`` handler already degrades gracefully
when it sees a terminal row on click (returns a warning toast plus a
grey "Expired" decided card), so no UI-side changes are needed once
rows are swept.

Cards themselves are *not* proactively replaced — Feishu doesn't
let us push an unsolicited card update without an incoming
interaction, so we let the next click trigger the replacement.  If
nobody ever clicks, the stale card just becomes a visual relic
pointing at a DB row that now says ``expired``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.web.models.channel import ConfirmationRequest

logger = logging.getLogger(__name__)


class ConfirmationRequestExpirer:
    """Periodically mark stale pending confirmations as expired.

    Parameters
    ----------
    max_age_minutes:
        Rows in ``status='pending'`` whose ``created_at`` is older than
        this many minutes are flipped to ``status='expired'``.
        Defaults to 24 hours.
    sweep_interval_seconds:
        How often the background loop runs.  Defaults to 10 minutes.
    """

    def __init__(
        self,
        *,
        max_age_minutes: int = 60 * 24,
        sweep_interval_seconds: int = 600,
    ) -> None:
        self.max_age_minutes = max_age_minutes
        self.sweep_interval_seconds = sweep_interval_seconds

    async def sweep(self, db: AsyncSession) -> int:
        """Run one sweep pass; returns the number of rows expired."""
        cutoff = datetime.now(UTC) - timedelta(minutes=self.max_age_minutes)
        stmt = (
            sa_update(ConfirmationRequest)
            .where(
                ConfirmationRequest.status == "pending",
                ConfirmationRequest.created_at < cutoff,
            )
            .values(status="expired", responded_at=datetime.now(UTC))
        )
        result = cast(CursorResult[Any], await db.execute(stmt))
        await db.commit()
        expired = int(result.rowcount or 0)
        if expired > 0:
            logger.info(
                "ConfirmationRequestExpirer: expired %d stale pending row(s) "
                "older than %d minutes",
                expired,
                self.max_age_minutes,
            )
        return expired

    async def run_loop(self) -> None:
        """Sweep in a loop until cancelled.  Launched from lifespan."""
        from fim_one.db import create_session

        logger.info(
            "ConfirmationRequestExpirer started "
            "(interval=%ds, max_age=%dm)",
            self.sweep_interval_seconds,
            self.max_age_minutes,
        )
        while True:
            try:
                await asyncio.sleep(self.sweep_interval_seconds)
                async with create_session() as db:
                    await self.sweep(db)
            except asyncio.CancelledError:
                logger.info("ConfirmationRequestExpirer stopped")
                break
            except Exception:
                logger.exception("ConfirmationRequestExpirer sweep failed")


__all__ = ["ConfirmationRequestExpirer"]
