"""index acquisition_events by (provider, occurred_at)

`acquisition_events` now gets a row per auto-get search that found nothing, not
just per download, so it grows roughly an order of magnitude faster (OpenBooks
is capped at 8 searches/hour, LibGen at 80). provider_health reads it as "the
last 5 events for provider X" and "the last 5 successes for provider X", twice
per provider, every 30 seconds while the dashboard is open -- which on the
single-column provider index means sorting that provider's entire history each
time. This is the index for the query that actually runs.

Deliberately no backfill of the new `no_match` count from the queue. The
surviving `no_match` rows in `acquisition_candidates` have `resolved_at` NULL
(only `updated_at`, which `list_requests` touches every time it re-ranks), so
seeding from them would drop hundreds of events with near-now timestamps into the
health window and manufacture exactly the outage this work exists to detect.
The counter starts at zero at deploy and fills in within hours.

Revision ID: d5a2c8e41f76
Revises: c4f1a7b93e28
Create Date: 2026-09-18 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd5a2c8e41f76'
down_revision: Union[str, None] = 'c4f1a7b93e28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_acquisition_events_provider_occurred_at',
        'acquisition_events',
        ['provider', 'occurred_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_acquisition_events_provider_occurred_at', table_name='acquisition_events'
    )
