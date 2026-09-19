"""add series.hardcover_json / hardcover_synced_at

Revision ID: a3b4c5d6e7f8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMN — SQLite handles these without a table rebuild.
    # prompts/25 Phase 2: Hardcover's canonical series membership, cached per
    # series and refreshed on a schedule.
    op.add_column('series', sa.Column('hardcover_json', sa.JSON(), nullable=True))
    op.add_column('series', sa.Column('hardcover_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('series', 'hardcover_synced_at')
    op.drop_column('series', 'hardcover_json')
