"""add series_merge to operations.action; reclassify existing merge moves

Revision ID: f0e1d2c3b4a5
Revises: b2c3d4e5f6a7
Create Date: 2026-09-06 17:00:00.000000

`series_merge` moves were logged as `move_and_rename`, which
operation_service.undo_operation treats as auto-undoable — but undoing one
lands the file in the folder the merge just deleted, with a stale
book.series. They must be non-undoable.

- Widen the `operationaction` enum to include `series_merge` (same
  batch_alter_table recipe as b2c3d4e5f6a7; no CHECK on SQLite, this records
  the addition for a future length-enforcing DB).
- Convert existing rows: a `move_and_rename` op whose `reason` starts with
  "series merge:" was written by series_merge_service.apply_series_merge
  (series_merge_service.py sets that prefix reliably).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f0e1d2c3b4a5'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTION_OLD = sa.Enum("move", "rename", "move_and_rename", "write_metadata", name="operationaction")
_ACTION_NEW = sa.Enum(
    "move", "rename", "move_and_rename", "write_metadata", "series_merge", name="operationaction"
)


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.alter_column(
            "action", existing_type=_ACTION_OLD, type_=_ACTION_NEW, existing_nullable=False
        )
    op.execute(
        "UPDATE operations SET action = 'series_merge' "
        "WHERE action = 'move_and_rename' AND reason LIKE 'series merge:%'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE operations SET action = 'move_and_rename' WHERE action = 'series_merge'"
    )
    with op.batch_alter_table("operations") as batch:
        batch.alter_column(
            "action", existing_type=_ACTION_NEW, type_=_ACTION_OLD, existing_nullable=False
        )
