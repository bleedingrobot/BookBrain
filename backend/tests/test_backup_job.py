"""Tests for the scheduled DB backup orchestrator (app/jobs/backup_job.py)."""

import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy import select
from types import SimpleNamespace

from app.data.models import JobRun, JobRunStatus
from app.jobs import backup_job
from app.schemas.drive import FolderConfig
from app.services import job_run_service


@pytest.fixture(autouse=True)
def _route_sessions(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(job_run_service, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(backup_job, "async_session_factory", lambda: _CM())


def _patch(monkeypatch, *, creds=object(), library="lib-1", refresh_error=False, backup=None):
    class _FakeAuth:
        async def get_credentials(self, _repo):
            if refresh_error:
                raise RefreshError("invalid_grant")
            return creds

    monkeypatch.setattr(backup_job, "get_auth_service", lambda: _FakeAuth())

    async def fake_library(_repo):
        return (
            FolderConfig(folder_id=library, folder_name="Library", created_by_app=False)
            if library
            else None
        )

    monkeypatch.setattr(
        backup_job.DriveService, "get_library_folder_config", staticmethod(fake_library)
    )

    async def fake_backup(_creds, _folder_id, **_kw):
        if backup == "raise":
            raise RuntimeError("drive down")
        return SimpleNamespace(db_name="epub_librarian-2026-09-07.db.gz", total_bytes=4096, kept=7)

    monkeypatch.setattr(backup_job.backup_service, "create_backup", fake_backup)


async def test_happy_path_writes_a_success_row(db_session, monkeypatch):
    _patch(monkeypatch)
    result = await backup_job.run_backup_job(trigger="scheduler")

    assert result.ok and not result.skipped
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.kind == "backup" and row.status == JobRunStatus.success
    assert "epub_librarian-2026-09-07.db.gz" in row.summary


async def test_dead_token_is_a_clean_failure(db_session, monkeypatch):
    _patch(monkeypatch, refresh_error=True)
    result = await backup_job.run_backup_job(trigger="cli")

    assert not result.ok
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.failed and "reconnect Google" in row.error


async def test_no_library_folder_is_a_clean_failure(db_session, monkeypatch):
    _patch(monkeypatch, library=None)
    result = await backup_job.run_backup_job(trigger="cli")

    assert not result.ok
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.failed


async def test_backup_service_raising_writes_a_failed_row(db_session, monkeypatch):
    _patch(monkeypatch, backup="raise")
    result = await backup_job.run_backup_job(trigger="cli")

    assert not result.ok and "drive down" in result.error
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.failed


async def test_skips_when_another_backup_run_is_active(db_session, monkeypatch):
    _patch(monkeypatch)
    await job_run_service.start_run("backup", "scheduler")  # a run already in flight

    result = await backup_job.run_backup_job(trigger="cli")

    assert result.skipped
    kinds = [r.summary for r in (await db_session.execute(select(JobRun))).scalars().all()]
    assert any(s and "skipped" in s for s in kinds)
