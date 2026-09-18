"""add acquisition_events

An append-only log of finished acquisition attempts. The dashboard used to
count successes out of `acquisition_candidates`, which is a queue that deletes
a row once the book is organised and hides it once the wishlist item is
reconciled -- so every provider under-reported, and slow ones (OpenBooks, one
book per ~10 min) usually showed an empty "last got" list while fast ones
(LibGen, one per minute) looked healthy purely because something was always
still inside the deletion lag.

Backfills from whatever approved/failed rows are still in the queue so the
lifetime counters don't restart at zero. That backfill is necessarily a floor:
rows already pruned are gone and cannot be recovered.

Revision ID: c4f1a7b93e28
Revises: fbc7d75ffb70
Create Date: 2026-09-18 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a7b93e28'
down_revision: Union[str, None] = 'fbc7d75ffb70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'acquisition_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'occurred_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False
        ),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('server', sa.String(), nullable=True),
        sa.Column('outcome', sa.String(), nullable=False),
        sa.Column('request_id', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('author', sa.String(), nullable=True),
        sa.Column('filename', sa.String(), nullable=True),
        sa.Column('message', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_acquisition_events_occurred_at', 'acquisition_events', ['occurred_at'])
    op.create_index('ix_acquisition_events_provider', 'acquisition_events', ['provider'])

    # Seed from the queue rows that survive, so "got 125" doesn't become
    # "got 0" the moment this ships. `resolved_at` is when the attempt
    # finished, which is exactly what occurred_at means here.
    op.execute(
        """
        INSERT INTO acquisition_events
            (occurred_at, provider, server, outcome, request_id, source, title, author, filename, message)
        SELECT
            resolved_at,
            COALESCE(candidate_provider, 'openbooks'),
            candidate_server,
            CASE status WHEN 'approved' THEN 'got' ELSE 'failed' END,
            request_id,
            source,
            COALESCE(candidate_title, request_title),
            COALESCE(candidate_author, request_author),
            NULL,
            message
        FROM acquisition_candidates
        WHERE status IN ('approved', 'failed') AND resolved_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index('ix_acquisition_events_provider', table_name='acquisition_events')
    op.drop_index('ix_acquisition_events_occurred_at', table_name='acquisition_events')
    op.drop_table('acquisition_events')
