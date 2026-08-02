from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.operations import OperationSummary
from app.services import operation_service
from app.services.auth_service import AuthService, get_auth_service
from app.services.operation_service import OperationNotFoundError, OperationNotUndoableError

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("", response_model=list[OperationSummary])
async def list_operations(limit: int = 200, db: AsyncSession = Depends(get_db)) -> list[OperationSummary]:
    return await operation_service.list_operations(db, limit=limit)


@router.post("/{operation_id}/undo", response_model=OperationSummary)
async def undo_operation(
    operation_id: int,
    db: AsyncSession = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> OperationSummary:
    creds = await auth.get_credentials(SettingsRepository(db))
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")

    try:
        await operation_service.undo_operation(db, creds, operation_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OperationNotUndoableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await operation_service.get_operation_summary(db, operation_id)
