"""local_files wishlist match columns

Revision ID: 5ba922c8ef82
Revises: 2074aef71940
Create Date: 2026-09-16 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5ba922c8ef82'
down_revision: Union[str, None] = '2074aef71940'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('local_files', sa.Column('matched_request_id', sa.String(), nullable=True))
    op.add_column('local_files', sa.Column('matched_title', sa.String(), nullable=True))
    op.add_column('local_files', sa.Column('matched_author', sa.String(), nullable=True))
    op.add_column('local_files', sa.Column('matched_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('local_files', 'matched_score')
    op.drop_column('local_files', 'matched_author')
    op.drop_column('local_files', 'matched_title')
    op.drop_column('local_files', 'matched_request_id')
