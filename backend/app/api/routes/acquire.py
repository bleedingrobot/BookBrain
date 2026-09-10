import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
    AcquireSuggestions,
    ApproveRequestBody,
    OpenBooksServerStatus,
    OpenRequest,
    RequestRefreshJob,
)
from app.services import (
    acquire_service,
    acquisition_service,
    openbooks_process_service,
    openbooks_service,
)
from app.services.drive_service import DriveService
from app.services.openbooks_process_service import OpenBooksProcessError
from app.services.openbooks_service import (
    OpenBooksError,
    OpenBooksRateLimited,
    OpenBooksUnavailable,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/acquire", tags=["acquire"])


def _openbooks_http_error(exc: Exception) -> HTTPException:
    """Map an OpenBooks failure to an HTTP status. A catch-all so a stray
    exception in the WS client is a clean 502, never a 500."""
    if isinstance(exc, OpenBooksRateLimited):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, OpenBooksUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, OpenBooksError):
        return HTTPException(status_code=502, detail=str(exc))
    logger.exception("acquire: unexpected error talking to OpenBooks")
    return HTTPException(status_code=502, detail="OpenBooks request failed unexpectedly — see server logs")


async def _require_folders(db: AsyncSession) -> tuple[str, str]:
    """(inbox_folder_id, library_folder_id) or a 400."""
    repo = SettingsRepository(db)
    inbox = await DriveService.get_inbox_folder_config(repo)
    library = await DriveService.get_library_folder_config(repo)
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    return inbox.folder_id, library.folder_id


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
    except Exception as exc:
        raise _openbooks_http_error(exc) from exc
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
    except Exception as exc:
        raise _openbooks_http_error(exc) from exc
    return AcquireDownloadResponse(**result)


# --------------------------------------------------------------------------
# Fill open viewer requests (bookbrain-wishlist.json items still "wanted")
# --------------------------------------------------------------------------


@router.get("/suggestions", response_model=AcquireSuggestions)
async def list_suggestions(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> AcquireSuggestions:
    _require_enabled()
    repo = SettingsRepository(db)
    library = await DriveService.get_library_folder_config(repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    data = await acquisition_service.list_suggestions(provider, library.folder_id)
    return AcquireSuggestions(**data)


@router.get("/requests", response_model=list[OpenRequest])
async def list_requests(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> list[OpenRequest]:
    _require_enabled()
    _, library_folder_id = await _require_folders(db)
    views = await acquisition_service.list_requests(provider, library_folder_id)
    return [OpenRequest(**vars(v)) for v in views]


@router.post("/requests/refresh", response_model=RequestRefreshJob)
async def refresh_requests(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> RequestRefreshJob:
    _require_enabled()
    _, library_folder_id = await _require_folders(db)
    job = acquisition_service.new_refresh_job()
    background_tasks.add_task(
        acquisition_service.run_refresh_job, job.job_id, provider, library_folder_id
    )
    return RequestRefreshJob(**vars(job))


@router.get("/requests/refresh/{job_id}", response_model=RequestRefreshJob)
async def refresh_status(job_id: str) -> RequestRefreshJob:
    job = acquisition_service.get_refresh_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return RequestRefreshJob(**vars(job))


@router.post("/requests/{request_id}/approve", response_model=AcquireDownloadResponse)
async def approve_request(
    request_id: str,
    body: ApproveRequestBody,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> AcquireDownloadResponse:
    _require_enabled()
    inbox_folder_id, library_folder_id = await _require_folders(db)
    try:
        result = await acquisition_service.approve_request(
            request_id, body.full, provider, inbox_folder_id, library_folder_id
        )
    except Exception as exc:
        raise _openbooks_http_error(exc) from exc
    return AcquireDownloadResponse(**result)


@router.post("/requests/{request_id}/skip", status_code=204)
async def skip_request(request_id: str) -> None:
    _require_enabled()
    try:
        await acquisition_service.skip_request(request_id)
    except OpenBooksError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/requests/{request_id}/reset", status_code=204)
async def reset_request(request_id: str) -> None:
    _require_enabled()
    await acquisition_service.reset_request(request_id)
