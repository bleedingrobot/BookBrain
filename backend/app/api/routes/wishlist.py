from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.schemas.wishlist import (
    ResolveRequest,
    ResolveResult,
    WishlistItemCreate,
    WishlistItemOut,
    WishlistItemUpdate,
)
from app.services import wishlist_service

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.post("/resolve", response_model=ResolveResult)
async def resolve(body: ResolveRequest, db: AsyncSession = Depends(get_db)) -> ResolveResult:
    return await wishlist_service.resolve_request(db, body.text)


@router.get("", response_model=list[WishlistItemOut])
async def list_wishlist(db: AsyncSession = Depends(get_db)) -> list[WishlistItemOut]:
    return await wishlist_service.list_items(db)


@router.post("", response_model=WishlistItemOut, status_code=201)
async def add(body: WishlistItemCreate, db: AsyncSession = Depends(get_db)) -> WishlistItemOut:
    return await wishlist_service.add_item(db, body)


@router.patch("/{item_id}", response_model=WishlistItemOut)
async def update(
    item_id: int, body: WishlistItemUpdate, db: AsyncSession = Depends(get_db)
) -> WishlistItemOut:
    try:
        return await wishlist_service.set_status(db, item_id, body.status)
    except wishlist_service.WishlistItemNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid status: {exc}") from exc


@router.delete("/{item_id}", status_code=204)
async def delete(item_id: int, db: AsyncSession = Depends(get_db)) -> None:
    try:
        await wishlist_service.delete_item(db, item_id)
    except wishlist_service.WishlistItemNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
