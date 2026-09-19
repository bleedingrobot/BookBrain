"""add books.content_recs_json / content_recs_synced_at

Revision ID: ae807bad1056
Revises: 27f11b7b9f16
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ae807bad1056'
down_revision: Union[str, None] = '27f11b7b9f16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # prompts/39 — locally-computed tag-similarity "similar books" (content_recs_service),
    # mirroring the hardcover_json / hardcover_synced_at column pair.
    op.add_column('books', sa.Column('content_recs_json', sa.JSON(), nullable=True))
    op.add_column('books', sa.Column('content_recs_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'content_recs_synced_at')
    op.drop_column('books', 'content_recs_json')
