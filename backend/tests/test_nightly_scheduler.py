"""Tests for the APScheduler wiring (app/jobs/scheduler.py)."""

import pytest

from app.core.settings_keys import (
    BACKUP_RUN_ENABLED,
    BACKUP_RUN_HOUR,
    NIGHTLY_RUN_ENABLED,
    NIGHTLY_RUN_HOUR,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.jobs import scheduler as sched


@pytest.fixture(autouse=True)
def _route_sessions_to_test_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(sched, "async_session_factory", lambda: _CM())


async def test_read_nightly_config_defaults(db_session):
    enabled, hour = await sched.read_nightly_config()
    assert enabled is False
    assert hour == sched.DEFAULT_NIGHTLY_HOUR


async def test_read_nightly_config_reads_stored_values(db_session):
    repo = SettingsRepository(db_session)
    await repo.set(NIGHTLY_RUN_ENABLED, "true")
    await repo.set(NIGHTLY_RUN_HOUR, "23")
    enabled, hour = await sched.read_nightly_config()
    assert enabled is True and hour == 23


async def test_read_nightly_config_clamps_and_survives_junk(db_session):
    repo = SettingsRepository(db_session)
    await repo.set(NIGHTLY_RUN_HOUR, "99")
    _, hour = await sched.read_nightly_config()
    assert hour == 23
    await repo.set(NIGHTLY_RUN_HOUR, "not-a-number")
    _, hour = await sched.read_nightly_config()
    assert hour == sched.DEFAULT_NIGHTLY_HOUR


async def test_sync_adds_removes_and_reschedules_the_job(db_session):
    scheduler = sched.create_scheduler()
    repo = SettingsRepository(db_session)

    # disabled -> no job
    await sched.sync_nightly_schedule(scheduler)
    assert scheduler.get_job(sched._NIGHTLY_JOB_ID) is None

    # enabled -> job at the configured hour
    await repo.set(NIGHTLY_RUN_ENABLED, "true")
    await repo.set(NIGHTLY_RUN_HOUR, "3")
    await sched.sync_nightly_schedule(scheduler)
    job = scheduler.get_job(sched._NIGHTLY_JOB_ID)
    assert job is not None
    assert "hour='3'" in str(job.trigger)

    # hour change -> rescheduled, still one job
    await repo.set(NIGHTLY_RUN_HOUR, "5")
    await sched.sync_nightly_schedule(scheduler)
    jobs = [j for j in scheduler.get_jobs() if j.id == sched._NIGHTLY_JOB_ID]
    assert len(jobs) == 1
    assert "hour='5'" in str(jobs[0].trigger)

    # disabled again -> job removed
    await repo.set(NIGHTLY_RUN_ENABLED, "false")
    await sched.sync_nightly_schedule(scheduler)
    assert scheduler.get_job(sched._NIGHTLY_JOB_ID) is None


async def test_backup_schedule_is_independent_of_the_nightly_one(db_session):
    scheduler = sched.create_scheduler()
    repo = SettingsRepository(db_session)

    # backup on, nightly off -> only the backup job exists
    await repo.set(BACKUP_RUN_ENABLED, "true")
    await repo.set(BACKUP_RUN_HOUR, "4")
    await sched.sync_backup_schedule(scheduler)
    await sched.sync_nightly_schedule(scheduler)

    assert scheduler.get_job(sched._NIGHTLY_JOB_ID) is None
    job = scheduler.get_job(sched._BACKUP_JOB_ID)
    assert job is not None and "hour='4'" in str(job.trigger)

    await repo.set(BACKUP_RUN_ENABLED, "false")
    await sched.sync_backup_schedule(scheduler)
    assert scheduler.get_job(sched._BACKUP_JOB_ID) is None


async def test_read_backup_config_defaults(db_session):
    enabled, hour = await sched.read_backup_config()
    assert enabled is False and hour == sched.DEFAULT_BACKUP_HOUR
