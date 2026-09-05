from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_keys import NIGHTLY_RUN_ENABLED, NIGHTLY_RUN_HOUR
from app.data.db import get_db
from app.data.models import JobRun
from app.data.repositories.settings_repository import SettingsRepository
from app.jobs.scheduler import read_nightly_config, sync_nightly_schedule
from app.schemas.jobs import NightlyRunInfo, NightlySettings
from app.services import job_run_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _run_info(row: JobRun | None) -> NightlyRunInfo | None:
    if row is None:
        return None
    return NightlyRunInfo(
        status=row.status.value,
        trigger=row.trigger,
        started_at=row.started_at.isoformat() if row.started_at else "",
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        summary=row.summary,
        error=row.error,
    )


async def _current() -> NightlySettings:
    enabled, hour = await read_nightly_config()
    last = await job_run_service.get_last_run("nightly")
    return NightlySettings(enabled=enabled, hour=hour, last_run=_run_info(last))


@router.get("/nightly", response_model=NightlySettings)
async def get_nightly_settings() -> NightlySettings:
    return await _current()


@router.put("/nightly", response_model=NightlySettings)
async def update_nightly_settings(
    body: NightlySettings, request: Request, db: AsyncSession = Depends(get_db)
) -> NightlySettings:
    repo = SettingsRepository(db)
    await repo.set(NIGHTLY_RUN_ENABLED, "true" if body.enabled else "false")
    await repo.set(NIGHTLY_RUN_HOUR, str(body.hour))

    # Push the change into the live scheduler if it's running (it isn't in
    # tests, or when the API is imported without the lifespan).
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await sync_nightly_schedule(scheduler)

    return await _current()
