from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import File, FileStatus, Operation, OperationAction, OperationStatus
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.operations import OperationSummary


class OperationNotFoundError(Exception):
    pass


class OperationNotUndoableError(Exception):
    pass


async def list_operations(session: AsyncSession, limit: int = 200) -> list[OperationSummary]:
    result = await session.execute(
        select(Operation, File.filename)
        .join(File, Operation.file_id == File.id)
        .order_by(Operation.timestamp.desc())
        .limit(limit)
    )
    return [_to_summary(operation, filename) for operation, filename in result.all()]


async def get_operation_summary(session: AsyncSession, operation_id: int) -> OperationSummary:
    operation = await session.get(Operation, operation_id)
    if operation is None:
        raise OperationNotFoundError(f"operation {operation_id} not found")
    file_row = await session.get(File, operation.file_id)
    if file_row is None:
        raise OperationNotFoundError(f"file for operation {operation_id} not found")
    return _to_summary(operation, file_row.filename)


async def undo_operation(
    session: AsyncSession,
    creds: Credentials | None,
    operation_id: int,
    *,
    provider: DriveProvider | None = None,
) -> Operation:
    """Reverses a completed, real (non-dry-run) move/rename. A dry run never
    touched Drive, so there's nothing to undo — and an already-undone
    operation can't be undone again. A `write_metadata` op isn't a move at
    all (and BookBrain doesn't keep the pre-rewrite bytes), so it's never
    undoable here."""
    operation = await session.get(Operation, operation_id)
    if operation is None:
        raise OperationNotFoundError(f"operation {operation_id} not found")
    _UNDOABLE_ACTIONS = {
        OperationAction.move,
        OperationAction.rename,
        OperationAction.move_and_rename,
    }
    if (
        operation.dry_run
        or operation.status != OperationStatus.done
        or operation.action not in _UNDOABLE_ACTIONS
    ):
        raise OperationNotUndoableError(
            f"operation {operation_id} is not an undoable completed move"
        )

    file_row = await session.get(File, operation.file_id)
    if file_row is None:
        raise OperationNotFoundError(f"file for operation {operation_id} not found")

    provider = provider or DriveProvider(build_drive_service(creds))
    restored_name = operation.original_name or file_row.filename
    provider.move_and_rename(
        file_row.drive_file_id,
        old_parent_id=operation.new_parent_id,
        new_parent_id=operation.original_parent_id,
        new_name=restored_name,
    )

    file_row.filename = restored_name
    file_row.drive_parent_id = operation.original_parent_id
    file_row.status = FileStatus.inbox

    operation.status = OperationStatus.undone

    await session.commit()
    return operation


def _to_summary(operation: Operation, filename: str) -> OperationSummary:
    return OperationSummary(
        id=operation.id,
        timestamp=operation.timestamp.isoformat(),
        file_id=operation.file_id,
        filename=filename,
        action=operation.action.value,
        original_name=operation.original_name,
        original_parent_id=operation.original_parent_id,
        new_name=operation.new_name,
        new_parent_id=operation.new_parent_id,
        confidence=operation.confidence,
        model=operation.model,
        reason=operation.reason,
        status=operation.status.value,
        dry_run=operation.dry_run,
    )
