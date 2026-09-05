from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.data.models import AuditClusterKind
from app.schemas.library_audit import DismissClusterRequest, DismissedClusterInfo, LibraryAuditResult
from app.schemas.series_merge import (
    SeriesMergeApplyRequest,
    SeriesMergeProposal,
    SeriesMergeRequest,
    SeriesMergeResult,
)
from app.services.drive_service import DriveService
from app.services.library_audit_service import (
    audit_library,
    dismiss_cluster,
    list_dismissed_clusters,
    undismiss_cluster,
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
