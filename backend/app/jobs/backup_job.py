"""Scheduled DB backup to Drive.

Mirrors `app.jobs.nightly` but does only the one thing — snapshot
`epub_librarian.db` into the Drive library folder's `backups/` (see
`backup_service`). It has its own Settings toggle + hour so backups can run
without the full nightly pipeline.

Two callers, like the nightly job:

* the in-process APScheduler job (`app.jobs.scheduler.sync_backup_schedule`),
  for when the server is up at the configured hour;
* `python -m app.jobs.backup_job`, a standalone entrypoint a Windows Scheduled
  Task can call for when the server is down.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from google.auth.exceptions import GoogleAuthError

from app.data.db import async_session_factory
from app.data.models import JobRunStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.services import backup_service, job_run_service
from app.services.auth_service import get_auth_service
from app.services.drive_service import DriveService

logger = logging.getLogger(__name__)

JOB_KIND = "backup"

_backup_lock = asyncio.Lock()

_RECONNECT_HINT = "reconnect Google in Settings"


def reset_backup_lock() -> None:
    """Test-only — see nightly.reset_nightly_lock."""
    global _backup_lock
    _backup_lock = asyncio.Lock()


@dataclass
class BackupJobResult:
    ok: bool
    summary: str
    error: str | None = None
    skipped: bool = False


async def run_backup_job(*, trigger: str) -> BackupJobResult:
    """Resolve credentials + the library folder, guard against a colliding
    run, take the backup, write a `job_runs` row. Never raises."""
    if _backup_lock.locked():
        await job_run_service.record_skipped(JOB_KIND, trigger, "already running in this process")
        return BackupJobResult(ok=True, skipped=True, summary="skipped — already running")

    async with _backup_lock:
        if await job_run_service.has_active_run(JOB_KIND):
            await job_run_service.record_skipped(JOB_KIND, trigger, "another backup run is active")
            return BackupJobResult(ok=True, skipped=True, summary="skipped — another run active")

        async with async_session_factory() as session:
            settings_repo = SettingsRepository(session)
            try:
                creds = await get_auth_service().get_credentials(settings_repo)
            except GoogleAuthError as exc:
                msg = f"Google token could not be refreshed — {_RECONNECT_HINT}."
                logger.error("backup: %s (%s)", msg, exc)
                await _record_failed(trigger, msg)
                return BackupJobResult(ok=False, summary=msg, error=msg)

            if creds is None:
                msg = f"Google Drive is not connected — {_RECONNECT_HINT}."
                await _record_failed(trigger, msg)
                return BackupJobResult(ok=False, summary=msg, error=msg)

            library = await DriveService.get_library_folder_config(settings_repo)

        if library is None:
            msg = "No library folder is configured — set one in Settings."
            await _record_failed(trigger, msg)
            return BackupJobResult(ok=False, summary=msg, error=msg)

        run_id = await job_run_service.start_run(JOB_KIND, trigger)
        try:
            result = await backup_service.create_backup(creds, library.folder_id)
        except Exception as exc:
            logger.exception("backup: run failed")
            await job_run_service.finish_run(run_id, status=JobRunStatus.failed, error=str(exc))
            return BackupJobResult(ok=False, summary=f"failed: {exc}", error=str(exc))

        summary = f"{result.db_name} ({result.total_bytes // 1024} KB, kept {result.kept})"
        await job_run_service.finish_run(run_id, status=JobRunStatus.success, summary=summary)
        logger.info("backup: done — %s", summary)
        return BackupJobResult(ok=True, summary=summary)


async def _record_failed(trigger: str, message: str) -> None:
    run_id = await job_run_service.start_run(JOB_KIND, trigger)
    await job_run_service.finish_run(run_id, status=JobRunStatus.failed, error=message)


_LOG_PATH = Path(__file__).resolve().parents[2] / "backup-runs.log"


def _configure_standalone_logging() -> None:
    handler = RotatingFileHandler(_LOG_PATH, maxBytes=500_000, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(stream)


def main() -> int:
    _configure_standalone_logging()
    logger.info("backup: standalone run starting (log: %s)", _LOG_PATH)
    result = asyncio.run(run_backup_job(trigger="cli"))
    if result.skipped:
        return 0
    if not result.ok:
        logger.error("backup: %s", result.error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
