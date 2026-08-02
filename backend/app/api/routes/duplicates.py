from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.schemas.duplicates import DuplicateGroup
from app.services.duplicate_service import list_duplicate_groups

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


@router.get("", response_model=list[DuplicateGroup])
async def get_duplicates(db: AsyncSession = Depends(get_db)) -> list[DuplicateGroup]:
    return await list_duplicate_groups(db)
