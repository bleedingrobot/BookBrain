from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_keys import (
    BACKUP_RUN_ENABLED,
    BACKUP_RUN_HOUR,
    NIGHTLY_RUN_ENABLED,
    NIGHTLY_RUN_HOUR,
)
from app.data.db import get_db
from app.data.models import JobRun
from app.data.repositories.settings_repository import SettingsRepository
from app.jobs.scheduler import (
    read_backup_config,
    read_nightly_config,
    sync_backup_schedule,
    sync_nightly_schedule,
)
from app.schemas.jobs import BackupSettings, NightlyRunInfo, NightlySettings
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


async def _current_nightly() -> NightlySettings:
    enabled, hour = await read_nightly_config()
    last = await job_run_service.get_last_run("nightly")
    return NightlySettings(enabled=enabled, hour=hour, last_run=_run_info(last))


async def _current_backup() -> BackupSettings:
    enabled, hour = await read_backup_config()
    last = await job_run_service.get_last_run("backup")
    return BackupSettings(enabled=enabled, hour=hour, last_run=_run_info(last))


async def _resync(request: Request, sync) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await sync(scheduler)


@router.get("/nightly", response_model=NightlySettings)
async def get_nightly_settings() -> NightlySettings:
    return await _current_nightly()


@router.put("/nightly", response_model=NightlySettings)
async def update_nightly_settings(
    body: NightlySettings, request: Request, db: AsyncSession = Depends(get_db)
) -> NightlySettings:
    repo = SettingsRepository(db)
    await repo.set(NIGHTLY_RUN_ENABLED, "true" if body.enabled else "false")
    await repo.set(NIGHTLY_RUN_HOUR, str(body.hour))
    await _resync(request, sync_nightly_schedule)
    return await _current_nightly()


@router.get("/backup", response_model=BackupSettings)
async def get_backup_settings() -> BackupSettings:
    return await _current_backup()


@router.put("/backup", response_model=BackupSettings)
async def update_backup_settings(
    body: BackupSettings, request: Request, db: AsyncSession = Depends(get_db)
) -> BackupSettings:
    repo = SettingsRepository(db)
    await repo.set(BACKUP_RUN_ENABLED, "true" if body.enabled else "false")
    await repo.set(BACKUP_RUN_HOUR, str(body.hour))
    await _resync(request, sync_backup_schedule)
    return await _current_backup()
