"""add local_files

Revision ID: f9a1c2d3e4b5
Revises: e4ef4e5285d7
Create Date: 2026-08-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f9a1c2d3e4b5'
down_revision: Union[str, None] = 'e4ef4e5285d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'local_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'copied', 'dismissed', name='localfilestatus'),
            nullable=False,
        ),
        sa.Column('discovered_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('path'),
    )


def downgrade() -> None:
    op.drop_table('local_files')
