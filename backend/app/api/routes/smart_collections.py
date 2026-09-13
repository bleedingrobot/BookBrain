from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.schemas.smart_collections import (
    RulePreviewRequest,
    RulePreviewResult,
    SmartCollectionCreate,
    SmartCollectionOut,
    SmartCollectionUpdate,
)
from app.services import smart_collections_service
from app.services.collection_rules import CollectionRuleError

router = APIRouter(prefix="/smart-collections", tags=["smart-collections"])


@router.get("", response_model=list[SmartCollectionOut])
async def list_smart_collections(db: AsyncSession = Depends(get_db)) -> list[SmartCollectionOut]:
    return await smart_collections_service.list_collections(db)


@router.post("", response_model=SmartCollectionOut, status_code=201)
async def create_smart_collection(
    body: SmartCollectionCreate, db: AsyncSession = Depends(get_db)
) -> SmartCollectionOut:
    try:
        return await smart_collections_service.create(db, body)
    except CollectionRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except smart_collections_service.SmartCollectionNameTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{collection_id}", response_model=SmartCollectionOut)
async def update_smart_collection(
    collection_id: int, body: SmartCollectionUpdate, db: AsyncSession = Depends(get_db)
) -> SmartCollectionOut:
    try:
        return await smart_collections_service.update(db, collection_id, body)
    except smart_collections_service.SmartCollectionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CollectionRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except smart_collections_service.SmartCollectionNameTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{collection_id}", status_code=204)
async def delete_smart_collection(collection_id: int, db: AsyncSession = Depends(get_db)) -> None:
    try:
        await smart_collections_service.delete(db, collection_id)
    except smart_collections_service.SmartCollectionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/preview", response_model=RulePreviewResult)
async def preview_smart_collection(
    body: RulePreviewRequest, db: AsyncSession = Depends(get_db)
) -> RulePreviewResult:
    try:
        return await smart_collections_service.preview(db, body.rule)
    except CollectionRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
