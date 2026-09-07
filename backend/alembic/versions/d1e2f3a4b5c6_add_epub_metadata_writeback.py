"""add epub metadata writeback columns

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-09-06 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('files', sa.Column('original_sha256', sa.String(), nullable=True))
    op.add_column('files', sa.Column('embedded_metadata_key', sa.String(), nullable=True))
    op.create_index('ix_files_original_sha256', 'files', ['original_sha256'])


def downgrade() -> None:
    op.drop_index('ix_files_original_sha256', table_name='files')
    op.drop_column('files', 'embedded_metadata_key')
    op.drop_column('files', 'original_sha256')
