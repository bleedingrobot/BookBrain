"""acquisition_candidate provider column

Revision ID: 2074aef71940
Revises: ae807bad1056
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2074aef71940'
down_revision: Union[str, None] = 'ae807bad1056'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'acquisition_candidates',
        sa.Column('candidate_provider', sa.String(), nullable=False, server_default='openbooks'),
    )


def downgrade() -> None:
    op.drop_column('acquisition_candidates', 'candidate_provider')
