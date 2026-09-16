"""add librarr_requests

Revision ID: fbc7d75ffb70
Revises: 5ba922c8ef82
Create Date: 2026-09-16 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fbc7d75ffb70'
down_revision: Union[str, None] = '5ba922c8ef82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'librarr_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(), nullable=False),
        sa.Column('librarr_request_id', sa.String(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'pending', 'approved', 'searching', 'downloading', 'completed', 'failed',
                name='librarrrequeststatus',
            ),
            nullable=False,
        ),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('added_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('librarr_request_id'),
    )


def downgrade() -> None:
    op.drop_table('librarr_requests')
