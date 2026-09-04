"""add wishlist

Revision ID: b1c2d3e4f5a6
Revises: f9a1c2d3e4b5
Create Date: 2026-09-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'f9a1c2d3e4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'wishlist',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('raw_request', sa.Text(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('author', sa.String(), nullable=True),
        sa.Column('series', sa.String(), nullable=True),
        sa.Column('series_number', sa.Float(), nullable=True),
        sa.Column('isbn13', sa.String(), nullable=True),
        sa.Column('cover_url', sa.String(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('wanted', 'acquired', name='wishliststatus'),
            nullable=False,
        ),
        sa.Column('acquired_at', sa.DateTime(), nullable=True),
        sa.Column('acquired_file_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False
        ),
        sa.ForeignKeyConstraint(['acquired_file_id'], ['files.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('wishlist')
