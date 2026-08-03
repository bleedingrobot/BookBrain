from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.db import get_db
from app.data.models import LocalFile
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.local_scan import CopyResult, DismissResult, FileIdsRequest, LocalFileSummary
from app.services import local_scan_service
from app.services.auth_service import AuthService, get_auth_service
from app.services.drive_service import DriveService

router = APIRouter(prefix="/local-scan", tags=["local-scan"])


def _to_summary(row: LocalFile) -> LocalFileSummary:
    return LocalFileSummary(id=row.id, filename=row.filename, path=row.path, size_bytes=row.size_bytes)


@router.post("", response_model=list[LocalFileSummary])
async def scan_local(db: AsyncSession = Depends(get_db)) -> list[LocalFileSummary]:
    settings = get_settings()
    rows = await local_scan_service.scan_local_folder(db, settings.torrents_watch_folder)
    return [_to_summary(r) for r in rows]


@router.get("/pending", response_model=list[LocalFileSummary])
async def get_pending(db: AsyncSession = Depends(get_db)) -> list[LocalFileSummary]:
    rows = await local_scan_service.list_pending(db)
    return [_to_summary(r) for r in rows]


@router.post("/copy", response_model=CopyResult)
async def copy_selected(
    body: FileIdsRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> CopyResult:
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    inbox = await DriveService.get_inbox_folder_config(settings_repo)
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")

    provider = DriveProvider(build_drive_service(creds))
    result = await local_scan_service.copy_to_drive(db, body.file_ids, provider, inbox.folder_id)
    return CopyResult(**result)


@router.post("/dismiss", response_model=DismissResult)
async def dismiss_selected(body: FileIdsRequest, db: AsyncSession = Depends(get_db)) -> DismissResult:
    count = await local_scan_service.dismiss(db, body.file_ids)
    return DismissResult(dismissed=count)
