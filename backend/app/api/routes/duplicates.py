from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup
from app.services.duplicate_service import (
    DuplicateNotClearableError,
    clear_duplicates,
    clear_one_duplicate,
    list_duplicate_groups,
    unflag_duplicate,
)

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


@router.get("", response_model=list[DuplicateGroup])
async def get_duplicates(db: AsyncSession = Depends(get_db)) -> list[DuplicateGroup]:
    return await list_duplicate_groups(db)


@router.post("/clear", response_model=ClearDuplicatesResult)
async def clear_duplicates_route(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> ClearDuplicatesResult:
    return await clear_duplicates(db, provider)


@router.post("/{file_id}/clear", response_model=ClearDuplicatesResult)
async def clear_one_duplicate_route(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> ClearDuplicatesResult:
    try:
        return await clear_one_duplicate(db, provider, file_id)
    except DuplicateNotClearableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{file_id}/unflag", status_code=204)
async def unflag_duplicate_route(file_id: int, db: AsyncSession = Depends(get_db)) -> None:
    try:
        await unflag_duplicate(db, file_id)
    except DuplicateNotClearableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
