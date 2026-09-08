"""add authors.hardcover_person_id

Revision ID: d6e7f8a9bacb
Revises: c5d6e7f8a9ba
Create Date: 2026-09-09 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6e7f8a9bacb'
down_revision: Union[str, None] = 'c5d6e7f8a9ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMN — SQLite handles these without a rebuild.
    # prompts/28 Phase 1: the resolved Hardcover "person id" (canonical +
    # alias walk), so two Author rows for one person share a value and can
    # be merged even without a shared book.
    op.add_column('authors', sa.Column('hardcover_person_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('authors', 'hardcover_person_id')
