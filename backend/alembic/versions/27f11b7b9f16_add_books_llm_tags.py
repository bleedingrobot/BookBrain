"""add books.llm_tags_json

Revision ID: 27f11b7b9f16
Revises: c1d2e3f4a5b6
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '27f11b7b9f16'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # prompts/38 — local-LLM (Ollama) tagging + descriptions, kept in its own
    # column rather than nested under hardcover_json: hardcover_recs_service
    # does full-object replacement of that column, which would silently wipe
    # this data on its next refresh.
    op.add_column('books', sa.Column('llm_tags_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('books', 'llm_tags_json')
