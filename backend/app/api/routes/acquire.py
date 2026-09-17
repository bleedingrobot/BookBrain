import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.settings_keys import (
    LIBGEN_AUTOGET_ENABLED,
    OPENBOOKS_AUTOGET_ENABLED,
    TORRENT_AUTOGET_ENABLED,
)
from app.jobs.scheduler import (
    sync_autoget_schedule,
    sync_libgen_autoget_schedule,
    sync_torrent_local_scan_schedule,
    sync_torrent_poll_schedule,
    sync_torrent_submit_schedule,
)

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.exceptions import (
    AcquisitionError,
    AcquisitionRateLimited,
    AcquisitionUnavailable,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.acquire import (
    AcquireBook,
    AcquireDownloadRequest,
    AcquireDownloadResponse,
    AcquireProviderStatus,
    AcquireSearchRequest,
    AcquireSearchResponse,
    AcquireStatus,
    AcquireSuggestions,
    ApproveRequestBody,
    AutoGetSettings,
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
from app.services.acquisition_providers import default_acquisition_providers
from app.services.drive_service import DriveService
from app.services.openbooks_process_service import OpenBooksProcessError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/acquire", tags=["acquire"])


def _acquisition_http_error(exc: Exception) -> HTTPException:
    """Map an acquisition-provider failure to an HTTP status. A catch-all so
    a stray exception in a provider is a clean 502, never a 500."""
    if isinstance(exc, AcquisitionRateLimited):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, AcquisitionUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AcquisitionError):
        return HTTPException(status_code=502, detail=str(exc))
    logger.exception("acquire: unexpected error talking to an acquisition provider")
    return HTTPException(status_code=502, detail="acquisition request failed unexpectedly — see server logs")


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


def _require_enabled() -> list[AcquisitionProvider]:
    providers = default_acquisition_providers()
    if not providers:
        raise HTTPException(
            status_code=400,
            detail=(
                "no acquisition source is enabled (set OPENBOOKS_ENABLED=true and run "
                "tools/run-openbooks.ps1, and/or ANNAS_ARCHIVE_ENABLED=true)"
            ),
        )
    return providers


def _require_openbooks_enabled() -> None:
    if not openbooks_service.is_enabled():
        raise HTTPException(
            status_code=400,
            detail="OpenBooks integration is disabled (set OPENBOOKS_ENABLED=true and run tools/run-openbooks.ps1)",
        )


def _require_torrent_enabled() -> None:
    if not get_settings().torrent_enabled:
        raise HTTPException(
            status_code=400,
            detail="Torrent acquisition is disabled (set TORRENT_ENABLED=true and configure LIBRARR_URL)",
        )


def _require_libgen_enabled() -> None:
    if not get_settings().libgen_enabled:
        raise HTTPException(
            status_code=400,
            detail="Libgen acquisition is disabled (set LIBGEN_ENABLED=true)",
        )


async def _search_all(providers: list[AcquisitionProvider], query: str) -> AcquireSearchResponse:
    """Fan out one free-text query to every enabled provider — a route-level
    equivalent of acquisition_service._search_all_providers, which is shaped
    around a wishlist item dict rather than a raw query string."""

    async def _one(p: AcquisitionProvider):
        try:
            return await p.search(query), None
        except AcquisitionRateLimited as exc:
            await asyncio.sleep(exc.wait_seconds + 1)
            try:
                return await p.search(query), None
            except AcquisitionError as exc2:
                return [], exc2
        except AcquisitionError as exc:
            logger.warning("acquire: %s search failed for %r: %s", p.name, query, exc)
            return [], exc

    outcomes = await asyncio.gather(*(_one(p) for p in providers))
    results = [r for rs, _err in outcomes for r in rs]
    # A message only when every provider came back empty/erroring — otherwise
    # a partial failure is silent (the results that did come back speak for
    # themselves), matching the "degrade quietly" philosophy elsewhere.
    message = None
    if not results:
        errors = [str(err) for _rs, err in outcomes if err is not None]
        message = "; ".join(errors) if errors else "No results found"
    return AcquireSearchResponse(
        results=[AcquireBook(**vars(b)) for b in results],
        parse_errors=0,
        message=message,
    )


@router.get("/status", response_model=AcquireStatus)
async def get_status() -> AcquireStatus:
    providers = default_acquisition_providers()
    names = {p.name for p in providers}
    return AcquireStatus(
        enabled=bool(providers),
        providers=[
            AcquireProviderStatus(name="openbooks", enabled="openbooks" in names, requires_process=True),
            AcquireProviderStatus(
                name="annas_archive", enabled="annas_archive" in names, requires_process=False
            ),
            AcquireProviderStatus(name="libgen", enabled="libgen" in names, requires_process=False),
            # Not an AcquisitionProvider (see torrent_service.py's module
            # docstring for why) — Docker/systemd manages Librarr/qBittorrent/
            # Prowlarr's lifecycle, not BookBrain, unlike OpenBooks.
            AcquireProviderStatus(
                name="torrent", enabled=get_settings().torrent_enabled, requires_process=False
            ),
        ],
    )


@router.get("/server", response_model=OpenBooksServerStatus)
async def server_status() -> OpenBooksServerStatus:
    _require_openbooks_enabled()
    return OpenBooksServerStatus(**await asyncio.to_thread(openbooks_process_service.status))


@router.post("/server/start", response_model=OpenBooksServerStatus)
async def server_start() -> OpenBooksServerStatus:
    _require_openbooks_enabled()
    try:
        state = await asyncio.to_thread(openbooks_process_service.start)
    except OpenBooksProcessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OpenBooksServerStatus(**state)


@router.post("/server/stop", response_model=OpenBooksServerStatus)
async def server_stop() -> OpenBooksServerStatus:
    _require_openbooks_enabled()
    try:
        state = await asyncio.to_thread(openbooks_process_service.stop)
    except OpenBooksProcessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OpenBooksServerStatus(**state)


@router.post("/search", response_model=AcquireSearchResponse)
async def search(body: AcquireSearchRequest) -> AcquireSearchResponse:
    providers = _require_enabled()
    return await _search_all(providers, body.query)


@router.post("/download", response_model=AcquireDownloadResponse)
async def download(
    body: AcquireDownloadRequest,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> AcquireDownloadResponse:
    providers = {p.name: p for p in _require_enabled()}
    acquisition_provider = providers.get(body.provider or "openbooks")
    if acquisition_provider is None:
        raise HTTPException(status_code=400, detail=f"'{body.provider}' isn't an enabled acquisition source")
    inbox = await DriveService.get_inbox_folder_config(SettingsRepository(db))
    if inbox is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")
    try:
        result = await acquire_service.acquire_to_inbox(
            acquisition_provider, body.full, body.filename, provider, inbox.folder_id
        )
    except Exception as exc:
        raise _acquisition_http_error(exc) from exc
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
    limit: int = 25,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> RequestRefreshJob:
    """Search every enabled acquisition source for up to `limit` un-searched
    targets (wishlist + Hardcover want-to-read + list candidates), ~11s
    apiece. Click again / let the nightly run to work through the rest."""
    _require_enabled()
    _, library_folder_id = await _require_folders(db)
    job = acquisition_service.new_refresh_job()
    background_tasks.add_task(
        acquisition_service.run_refresh_job,
        job.job_id,
        provider,
        library_folder_id,
        limit=max(1, min(limit, 100)),
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
        raise _acquisition_http_error(exc) from exc
    return AcquireDownloadResponse(**result)


@router.post("/requests/{request_id}/skip", status_code=204)
async def skip_request(request_id: str) -> None:
    _require_enabled()
    try:
        await acquisition_service.skip_request(request_id)
    except AcquisitionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/requests/{request_id}/reset", status_code=204)
async def reset_request(request_id: str) -> None:
    _require_enabled()
    await acquisition_service.reset_request(request_id)


@router.get("/autoget", response_model=AutoGetSettings)
async def get_autoget(db: AsyncSession = Depends(get_db)) -> AutoGetSettings:
    _require_openbooks_enabled()
    v = await SettingsRepository(db).get(OPENBOOKS_AUTOGET_ENABLED)
    return AutoGetSettings(enabled=v == "true")


@router.put("/autoget", response_model=AutoGetSettings)
async def set_autoget(
    body: AutoGetSettings, request: Request, db: AsyncSession = Depends(get_db)
) -> AutoGetSettings:
    """When on, a background job downloads one confident 'Books to get'
    candidate per minute while nothing else is running. OpenBooks-only — see
    acquisition_service.autoget_tick's own gating. Libgen and torrent have
    their own independent switches (/libgen-autoget, /torrent-autoget)."""
    _require_openbooks_enabled()
    await SettingsRepository(db).set(OPENBOOKS_AUTOGET_ENABLED, "true" if body.enabled else "false")
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await sync_autoget_schedule(scheduler)
    return AutoGetSettings(enabled=body.enabled)


@router.get("/libgen-autoget", response_model=AutoGetSettings)
async def get_libgen_autoget(db: AsyncSession = Depends(get_db)) -> AutoGetSettings:
    _require_libgen_enabled()
    v = await SettingsRepository(db).get(LIBGEN_AUTOGET_ENABLED)
    return AutoGetSettings(enabled=v == "true")


@router.put("/libgen-autoget", response_model=AutoGetSettings)
async def set_libgen_autoget(
    body: AutoGetSettings, request: Request, db: AsyncSession = Depends(get_db)
) -> AutoGetSettings:
    """Separate switch from /autoget (OpenBooks) — Libgen is a plain HTTP
    scraper with its own rate limits, not the shared #ebook IRC bots, so it's
    pausable independently."""
    _require_libgen_enabled()
    await SettingsRepository(db).set(LIBGEN_AUTOGET_ENABLED, "true" if body.enabled else "false")
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await sync_libgen_autoget_schedule(scheduler)
    return AutoGetSettings(enabled=body.enabled)


@router.get("/torrent-autoget", response_model=AutoGetSettings)
async def get_torrent_autoget(db: AsyncSession = Depends(get_db)) -> AutoGetSettings:
    _require_torrent_enabled()
    v = await SettingsRepository(db).get(TORRENT_AUTOGET_ENABLED)
    return AutoGetSettings(enabled=v == "true")


@router.put("/torrent-autoget", response_model=AutoGetSettings)
async def set_torrent_autoget(
    body: AutoGetSettings, request: Request, db: AsyncSession = Depends(get_db)
) -> AutoGetSettings:
    """Separate switch from /autoget (OpenBooks/Libgen) by design — a
    torrent commits real bandwidth/disk per book, unlike a quick search, so
    it's pausable independently. Only starting new submissions; the poll and
    local-scan handoff legs run regardless (see torrent_service.py)."""
    _require_torrent_enabled()
    await SettingsRepository(db).set(TORRENT_AUTOGET_ENABLED, "true" if body.enabled else "false")
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        await sync_torrent_submit_schedule(scheduler)
        await sync_torrent_poll_schedule(scheduler)
        await sync_torrent_local_scan_schedule(scheduler)
    return AutoGetSettings(enabled=body.enabled)
