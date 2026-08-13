from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.schemas.library_audit import LibraryAuditResult
from app.services.library_audit_service import audit_library

router = APIRouter(prefix="/library-audit", tags=["library-audit"])


@router.get("", response_model=LibraryAuditResult)
async def get_library_audit(db: AsyncSession = Depends(get_db)) -> LibraryAuditResult:
    return await audit_library(db)
