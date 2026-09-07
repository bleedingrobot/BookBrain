"""Tests for the nightly unattended run (app/jobs/nightly.py)."""

from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy import select

from app.data.models import File, FileStatus, JobRun, JobRunStatus, Review, ReviewStatus
from app.jobs import nightly
from app.schemas.drive import FolderConfig
from app.schemas.scan import ScanJobState
from app.services import job_run_service


@pytest.fixture(autouse=True)
def _route_sessions_to_test_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(job_run_service, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(nightly, "async_session_factory", lambda: _CM())


class _FakeScanService:
    def __init__(self, detail="1 new, 0 flagged for review, 1 auto-organized, 0 failed to parse"):
        self._detail = detail
        self.ran_with: tuple | None = None
        self._running = False

    def create_job(self):
        return SimpleNamespace(job_id="job-1")

    async def run_scan(self, job_id, creds, folder_id):
        self.ran_with = (job_id, creds, folder_id)

    def get_status(self, job_id):
        return SimpleNamespace(status=ScanJobState.done, detail=self._detail)

    def has_running_job(self):
        return self._running


def _patch_pipeline(monkeypatch, *, scan=None, covers=None, index=812, backup_raises=False):
    scan = scan or _FakeScanService()
    monkeypatch.setattr(nightly, "get_scan_service", lambda: scan)

    async def fake_covers(creds, library_folder_id, **kwargs):
        return covers or {"done": 3, "nocover": 1, "failed": 0, "remaining": 0}

    async def fake_index(creds, library_folder_id):
        return index

    async def fake_pull(creds, inbox_folder_id):
        return "torrents: 0 copied, 0 failed"

    async def fake_backup(creds, library_folder_id, **kwargs):
        if backup_raises:
            raise RuntimeError("drive down")
        return SimpleNamespace(db_name="epub_librarian-2026-09-07.db.gz", total_bytes=4096, kept=7)

    monkeypatch.setattr(nightly, "regenerate_covers", fake_covers)
    monkeypatch.setattr(nightly, "regenerate_library_index", fake_index)
    monkeypatch.setattr(nightly, "_pull_local_folder", fake_pull)
    monkeypatch.setattr(nightly.backup_service, "create_backup", fake_backup)
    return scan


# --------------------------------------------------------------------------
# run_nightly — the credential-free core
# --------------------------------------------------------------------------


async def test_run_nightly_runs_every_phase(monkeypatch):
    scan = _patch_pipeline(monkeypatch)

    result = await nightly.run_nightly(
        object(),
        inbox_folder_id="inbox-1",
        library_folder_id="lib-1",
        pull_local_folder=False,
    )

    assert result.ok
    assert scan.ran_with is not None
    assert scan.ran_with[0] == "job-1" and scan.ran_with[2] == "inbox-1"
    assert "scan:" in result.summary
    assert "covers: 3 new" in result.summary
    assert "index: 812 books" in result.summary
    assert "backup: epub_librarian-2026-09-07.db.gz" in result.summary


async def test_run_nightly_backup_failure_does_not_abort_the_run(monkeypatch):
    _patch_pipeline(monkeypatch, backup_raises=True)

    result = await nightly.run_nightly(
        object(), inbox_folder_id="inbox-1", library_folder_id="lib-1", pull_local_folder=False
    )

    assert result.ok
    assert "backup: FAILED — drive down" in result.summary
    assert "scan:" in result.summary  # the run carried on


async def test_pull_local_folder_noops_on_empty_folder(db_session, monkeypatch, tmp_path):
    settings = nightly.get_settings()
    monkeypatch.setattr(settings, "torrents_watch_folder", str(tmp_path))
    assert await nightly._pull_local_folder(object(), "inbox-1") is None


async def test_run_nightly_pulls_local_folder_when_enabled(monkeypatch):
    _patch_pipeline(monkeypatch)
    called = {}

    async def fake_pull(creds, inbox_folder_id):
        called["hit"] = inbox_folder_id
        return "torrents: 2 copied, 0 failed"

    monkeypatch.setattr(nightly, "_pull_local_folder", fake_pull)

    result = await nightly.run_nightly(
        object(), inbox_folder_id="inbox-1", library_folder_id="lib-1", pull_local_folder=True
    )

    assert called["hit"] == "inbox-1"
    assert "torrents: 2 copied" in result.summary


async def test_run_nightly_skips_covers_and_index_without_library_folder(monkeypatch):
    _patch_pipeline(monkeypatch)

    result = await nightly.run_nightly(
        object(), inbox_folder_id="inbox-1", library_folder_id=None, pull_local_folder=False
    )

    assert result.ok
    assert "no library folder" in result.summary


async def test_run_nightly_raises_when_scan_fails(monkeypatch):
    scan = _FakeScanService()

    def failed_status(job_id):
        return SimpleNamespace(status=ScanJobState.failed, detail="Drive API 500")

    scan.get_status = failed_status
    _patch_pipeline(monkeypatch, scan=scan)

    with pytest.raises(RuntimeError, match="scan failed"):
        await nightly.run_nightly(
            object(), inbox_folder_id="inbox-1", library_folder_id="lib-1", pull_local_folder=False
        )


async def test_run_nightly_never_touches_reviews_or_duplicates(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)

    book_file = File(
        drive_file_id="d1", filename="a.epub", sha256="s1", size_bytes=1, status=FileStatus.review
    )
    dup_file = File(
        drive_file_id="d2", filename="b.epub", sha256="s2", size_bytes=1, status=FileStatus.duplicate
    )
    db_session.add_all([book_file, dup_file])
    await db_session.flush()
    db_session.add(Review(file_id=book_file.id, status=ReviewStatus.pending, proposed_json={}))
    await db_session.commit()

    await nightly.run_nightly(
        object(), inbox_folder_id="inbox-1", library_folder_id="lib-1", pull_local_folder=False
    )

    reviews = (await db_session.execute(select(Review))).scalars().all()
    assert [r.status for r in reviews] == [ReviewStatus.pending]
    dups = (await db_session.execute(select(File).where(File.status == FileStatus.duplicate))).scalars().all()
    assert len(dups) == 1


# --------------------------------------------------------------------------
# run_nightly_job — the orchestrator
# --------------------------------------------------------------------------


def _patch_auth_and_folders(
    monkeypatch, *, creds=object(), inbox="inbox-1", library="lib-1", refresh_error=False
):
    class _FakeAuth:
        async def get_credentials(self, settings_repo):
            if refresh_error:
                raise RefreshError("invalid_grant")
            return creds

    monkeypatch.setattr(nightly, "get_auth_service", lambda: _FakeAuth())

    async def fake_inbox(_repo):
        return FolderConfig(folder_id=inbox, folder_name="Inbox", created_by_app=False) if inbox else None

    async def fake_library(_repo):
        return (
            FolderConfig(folder_id=library, folder_name="Library", created_by_app=False)
            if library
            else None
        )

    monkeypatch.setattr(nightly.DriveService, "get_inbox_folder_config", staticmethod(fake_inbox))
    monkeypatch.setattr(nightly.DriveService, "get_library_folder_config", staticmethod(fake_library))


async def test_run_nightly_job_happy_path_writes_success_row(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)
    _patch_auth_and_folders(monkeypatch)

    result = await nightly.run_nightly_job(trigger="cli")

    assert result.ok and not result.skipped
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.success
    assert row.trigger == "cli"
    assert row.finished_at is not None
    assert "scan:" in row.summary


async def test_run_nightly_job_dead_token_is_clean_not_a_crash(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)
    _patch_auth_and_folders(monkeypatch, refresh_error=True)

    result = await nightly.run_nightly_job(trigger="scheduler")

    assert not result.ok
    assert "reconnect google in settings" in result.error.lower()
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.failed


async def test_run_nightly_job_not_connected_fails_cleanly(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)
    _patch_auth_and_folders(monkeypatch, creds=None)

    result = await nightly.run_nightly_job(trigger="cli")

    assert not result.ok
    assert "not connected" in result.error.lower()
    row = (await db_session.execute(select(JobRun))).scalar_one()
    assert row.status == JobRunStatus.failed


async def test_run_nightly_job_no_inbox_folder_fails_cleanly(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)
    _patch_auth_and_folders(monkeypatch, inbox=None)

    result = await nightly.run_nightly_job(trigger="cli")

    assert not result.ok
    assert "inbox" in result.error.lower()


async def test_run_nightly_job_skips_when_a_run_is_already_active(db_session, monkeypatch):
    _patch_pipeline(monkeypatch)
    _patch_auth_and_folders(monkeypatch)

    # A stale-but-recent running row from a previous invocation.
    db_session.add(JobRun(kind="nightly", trigger="scheduler", status=JobRunStatus.running))
    await db_session.commit()

    result = await nightly.run_nightly_job(trigger="cli")

    assert result.skipped
    rows = (await db_session.execute(select(JobRun).where(JobRun.trigger == "cli"))).scalars().all()
    assert len(rows) == 1
    assert "skipped" in rows[0].summary


async def test_run_nightly_job_skips_when_a_manual_scan_is_running(db_session, monkeypatch):
    scan = _FakeScanService()
    scan._running = True
    _patch_pipeline(monkeypatch, scan=scan)
    _patch_auth_and_folders(monkeypatch)

    result = await nightly.run_nightly_job(trigger="scheduler")

    assert result.skipped
    assert scan.ran_with is None
