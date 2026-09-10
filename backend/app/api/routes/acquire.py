import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.schemas.acquire import (
    AcquireBook,
    AcquireDownloadRequest,
    AcquireDownloadResponse,
    AcquireSearchRequest,
    AcquireSearchResponse,
    AcquireStatus,
    OpenBooksServerStatus,
)
from app.services import acquire_service, openbooks_process_service, openbooks_service
from app.services.drive_service import DriveService
from app.services.openbooks_process_service import OpenBooksProcessError
from app.services.openbooks_service import (
    OpenBooksError,
    OpenBooksRateLimited,
    OpenBooksUnavailable,
)

router = APIRouter(prefix="/acquire", tags=["acquire"])


def _require_enabled() -> None:
    if not openbooks_service.is_enabled():
        raise HTTPException(
            status_code=400,
            detail="OpenBooks integration is disabled (set OPENBOOKS_ENABLED=true and run tools/run-openbooks.ps1)",
        )


@router.get("/status", response_model=AcquireStatus)
async def get_status() -> AcquireStatus:
    return AcquireStatus(enabled=openbooks_service.is_enabled())


@router.get("/server", response_model=OpenBooksServerStatus)
async def server_status() -> OpenBooksServerStatus:
    _require_enabled()
    return OpenBooksServerStatus(**await asyncio.to_thread(openbooks_process_service.status))


@router.post("/server/start", response_model=OpenBooksServerStatus)
async def server_start() -> OpenBooksServerStatus:
    _require_enabled()
    try:
        state = await asyncio.to_thread(openbooks_process_service.start)
    except OpenBooksProcessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OpenBooksServerStatus(**state)


@router.post("/server/stop", response_model=OpenBooksServerStatus)
async def server_stop() -> OpenBooksServerStatus:
    _require_enabled()
    try:
        state = await asyncio.to_thread(openbooks_process_service.stop)
    except OpenBooksProcessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OpenBooksServerStatus(**state)


@router.post("/search", response_model=AcquireSearchResponse)
async def search(body: AcquireSearchRequest) -> AcquireSearchResponse:
    _require_enabled()
    try:
        outcome = await openbooks_service.search(body.query)
    except OpenBooksRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except OpenBooksUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OpenBooksError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AcquireSearchResponse(
        results=[AcquireBook(**vars(b)) for b in outcome.results],
        parse_errors=outcome.parse_errors,
        message=outcome.message,
    )


@router.post("/download", response_model=AcquireDownloadResponse)
async def download(
    body: AcquireDownloadRequest,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> AcquireDownloadResponse:
    _require_enabled()
    inbox = await DriveService.get_inbox_folder_config(SettingsRepository(db))
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")
    try:
        result = await acquire_service.acquire_to_inbox(
            body.full, body.filename, provider, inbox.folder_id
        )
    except OpenBooksRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except OpenBooksUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OpenBooksError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AcquireDownloadResponse(**result)
