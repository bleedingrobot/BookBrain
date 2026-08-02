import pytest
from sqlalchemy import select

from app.data.models import File, FileStatus, Operation, OperationAction, OperationStatus
from app.services import operation_service
from app.services.operation_service import OperationNotFoundError, OperationNotUndoableError


class _FakeProvider:
    def __init__(self) -> None:
        self.move_calls: list[dict] = []

    def move_and_rename(self, file_id, *, old_parent_id, new_parent_id, new_name) -> dict:
        self.move_calls.append(
            {
                "file_id": file_id,
                "old_parent_id": old_parent_id,
                "new_parent_id": new_parent_id,
                "new_name": new_name,
            }
        )
        return {"id": file_id, "name": new_name, "parents": [new_parent_id]}


async def _seed_organized_file(db_session, *, dry_run: bool = False, status=OperationStatus.done) -> Operation:
    file_row = File(
        drive_file_id="drive-1",
        drive_parent_id="library-folder",
        filename="Dune.epub",
        sha256="abc123",
        size_bytes=100,
        status=FileStatus.organised,
    )
    db_session.add(file_row)
    await db_session.flush()

    operation = Operation(
        file_id=file_row.id,
        action=OperationAction.move_and_rename,
        original_name="dune.epub",
        original_parent_id="inbox-folder",
        new_name="Dune.epub",
        new_parent_id="library-folder",
        confidence=95,
        model="deterministic",
        status=status,
        dry_run=dry_run,
    )
    db_session.add(operation)
    await db_session.commit()
    return operation


async def test_list_operations_includes_filename(db_session) -> None:
    await _seed_organized_file(db_session)

    ops = await operation_service.list_operations(db_session)

    assert len(ops) == 1
    assert ops[0].filename == "Dune.epub"
    assert ops[0].status == "done"
    assert ops[0].dry_run is False


async def test_undo_reverses_move_and_rename(db_session) -> None:
    operation = await _seed_organized_file(db_session)
    provider = _FakeProvider()

    await operation_service.undo_operation(db_session, None, operation.id, provider=provider)

    assert len(provider.move_calls) == 1
    move = provider.move_calls[0]
    assert move["old_parent_id"] == "library-folder"
    assert move["new_parent_id"] == "inbox-folder"
    assert move["new_name"] == "dune.epub"

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.filename == "dune.epub"
    assert file_row.drive_parent_id == "inbox-folder"
    assert file_row.status.value == "inbox"

    updated_op = (await db_session.execute(select(Operation))).scalar_one()
    assert updated_op.status.value == "undone"


async def test_undo_rejects_dry_run_operation(db_session) -> None:
    operation = await _seed_organized_file(db_session, dry_run=True)
    provider = _FakeProvider()

    with pytest.raises(OperationNotUndoableError):
        await operation_service.undo_operation(db_session, None, operation.id, provider=provider)

    assert provider.move_calls == []


async def test_undo_rejects_already_undone_operation(db_session) -> None:
    operation = await _seed_organized_file(db_session, status=OperationStatus.undone)
    provider = _FakeProvider()

    with pytest.raises(OperationNotUndoableError):
        await operation_service.undo_operation(db_session, None, operation.id, provider=provider)


async def test_undo_raises_for_missing_operation(db_session) -> None:
    with pytest.raises(OperationNotFoundError):
        await operation_service.undo_operation(db_session, None, 999, provider=_FakeProvider())


async def test_get_operation_summary_raises_for_missing(db_session) -> None:
    with pytest.raises(OperationNotFoundError):
        await operation_service.get_operation_summary(db_session, 999)
