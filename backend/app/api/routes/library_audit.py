from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.data.models import AuditClusterKind
from app.schemas.library_audit import DismissClusterRequest, DismissedClusterInfo, LibraryAuditResult
from app.schemas.reident_audit import (
    DeepCheckEstimate,
    DeepCheckRequest,
    DeepCheckResult,
    ReidentDismissedInfo,
    ReidentDismissRequest,
    ReidentRebuildJobStatus,
    ReidentReport,
)
from app.schemas.series_merge import (
    SeriesMergeApplyRequest,
    SeriesMergeProposal,
    SeriesMergeRequest,
    SeriesMergeResult,
)
from app.services import reident_audit_service
from app.services.drive_service import DriveService
from app.services.library_audit_service import (
    audit_library,
    dismiss_cluster,
    list_dismissed_clusters,
    undismiss_cluster,
)
from app.services.reident_audit_service import (
    ReidentAuditService,
    get_reident_audit_service,
)
from app.services.series_merge_service import (
    SeriesMergeValidationError,
    apply_series_merge,
    propose_series_merge,
)

router = APIRouter(prefix="/library-audit", tags=["library-audit"])


@router.get("", response_model=LibraryAuditResult)
async def get_library_audit(db: AsyncSession = Depends(get_db)) -> LibraryAuditResult:
    return await audit_library(db)


@router.post("/dismiss", status_code=204)
async def dismiss_audit_cluster(
    body: DismissClusterRequest, db: AsyncSession = Depends(get_db)
) -> None:
    await dismiss_cluster(db, AuditClusterKind(body.kind), body.member_ids)


@router.get("/dismissed", response_model=list[DismissedClusterInfo])
async def get_dismissed_clusters(db: AsyncSession = Depends(get_db)) -> list[DismissedClusterInfo]:
    return await list_dismissed_clusters(db)


@router.post("/dismissed/{dismissed_id}/restore", status_code=204)
async def restore_dismissed_cluster(dismissed_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await undismiss_cluster(db, dismissed_id)


@router.post("/series/investigate", response_model=SeriesMergeProposal)
async def investigate_series_cluster(
    body: SeriesMergeRequest, db: AsyncSession = Depends(get_db)
) -> SeriesMergeProposal:
    try:
        return await propose_series_merge(db, body.series_ids)
    except SeriesMergeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reident", response_model=ReidentReport)
async def get_reident_report(db: AsyncSession = Depends(get_db)) -> ReidentReport:
    """The cached Bulk Re-identify Audit report, with dismissed books removed.
    `generated_at` is null until the first rebuild."""
    return await reident_audit_service.get_report_filtered(db)


@router.post("/reident/rebuild", response_model=ReidentRebuildJobStatus, status_code=202)
async def rebuild_reident_report(
    background_tasks: BackgroundTasks,
    service: ReidentAuditService = Depends(get_reident_audit_service),
) -> ReidentRebuildJobStatus:
    """Regenerate the report. The free pass — provider lookups + cached
    ai_decisions only, zero API credits. Tracked as an in-memory job."""
    if service.has_running_job():
        raise HTTPException(status_code=409, detail="a re-identification rebuild is already running")
    job = service.create_job()
    background_tasks.add_task(service.run, job.job_id)
    return job


@router.get("/reident/rebuild/{job_id}", response_model=ReidentRebuildJobStatus)
async def get_reident_rebuild_status(
    job_id: str, service: ReidentAuditService = Depends(get_reident_audit_service)
) -> ReidentRebuildJobStatus:
    status = service.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="reident rebuild job not found")
    return status


@router.post("/reident/deep-check/estimate", response_model=DeepCheckEstimate)
async def estimate_reident_deep_check(
    body: DeepCheckRequest, db: AsyncSession = Depends(get_db)
) -> DeepCheckEstimate:
    return await reident_audit_service.estimate_deep_check(db, body.book_ids)


@router.post("/reident/deep-check", response_model=DeepCheckResult)
async def run_reident_deep_check(
    body: DeepCheckRequest, db: AsyncSession = Depends(get_db)
) -> DeepCheckResult:
    """Opt-in, capped AI re-check of already-flagged rows. Costs API credits —
    the frontend shows the estimate first."""
    return await reident_audit_service.run_deep_check(db, body.book_ids)


@router.post("/reident/dismiss", status_code=204)
async def dismiss_reident_flag(
    body: ReidentDismissRequest, db: AsyncSession = Depends(get_db)
) -> None:
    await reident_audit_service.dismiss_book(db, body.book_id)


@router.get("/reident/dismissed", response_model=list[ReidentDismissedInfo])
async def list_reident_dismissed(db: AsyncSession = Depends(get_db)) -> list[ReidentDismissedInfo]:
    return await reident_audit_service.list_dismissed(db)


@router.post("/reident/dismissed/{book_id}/restore", status_code=204)
async def restore_reident_flag(book_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await reident_audit_service.undismiss_book(db, book_id)


@router.post("/series/apply", response_model=SeriesMergeResult)
async def apply_series_cluster_fix(
    body: SeriesMergeApplyRequest,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> SeriesMergeResult:
    settings_repo = SettingsRepository(db)
    library = await DriveService.get_library_folder_config(settings_repo)
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    try:
        return await apply_series_merge(
            db,
            provider,
            library.folder_id,
            series_ids=body.series_ids,
            canonical_series_name=body.canonical_series_name,
            confirm_same_series=body.confirm_same_series,
            excluded_series_names=body.excluded_series_names,
        )
    except SeriesMergeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
