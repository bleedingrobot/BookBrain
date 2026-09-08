"""add books.hardcover_json / hardcover_synced_at

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-08 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMN — SQLite handles these without a rebuild.
    # prompts/25 Phase 3: Hardcover's "readers also liked" per book.
    op.add_column('books', sa.Column('hardcover_json', sa.JSON(), nullable=True))
    op.add_column('books', sa.Column('hardcover_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'hardcover_synced_at')
    op.drop_column('books', 'hardcover_json')
