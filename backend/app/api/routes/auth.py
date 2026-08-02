from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.auth import AuthStartRequest, AuthStartResponse, AuthStatus
from app.services.auth_service import AuthService, get_auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/start", response_model=AuthStartResponse)
async def start_auth(
    body: AuthStartRequest, service: AuthService = Depends(get_auth_service)
) -> AuthStartResponse:
    try:
        url = service.start(body.folder_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AuthStartResponse(authorization_url=url)


@router.get("/callback")
async def auth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    settings_repo = SettingsRepository(db)
    try:
        await service.handle_callback(code, state, settings_repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"{get_settings().frontend_origin}/settings?connected=1")


@router.get("/status", response_model=AuthStatus)
async def auth_status(
    db: AsyncSession = Depends(get_db), service: AuthService = Depends(get_auth_service)
) -> AuthStatus:
    return AuthStatus(**await service.status(SettingsRepository(db)))


@router.post("/disconnect", status_code=204)
async def disconnect(
    db: AsyncSession = Depends(get_db), service: AuthService = Depends(get_auth_service)
) -> None:
    await service.disconnect(SettingsRepository(db))
