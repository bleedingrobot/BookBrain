"""add job_runs

Revision ID: c7d8e9f0a1b2
Revises: 13df754eacc9
Create Date: 2026-09-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = '13df754eacc9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('trigger', sa.String(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('running', 'success', 'failed', name='jobrunstatus'),
            nullable=False,
        ),
        sa.Column(
            'started_at',
            sa.DateTime(),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_runs_kind_started', 'job_runs', ['kind', 'started_at'])


def downgrade() -> None:
    op.drop_index('ix_job_runs_kind_started', table_name='job_runs')
    op.drop_table('job_runs')
