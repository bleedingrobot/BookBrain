from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.settings_keys import ORGANIZE_DRY_RUN, ORGANIZE_HOLD_HOURS
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.organize import OrganizeSettings
from app.schemas.system import SystemStatus
from app.services.organize_service import get_organize_dry_run, get_organize_hold_hours

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
    return OrganizeSettings(
        dry_run=await get_organize_dry_run(repo),
        hold_hours=await get_organize_hold_hours(repo),
    )


@router.put("/organize", response_model=OrganizeSettings)
async def update_organize_settings(
    body: OrganizeSettings, db: AsyncSession = Depends(get_db)
) -> OrganizeSettings:
    repo = SettingsRepository(db)
    await repo.set(ORGANIZE_DRY_RUN, "true" if body.dry_run else "false")
    # get_organize_hold_hours clamps on read; clamp on write too so the stored
    # value and what the UI shows back can't drift.
    await repo.set(ORGANIZE_HOLD_HOURS, str(max(0, min(body.hold_hours, 720))))
    return OrganizeSettings(
        dry_run=await get_organize_dry_run(repo),
        hold_hours=await get_organize_hold_hours(repo),
    )
