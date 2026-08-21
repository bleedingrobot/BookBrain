from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup
from app.services.duplicate_service import clear_duplicates, list_duplicate_groups

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
