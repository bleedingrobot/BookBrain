"""add authors.hardcover_json / hardcover_synced_at

Revision ID: c5d6e7f8a9ba
Revises: b4c5d6e7f8a9
Create Date: 2026-09-08 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9ba'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMN — SQLite handles these without a rebuild.
    # prompts/27 Part 2: Hardcover's recent + near-future books per author.
    op.add_column('authors', sa.Column('hardcover_json', sa.JSON(), nullable=True))
    op.add_column('authors', sa.Column('hardcover_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('authors', 'hardcover_synced_at')
    op.drop_column('authors', 'hardcover_json')
