"""Thin persistence helpers around the `job_runs` table — the audit trail
for whole-pipeline runs (see models.JobRun). Each call opens its own
short-lived session, matching the rest of the pipeline services."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import async_session_factory
from app.data.models import JobRun, JobRunStatus

logger = logging.getLogger(__name__)

# A `running` row older than this is assumed to be a crashed run, not a
# genuinely in-flight one — otherwise a single hard kill mid-run would wedge
# every future nightly run forever. A real nightly pass on James's ~2200-book
# library is minutes, not hours.
STALE_RUN_AFTER = timedelta(hours=3)


async def start_run(kind: str, trigger: str) -> int:
    async with async_session_factory() as session:
        row = JobRun(kind=kind, trigger=trigger, status=JobRunStatus.running)
        session.add(row)
        await session.commit()
        return row.id


async def finish_run(
    run_id: int,
    *,
    status: JobRunStatus,
    summary: str | None = None,
    error: str | None = None,
) -> None:
    async with async_session_factory() as session:
        row = await session.get(JobRun, run_id)
        if row is None:  # pragma: no cover - defensive
            logger.warning("finish_run: job_run %s vanished", run_id)
            return
        row.status = status
        row.summary = summary
        row.error = error
        row.finished_at = datetime.now(UTC)
        await session.commit()


async def record_skipped(kind: str, trigger: str, reason: str) -> None:
    """A run that never started because another was active still deserves a
    line in the trail — otherwise the Dashboard just silently shows the older
    run and James can't tell the scheduler fired at all."""
    async with async_session_factory() as session:
        session.add(
            JobRun(
                kind=kind,
                trigger=trigger,
                status=JobRunStatus.success,
                summary=f"skipped — {reason}",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def has_active_run(kind: str) -> bool:
    async with async_session_factory() as session:
        return await _has_active_run(session, kind)


async def _has_active_run(session: AsyncSession, kind: str) -> bool:
    cutoff = datetime.now(UTC) - STALE_RUN_AFTER
    rows = (
        await session.execute(
            select(JobRun).where(JobRun.kind == kind, JobRun.status == JobRunStatus.running)
        )
    ).scalars().all()
    for row in rows:
        started = row.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started is None or started >= cutoff:
            return True
    return False


async def get_last_run(kind: str) -> JobRun | None:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(JobRun)
                .where(JobRun.kind == kind)
                .order_by(JobRun.started_at.desc(), JobRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
