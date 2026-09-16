"""add files.cover_phash

Revision ID: e2f3a4b5c6d7
Revises: f0e1d2c3b4a5
Create Date: 2026-09-06 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'f0e1d2c3b4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMN — SQLite supports ALTER TABLE ADD COLUMN, so no
    # batch_alter_table table-recreate is needed (that's only for altering
    # existing columns/constraints). No index: the cover-dedup pass is a full
    # scan of a few thousand short strings.
    op.add_column('files', sa.Column('cover_phash', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('files', 'cover_phash')
