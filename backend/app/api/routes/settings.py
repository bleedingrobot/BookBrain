from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.settings_keys import ORGANIZE_DRY_RUN
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.organize import OrganizeSettings
from app.schemas.system import SystemStatus

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/status", response_model=SystemStatus)
async def get_system_status() -> SystemStatus:
    settings = get_settings()
    return SystemStatus(
        anthropic_configured=bool(settings.anthropic_api_key),
        google_books_configured=bool(settings.google_books_api_key),
        confidence_auto_organize=settings.confidence_auto_organize,
        confidence_auto_flagged=settings.confidence_auto_flagged,
    )


@router.get("/organize", response_model=OrganizeSettings)
async def get_organize_settings(db: AsyncSession = Depends(get_db)) -> OrganizeSettings:
    repo = SettingsRepository(db)
    value = await repo.get(ORGANIZE_DRY_RUN)
    return OrganizeSettings(dry_run=value != "false")


@router.put("/organize", response_model=OrganizeSettings)
async def update_organize_settings(
    body: OrganizeSettings, db: AsyncSession = Depends(get_db)
) -> OrganizeSettings:
    repo = SettingsRepository(db)
    await repo.set(ORGANIZE_DRY_RUN, "true" if body.dry_run else "false")
    return body
