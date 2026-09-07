"""align files.status / files.status_reason enums with the models

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-06 16:00:00.000000

models.FileStatus gained `rejected`; models.FileStatusReason gained
`previously_rejected` and `same_book`, with no migration — `alembic check`
was red.

On SQLite an `sa.Enum` emits no CHECK constraint (it's a bare VARCHAR), so
the new values already persist fine and there is *no data change* here. This
migration exists to (a) satisfy `alembic check` and (b) widen the SQLite
VARCHAR to fit `previously_rejected` (19 chars > the old VARCHAR(14)) so a
future length-enforcing DB (Postgres) doesn't truncate it. Do NOT add
`create_constraint=True` — that would put a hard CHECK on SQLite and turn
every future enum addition into a mandatory table rebuild.

`batch_alter_table` recreates `files` on SQLite; the test suite
(test_migrations, plus the full test_scan_service / test_duplicate_service
runs) verifies ix_files_sha256, ix_files_sha256_status and
ix_files_original_sha256 survive the recreate.

Same recipe is reused by the operations.action migration
(f0e1d2c3b4a5, series_merge).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_OLD = sa.Enum(
    "inbox", "organised", "review", "unidentified", "duplicate", name="filestatus"
)
_STATUS_NEW = sa.Enum(
    "inbox", "organised", "review", "unidentified", "duplicate", "rejected", name="filestatus"
)
_REASON_OLD = sa.Enum(
    "multi_parent", "no_parent", "manual_drift", "parse_failed", "low_confidence",
    name="filestatusreason",
)
_REASON_NEW = sa.Enum(
    "multi_parent", "no_parent", "manual_drift", "parse_failed", "low_confidence",
    "previously_rejected", "same_book",
    name="filestatusreason",
)


def upgrade() -> None:
    with op.batch_alter_table("files") as batch:
        batch.alter_column(
            "status", existing_type=_STATUS_OLD, type_=_STATUS_NEW, existing_nullable=False
        )
        batch.alter_column(
            "status_reason", existing_type=_REASON_OLD, type_=_REASON_NEW, existing_nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("files") as batch:
        batch.alter_column(
            "status", existing_type=_STATUS_NEW, type_=_STATUS_OLD, existing_nullable=False
        )
        batch.alter_column(
            "status_reason", existing_type=_REASON_NEW, type_=_REASON_OLD, existing_nullable=True
        )
