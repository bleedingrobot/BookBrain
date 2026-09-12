"""add books.embedding / embedding_hash / embedding_model

Revision ID: e7f8a9bacbd0
Revises: d6e7f8a9bacb
Create Date: 2026-09-09 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f8a9bacbd0'
down_revision: Union[str, None] = 'd6e7f8a9bacb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable ADD COLUMNs — SQLite handles these without a rebuild.
    # prompts/29: a local all-MiniLM-L6-v2 sentence embedding per book for the
    # library-viewer's semantic search.
    op.add_column('books', sa.Column('embedding', sa.LargeBinary(), nullable=True))
    op.add_column('books', sa.Column('embedding_hash', sa.String(), nullable=True))
    op.add_column('books', sa.Column('embedding_model', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'embedding_model')
    op.drop_column('books', 'embedding_hash')
    op.drop_column('books', 'embedding')
