from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.organize import OrganizeJobStatus
from app.services.auth_service import AuthService, get_auth_service
from app.services.drive_service import DriveService
from app.services.organize_service import OrganizeService, get_organize_dry_run, get_organize_service

router = APIRouter(prefix="/organize", tags=["organize"])


@router.post("", response_model=OrganizeJobStatus, status_code=202)
async def start_organize(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: OrganizeService = Depends(get_organize_service),
) -> OrganizeJobStatus:
    settings_repo = SettingsRepository(db)
    dry_run = await get_organize_dry_run(settings_repo)

    if dry_run:
        job = service.create_job()
        background_tasks.add_task(service.run_organize, job.job_id, None, None, True)
        return job

    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run_organize, job.job_id, creds, library.folder_id, False)
    return job


@router.get("/{job_id}", response_model=OrganizeJobStatus)
async def get_organize_status(
    job_id: str, service: OrganizeService = Depends(get_organize_service)
) -> OrganizeJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="organize job not found")
    return status
