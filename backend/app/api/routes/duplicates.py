from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup
from app.services.auth_service import AuthService, get_auth_service
from app.services.duplicate_service import clear_duplicates, list_duplicate_groups

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


@router.get("", response_model=list[DuplicateGroup])
async def get_duplicates(db: AsyncSession = Depends(get_db)) -> list[DuplicateGroup]:
    return await list_duplicate_groups(db)


@router.post("/clear", response_model=ClearDuplicatesResult)
async def clear_duplicates_route(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> ClearDuplicatesResult:
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    provider = DriveProvider(build_drive_service(creds))
    return await clear_duplicates(db, provider)
