from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services.auth_service import AuthService, get_auth_service


async def require_drive_provider(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> DriveProvider:
    """Shared dependency for routes that need a live Drive connection right
    now (as opposed to scan/organize, which only need credentials to hand
    off to a background job and build their own provider there). Without
    this, every such route re-implemented the same fetch-creds-or-401 block
    inline."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    return DriveProvider(build_drive_service(creds))
