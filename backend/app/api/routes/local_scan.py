import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.core.config import get_settings
from app.data.db import get_db
from app.data.models import LocalFile
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.local_scan import CopyResult, DismissResult, FileIdsRequest, LocalFileSummary
from app.services import local_scan_service
from app.services.auth_service import get_auth_service
from app.services.drive_service import DriveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/local-scan", tags=["local-scan"])


def _to_summary(row: LocalFile) -> LocalFileSummary:
    return LocalFileSummary(
        id=row.id,
        filename=row.filename,
        path=row.path,
        size_bytes=row.size_bytes,
        matched_title=row.matched_title,
        matched_author=row.matched_author,
        matched_score=row.matched_score,
    )


async def _try_match_against_wishlist(db: AsyncSession, rows: list[LocalFile]) -> None:
    """Best-effort — a plain scan works with no Drive connection at all
    (that's unchanged), so this never raises: no creds / no inbox+library
    folders configured yet just means no matching happens this pass."""
    if not rows:
        return
    try:
        repo = SettingsRepository(db)
        creds = await get_auth_service().get_credentials(repo)
        if creds is None:
            return
        inbox = await DriveService.get_inbox_folder_config(repo)
        library = await DriveService.get_library_folder_config(repo)
        if inbox is None or library is None:
            return
        provider = DriveProvider(build_drive_service(creds))
        await local_scan_service.match_against_wishlist(
            db, rows, provider, inbox.folder_id, library.folder_id
        )
    except Exception:
        logger.exception("local-scan: wishlist auto-match failed")


@router.post("", response_model=list[LocalFileSummary])
async def scan_local(db: AsyncSession = Depends(get_db)) -> list[LocalFileSummary]:
    settings = get_settings()
    rows = await local_scan_service.scan_local_folder(db, settings.torrents_watch_folder)
    await _try_match_against_wishlist(db, rows)
    return [_to_summary(r) for r in rows]


@router.get("/pending", response_model=list[LocalFileSummary])
async def get_pending(db: AsyncSession = Depends(get_db)) -> list[LocalFileSummary]:
    rows = await local_scan_service.list_pending(db)
    return [_to_summary(r) for r in rows]


@router.post("/copy", response_model=CopyResult)
async def copy_selected(
    body: FileIdsRequest,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> CopyResult:
    settings_repo = SettingsRepository(db)
    inbox = await DriveService.get_inbox_folder_config(settings_repo)
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")

    result = await local_scan_service.copy_to_drive(db, body.file_ids, provider, inbox.folder_id)
    return CopyResult(**result)


@router.post("/dismiss", response_model=DismissResult)
async def dismiss_selected(body: FileIdsRequest, db: AsyncSession = Depends(get_db)) -> DismissResult:
    count = await local_scan_service.dismiss(db, body.file_ids)
    return DismissResult(dismissed=count)
