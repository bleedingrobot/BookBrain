"""Tests for the torrent subsystem's APScheduler wiring (app/jobs/scheduler.py)."""

import pytest

from app.core.settings_keys import TORRENT_AUTOGET_ENABLED
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


@pytest.fixture
def _torrent_setting(monkeypatch):
    """torrent_enabled is read via a locally-scoped `from app.core.config
    import get_settings` inside each sync_* function, so patching the
    module attribute here is enough — no import-binding gotcha like the
    provider-composition-root case elsewhere in this codebase."""
    import app.core.config as cfg

    state = {"torrent_enabled": True}

    class _Cfg:
        @property
        def torrent_enabled(self):
            return state["torrent_enabled"]

    monkeypatch.setattr(cfg, "get_settings", lambda: _Cfg())
    return state


async def test_torrent_submit_schedule_adds_removes_and_reschedules(db_session, _torrent_setting):
    scheduler = sched.create_scheduler()
    repo = SettingsRepository(db_session)

    # toggle off -> no job
    await sched.sync_torrent_submit_schedule(scheduler)
    assert scheduler.get_job(sched._TORRENT_SUBMIT_JOB_ID) is None

    # toggle on -> job registered on its own interval
    await repo.set(TORRENT_AUTOGET_ENABLED, "true")
    await sched.sync_torrent_submit_schedule(scheduler)
    job = scheduler.get_job(sched._TORRENT_SUBMIT_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == sched.TORRENT_SUBMIT_INTERVAL_SECONDS

    # calling again with the same state reschedules, not duplicates
    await sched.sync_torrent_submit_schedule(scheduler)
    jobs = [j for j in scheduler.get_jobs() if j.id == sched._TORRENT_SUBMIT_JOB_ID]
    assert len(jobs) == 1

    # toggle off again -> removed
    await repo.set(TORRENT_AUTOGET_ENABLED, "false")
    await sched.sync_torrent_submit_schedule(scheduler)
    assert scheduler.get_job(sched._TORRENT_SUBMIT_JOB_ID) is None


async def test_torrent_submit_schedule_off_when_torrent_disabled_even_with_autoget_on(
    db_session, _torrent_setting
):
    """The submit leg needs both switches — the dedicated auto-get toggle
    AND settings.torrent_enabled (the whole subsystem's on/off)."""
    scheduler = sched.create_scheduler()
    await SettingsRepository(db_session).set(TORRENT_AUTOGET_ENABLED, "true")
    _torrent_setting["torrent_enabled"] = False

    await sched.sync_torrent_submit_schedule(scheduler)
    assert scheduler.get_job(sched._TORRENT_SUBMIT_JOB_ID) is None


async def test_torrent_poll_schedule_independent_of_the_autoget_toggle(db_session, _torrent_setting):
    """Poll only cares about settings.torrent_enabled — it must keep running
    even with the submit toggle off, so an in-flight torrent isn't
    stranded."""
    scheduler = sched.create_scheduler()
    # TORRENT_AUTOGET_ENABLED is left unset (off) — poll should still register.
    await sched.sync_torrent_poll_schedule(scheduler)
    job = scheduler.get_job(sched._TORRENT_POLL_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == sched.TORRENT_POLL_INTERVAL_SECONDS

    _torrent_setting["torrent_enabled"] = False
    await sched.sync_torrent_poll_schedule(scheduler)
    assert scheduler.get_job(sched._TORRENT_POLL_JOB_ID) is None


async def test_torrent_local_scan_schedule_independent_of_the_autoget_toggle(db_session, _torrent_setting):
    scheduler = sched.create_scheduler()
    await sched.sync_torrent_local_scan_schedule(scheduler)
    job = scheduler.get_job(sched._TORRENT_LOCAL_SCAN_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == sched.TORRENT_LOCAL_SCAN_INTERVAL_SECONDS

    _torrent_setting["torrent_enabled"] = False
    await sched.sync_torrent_local_scan_schedule(scheduler)
    assert scheduler.get_job(sched._TORRENT_LOCAL_SCAN_JOB_ID) is None


async def test_read_torrent_autoget_enabled_defaults_false(db_session):
    assert await sched.read_torrent_autoget_enabled() is False
    await SettingsRepository(db_session).set(TORRENT_AUTOGET_ENABLED, "true")
    assert await sched.read_torrent_autoget_enabled() is True
