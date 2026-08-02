from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.scan import ScanJobStatus
from app.services.auth_service import AuthService, get_auth_service
from app.services.drive_service import DriveService
from app.services.scan_service import ScanService, get_scan_service

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("", response_model=ScanJobStatus, status_code=202)
async def start_scan(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: ScanService = Depends(get_scan_service),
) -> ScanJobStatus:
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    inbox = await DriveService.get_inbox_folder_config(settings_repo)
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run_scan, job.job_id, creds, inbox.folder_id)
    return job


@router.get("/{job_id}", response_model=ScanJobStatus)
async def get_scan_status(
    job_id: str, service: ScanService = Depends(get_scan_service)
) -> ScanJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="scan job not found")
    return status
