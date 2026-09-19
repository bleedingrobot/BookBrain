"""add dismissed_audit_clusters

Revision ID: 13df754eacc9
Revises: b1c2d3e4f5a6
Create Date: 2026-09-05 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '13df754eacc9'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dismissed_audit_clusters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('series', 'author', name='auditclusterkind'), nullable=False),
        sa.Column('member_ids_key', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'member_ids_key', name='uq_dismissed_audit_cluster'),
    )


def downgrade() -> None:
    op.drop_table('dismissed_audit_clusters')
