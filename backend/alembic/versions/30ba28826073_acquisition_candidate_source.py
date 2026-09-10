"""acquisition_candidate source column

Revision ID: 30ba28826073
Revises: ee2155ba2aa5
Create Date: 2026-09-10 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '30ba28826073'
down_revision: Union[str, None] = 'ee2155ba2aa5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'acquisition_candidates',
        sa.Column('source', sa.String(), nullable=False, server_default='wishlist'),
    )


def downgrade() -> None:
    op.drop_column('acquisition_candidates', 'source')
