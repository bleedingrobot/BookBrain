"""add acquisition_candidates

Revision ID: ee2155ba2aa5
Revises: e7f8a9bacbd0
Create Date: 2026-09-10 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ee2155ba2aa5'
down_revision: Union[str, None] = 'e7f8a9bacbd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'acquisition_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(), nullable=False),
        sa.Column('request_title', sa.String(), nullable=False),
        sa.Column('request_author', sa.String(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('pending', 'approved', 'skipped', 'no_match', 'failed', name='acquisitionstatus'),
            nullable=False,
        ),
        sa.Column('candidate_full', sa.String(), nullable=True),
        sa.Column('candidate_title', sa.String(), nullable=True),
        sa.Column('candidate_author', sa.String(), nullable=True),
        sa.Column('candidate_format', sa.String(), nullable=True),
        sa.Column('candidate_size', sa.String(), nullable=True),
        sa.Column('candidate_server', sa.String(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('alternatives_json', sa.JSON(), nullable=True),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id'),
    )


def downgrade() -> None:
    op.drop_table('acquisition_candidates')
