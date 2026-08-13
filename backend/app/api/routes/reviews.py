from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_drive_provider
from app.data.db import get_db
from app.data.models import ReviewStatus
from app.providers.drive.provider import DriveProvider
from app.schemas.reviews import CorrectReviewRequest, ReviewDetail, ReviewSummary
from app.services import review_service
from app.services.review_service import ReviewAlreadyResolvedError, ReviewNotFoundError

router = APIRouter(prefix="/reviews", tags=["reviews"])

_VALID_STATUSES = {s.value for s in ReviewStatus}


@router.get("", response_model=list[ReviewSummary])
async def list_reviews(
    status: str = "pending", db: AsyncSession = Depends(get_db)
) -> list[ReviewSummary]:
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status {status!r}")
    return await review_service.list_reviews(db, ReviewStatus(status))


@router.get("/{review_id}", response_model=ReviewDetail)
async def get_review(review_id: int, db: AsyncSession = Depends(get_db)) -> ReviewDetail:
    try:
        return await review_service.get_review_detail(db, review_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{review_id}/approve", response_model=ReviewDetail)
async def approve_review(review_id: int, db: AsyncSession = Depends(get_db)) -> ReviewDetail:
    review = await _resolve(review_service.approve, db, review_id)
    return await review_service.get_review_detail(db, review.id)


@router.post("/{review_id}/correct", response_model=ReviewDetail)
async def correct_review(
    review_id: int, body: CorrectReviewRequest, db: AsyncSession = Depends(get_db)
) -> ReviewDetail:
    try:
        review = await review_service.correct(db, review_id, body)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await review_service.get_review_detail(db, review.id)


@router.post("/{review_id}/reject", response_model=ReviewDetail)
async def reject_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_drive_provider),
) -> ReviewDetail:
    try:
        review = await review_service.reject(db, review_id, provider)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await review_service.get_review_detail(db, review.id)


async def _resolve(action, db: AsyncSession, review_id: int):
    try:
        return await action(db, review_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewAlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
