from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.schemas.covers import CoverJobStatus
from app.schemas.descriptions import DescriptionBackfillEstimate, DescriptionJobStatus
from app.schemas.library import LibraryExportResult, RebuildEstimate
from app.schemas.backup import BackupInfo, BackupResult
from app.schemas.metadata_writeback import MetadataWritebackJobStatus
from app.schemas.recently_organized import RecentlyOrganizedResponse
from app.schemas.scan import ScanJobStatus
from app.services import (
    backup_service,
    embedding_service,
    hardcover_new_releases_service,
    hardcover_recs_service,
    hardcover_series_service,
    library_service,
    recently_organized_service,
)
from app.services.auth_service import AuthService, get_auth_service
from app.services.cover_service import CoverService, get_cover_service
from app.services.description_service import (
    DescriptionService,
    estimate_description_backfill,
    get_description_service,
)
from app.services.drive_service import DriveService
from app.services.metadata_writeback_service import (
    MetadataWritebackService,
    get_metadata_writeback_service,
)
from app.services.library_index_service import (
    regenerate_embeddings,
    regenerate_library_index,
    regenerate_new_releases,
    regenerate_news,
    regenerate_reading,
    regenerate_recommendations,
)
from app.services.scan_service import ScanService, estimate_rebuild, get_scan_service

router = APIRouter(prefix="/library", tags=["library"])


@router.post("/clear", status_code=204)
async def clear_library(db: AsyncSession = Depends(get_db)) -> None:
    await library_service.clear_library(db)


@router.post("/rebuild", response_model=ScanJobStatus, status_code=202)
async def rebuild_library(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: ScanService = Depends(get_scan_service),
) -> ScanJobStatus:
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run_rebuild, job.job_id, creds, library.folder_id)
    return job


@router.get("/rebuild/estimate", response_model=RebuildEstimate)
async def rebuild_estimate(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> RebuildEstimate:
    """~N library-tree files a rebuild would re-identify + a rough $ figure.
    One Drive listing, zero AI calls; `estimated=false` if it can't list."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    library = await DriveService.get_library_folder_config(settings_repo)
    return await estimate_rebuild(creds, library.folder_id if library else None)


@router.get("/rebuild/{job_id}", response_model=ScanJobStatus)
async def get_rebuild_status(
    job_id: str, service: ScanService = Depends(get_scan_service)
) -> ScanJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="rebuild job not found")
    return status


@router.post("/index")
async def refresh_library_index(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """Regenerate bookbrain-index.json in the library folder now, instead of
    waiting for the next organize/rebuild to do it. Handy for the very first
    generation, or after correcting a book's metadata by hand."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    count = await regenerate_library_index(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="index refresh failed — see server logs")
    return {"books": count}


@router.post("/series-catalog/refresh")
async def refresh_series_catalog(
    limit: int = 300,
    stale_days: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """prompts/25 Phase 2 — top up each series' Hardcover canonical book list
    (bounded, so it's safe to click; the nightly job does this too). The next
    index refresh then carries it into bookbrain-index.json. No-op without
    HARDCOVER_API_TOKEN. `stale_days=0` forces a re-sync of everything (use
    after a schema change like Part A's `releaseDate`)."""
    kwargs = {"limit": max(1, min(limit, 1000))}
    if stale_days is not None:
        kwargs["stale_after_days"] = max(0, stale_days)
    return await hardcover_series_service.refresh_series_catalog(db, **kwargs)


@router.post("/book-recs/refresh")
async def refresh_book_recs(
    limit: int = 400,
    stale_days: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """prompts/25 Phase 3 — fetch/resolve each book's Hardcover "readers also
    liked" list (bounded). Then POST /library/recommendations to write the
    sidecar. No-op without HARDCOVER_API_TOKEN. `stale_days=0` forces a
    re-sync (rows lacking `meta` from before Part B are always re-picked
    regardless)."""
    kwargs = {"limit": max(1, min(limit, 2000))}
    if stale_days is not None:
        kwargs["stale_after_days"] = max(0, stale_days)
    return await hardcover_recs_service.refresh_book_recs(db, **kwargs)


@router.post("/new-releases/refresh")
async def refresh_new_releases(
    limit: int = 120,
    stale_days: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """prompts/27 Part 2 — one Hardcover call per author to top up
    `Author.hardcover_json` with their recent + near-future books (bounded, so
    it's safe to click; the nightly job does this too). The same call also
    resolves `Author.hardcover_person_id` (prompts/28). Then POST
    /library/new-releases to write the sidecar. No-op without
    HARDCOVER_API_TOKEN. `stale_days=0` forces a re-sync of every author."""
    kwargs: dict = {"limit": max(1, min(limit, 500))}
    if stale_days is not None:
        kwargs["stale_after_days"] = max(0, stale_days)
    return await hardcover_new_releases_service.refresh_new_releases(db, **kwargs)


@router.post("/new-releases")
async def refresh_new_releases_file(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """Write bookbrain-new-releases.json from what new-releases/refresh has
    resolved so far, minus everything already owned or on the wishlist — the
    viewer's "From authors you read" strip reads it."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    count = await regenerate_new_releases(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="new-releases refresh failed — see logs")
    return {"books": count}


@router.post("/news")
async def refresh_news_file(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """prompts/32 — fetch the curated SFF news feeds and write
    bookbrain-news.json (the viewer's "From around the SFF world" section).
    No API token needed; best-effort per feed."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    count = await regenerate_news(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="news refresh failed — see logs")
    return {"items": count}


@router.post("/reading")
async def refresh_reading_file(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """prompts/30 — fetch the owner's Hardcover reading data (Read / Want /
    rating / dates), match it to the library, and write bookbrain-reading.json.
    No-op without HARDCOVER_API_TOKEN. Read-only; the owner's own data."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    count = await regenerate_reading(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="reading refresh failed / no token — see logs")
    return {"books": count}


@router.post("/embeddings/refresh")
async def refresh_embeddings(limit: int = 500, db: AsyncSession = Depends(get_db)) -> dict:
    """prompts/29 — re-embed organised books whose blurb changed (or that were
    never embedded), capped at `limit`. CPU-only, no AI cost. Then POST
    /library/embeddings to write the sidecar. First run over the whole library
    is ~1–2 min; loop the call until `pending` is 0."""
    return await embedding_service.refresh_embeddings(db, limit=max(1, min(limit, 5000)))


@router.post("/embeddings")
async def refresh_embeddings_file(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """Write bookbrain-embeddings.bin from whatever embeddings/refresh has
    computed so far — the viewer's semantic search reads it."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    count = await regenerate_embeddings(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="embeddings sidecar refresh failed — see logs")
    return {"books": count}


@router.post("/recommendations")
async def refresh_recommendations_file(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    """Write bookbrain-recommendations.json from what book-recs/refresh has
    resolved so far — the viewer's "Readers also liked" strip reads it."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    count = await regenerate_recommendations(creds, library.folder_id)
    if count is None:
        raise HTTPException(status_code=500, detail="recommendations refresh failed — see logs")
    return {"books": count}


@router.post("/covers", response_model=CoverJobStatus, status_code=202)
async def start_cover_generation(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: CoverService = Depends(get_cover_service),
) -> CoverJobStatus:
    """Backfill cover thumbnails for the whole organised library into the
    Drive `covers/` folder. Resumable — re-running only fills the gaps."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")

    job = service.create_job()
    background_tasks.add_task(service.run, job.job_id, creds, library.folder_id)
    return job


@router.get("/covers/{job_id}", response_model=CoverJobStatus)
async def get_cover_status(
    job_id: str, service: CoverService = Depends(get_cover_service)
) -> CoverJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="cover job not found")
    return status


@router.post("/descriptions", response_model=DescriptionJobStatus, status_code=202)
async def start_description_backfill(
    background_tasks: BackgroundTasks,
    ai: bool = False,
    refresh_epub: bool = False,
    service: DescriptionService = Depends(get_description_service),
) -> DescriptionJobStatus:
    """Fill in `books.description` for organised books that have no blurb
    from the EPUB or a metadata provider. `ai=true` also writes a short
    model-generated blurb for whatever's still blank. `refresh_epub=true`
    (prompts/29) also revisits books that only have the EPUB's embedded
    `<dc:description>`, promoting a provider blurb over it when it's clearly
    better — no AI is spent on those. Free; wants a working backend Google
    Books key."""
    job = service.create_job()
    background_tasks.add_task(
        service.run, job.job_id, use_ai=ai, include_epub_only=refresh_epub
    )
    return job


@router.get("/descriptions/estimate", response_model=DescriptionBackfillEstimate)
async def description_backfill_estimate() -> DescriptionBackfillEstimate:
    """~N description-less books the AI backfill would process this run (an
    upper bound — the free provider pass may cover some) + a rough $ figure.
    Pure DB count, zero AI calls."""
    return await estimate_description_backfill()


@router.get("/descriptions/{job_id}", response_model=DescriptionJobStatus)
async def get_description_status(
    job_id: str, service: DescriptionService = Depends(get_description_service)
) -> DescriptionJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="description job not found")
    return status


@router.post("/embedded-metadata", response_model=MetadataWritebackJobStatus, status_code=202)
async def start_embedded_metadata_writeback(
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    service: MetadataWritebackService = Depends(get_metadata_writeback_service),
) -> MetadataWritebackJobStatus:
    """Write BookBrain's resolved title/author/series (and a cover, if the
    epub has none) into each organised `.epub`'s embedded OPF metadata — so
    Kobo and other readers stop showing the messy original. `dry_run=true`
    reports what would change without touching Drive or any file hash.
    Resumable — re-running only touches files whose metadata has changed."""
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    library = await DriveService.get_library_folder_config(settings_repo)
    job = service.create_job(dry_run=dry_run)
    background_tasks.add_task(
        service.run,
        job.job_id,
        creds,
        library.folder_id if library else None,
        dry_run=dry_run,
    )
    return job


@router.get("/embedded-metadata/{job_id}", response_model=MetadataWritebackJobStatus)
async def get_embedded_metadata_status(
    job_id: str, service: MetadataWritebackService = Depends(get_metadata_writeback_service)
) -> MetadataWritebackJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="metadata writeback job not found")
    return status


async def _backup_prereqs(db: AsyncSession, auth: AuthService):
    settings_repo = SettingsRepository(db)
    creds = await auth.get_credentials(settings_repo)
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    return creds, library.folder_id


@router.post("/backup", response_model=BackupResult)
async def create_backup(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> BackupResult:
    """Snapshot epub_librarian.db (gzipped + a portable SQL dump) into the
    library folder's backups/ subfolder now, keeping the last N. The nightly
    run does this automatically; this is the ad-hoc button."""
    creds, folder_id = await _backup_prereqs(db, auth)
    try:
        return await backup_service.create_backup(creds, folder_id)
    except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
        raise HTTPException(status_code=500, detail=f"backup failed: {exc}") from exc


@router.get("/backups", response_model=list[BackupInfo])
async def list_backups(
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> list[BackupInfo]:
    creds, folder_id = await _backup_prereqs(db, auth)
    return await backup_service.list_backups(creds, folder_id)


@router.get("/recently-organized", response_model=RecentlyOrganizedResponse)
async def get_recently_organized(
    since: str = "48h", db: AsyncSession = Depends(get_db)
) -> RecentlyOrganizedResponse:
    """prompts/15 Stage I — every file auto-organized in the window (newest
    first) with the confidence it moved at, a one-line evidence summary, its
    current status, and whether a human has confirmed it. When
    `settings.organize_hold_hours > 0`, also the files currently held back
    from the organize pass. `since` accepts `24h` / `48h` / `7d`; clamped to
    30 days. No AI calls."""
    since_hours = recently_organized_service.parse_since(since)
    return await recently_organized_service.recently_organized(db, since_hours=since_hours)


@router.post("/export", response_model=LibraryExportResult)
async def export_library(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> LibraryExportResult:
    settings_repo = SettingsRepository(db)
    library = await DriveService.get_library_folder_config(settings_repo)
    parent_id = library.folder_id if library else None
    return await library_service.export_to_sheet(db, provider, parent_id=parent_id)
