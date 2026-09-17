from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services import viewer_session_service
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


async def require_viewer_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> DriveProvider:
    """Gate for the sibling-facing /api/viewer/drive/* proxy routes: a valid
    passcode-session cookie (app/services/viewer_session_service.py), then
    this backend's own stored Drive credential — never the caller's, since a
    passcode login has no Google token of its own. Distinct from
    require_drive_provider above, which has no caller-auth check at all and
    is only reachable from the admin app's own (unexposed) origin; this one
    is the first dependency meant to be called from a public origin, so the
    cookie check is load-bearing."""
    token = request.cookies.get(viewer_session_service.COOKIE_NAME)
    if not viewer_session_service.verify_session_token(token):
        raise HTTPException(status_code=401, detail="not signed in")
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=503, detail="library not connected to Google Drive yet")
    return DriveProvider(build_drive_service(creds))
