from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.schemas.covers import CoverJobStatus
from app.schemas.library import LibraryExportResult
from app.schemas.scan import ScanJobStatus
from app.services import library_service
from app.services.auth_service import AuthService, get_auth_service
from app.services.cover_service import CoverService, get_cover_service
from app.services.drive_service import DriveService
from app.services.library_index_service import regenerate_library_index
from app.services.scan_service import ScanService, get_scan_service

router = APIRouter(prefix="/library", tags=["library"])


@router.post("/clear", status_code=204)
async def clear_library(db: AsyncSession = Depends(get_db)) -> None:
    await library_service.clear_library(db)


@router.post("/rebuild", response_model=ScanJobStatus, status_code=202)
async def rebuild_library(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: ScanService = Depends(get_scan_service),
) -> ScanJobStatus:
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run_rebuild, job.job_id, creds, library.folder_id)
    return job


@router.get("/rebuild/{job_id}", response_model=ScanJobStatus)
async def get_rebuild_status(
    job_id: str, service: ScanService = Depends(get_scan_service)
) -> ScanJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="rebuild job not found")
    return status


@router.post("/index")
async def refresh_library_index(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """Regenerate bookbrain-index.json in the library folder now, instead of
    waiting for the next organize/rebuild to do it. Handy for the very first
    generation, or after correcting a book's metadata by hand."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    count = await regenerate_library_index(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="index refresh failed — see server logs")
    return {"books": count}


@router.post("/covers", response_model=CoverJobStatus, status_code=202)
async def start_cover_generation(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: CoverService = Depends(get_cover_service),
) -> CoverJobStatus:
    """Backfill cover thumbnails for the whole organised library into the
    Drive `covers/` folder. Resumable — re-running only fills the gaps."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run, job.job_id, creds, library.folder_id)
    return job


@router.get("/covers/{job_id}", response_model=CoverJobStatus)
async def get_cover_status(
    job_id: str, service: CoverService = Depends(get_cover_service)
) -> CoverJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="cover job not found")
    return status


@router.post("/export", response_model=LibraryExportResult)
async def export_library(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> LibraryExportResult:
    settings_repo = SettingsRepository(db)
    library = await DriveService.get_library_folder_config(settings_repo)
    parent_id = library.folder_id if library else None
    return await library_service.export_to_sheet(db, provider, parent_id=parent_id)
