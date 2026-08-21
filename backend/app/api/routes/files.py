from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.models import FileStatus
from app.providers.drive.provider import DriveProvider
from app.schemas.files import FileSummary
from app.services import file_service

router = APIRouter(prefix="/files", tags=["files"])

_VALID_STATUSES = {s.value for s in FileStatus}


@router.get("", response_model=list[FileSummary])
async def list_files(
    status: str | None = None, db: AsyncSession = Depends(get_db)
) -> list[FileSummary]:
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status {status!r}")
    parsed_status = FileStatus(status) if status is not None else None
    return await file_service.list_files(db, parsed_status)


@router.post("/{file_id}/remove", status_code=204)
async def remove_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> None:
    try:
        await file_service.remove_file(db, file_id, provider)
    except file_service.FileRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
