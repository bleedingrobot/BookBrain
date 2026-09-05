"""The nightly unattended run.

Everything in BookBrain's pipeline is a manual button in the admin UI
(check Torrents → scan → review → clear duplicates → organize → covers →
refresh index). `run_nightly` does the parts that are safe to do with no
human present, in one pass:

1. pull the local Torrents folder into the Drive inbox (best-effort)
2. scan the inbox — which also auto-organizes everything that clears the
   confidence threshold, via `ScanService.run_scan`'s built-in `_auto_organize`
3. regenerate cover thumbnails for anything newly organised
4. regenerate the `bookbrain-index.json` sidecar the library-viewer reads

It never touches the review queue or the duplicates list — anything the
pipeline isn't sure about still waits for James in the morning.

Two callers share one job function:

* `app.main`'s APScheduler job, for when the server is up at the scheduled
  hour (`_scheduled_nightly` there).
* `python -m app.jobs.nightly`, a standalone entrypoint with no HTTP layer,
  for a Windows Scheduled Task to call. Exits non-zero on failure.

`run_nightly_job` is the orchestrator both use: it resolves credentials and
folders, guards against colliding with another run, and writes a `job_runs`
audit row. `run_nightly` is the credential-and-folder-free core, kept
separate so tests can drive it with fakes.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import JobRunStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services import job_run_service, local_scan_service
from app.services.auth_service import get_auth_service
from app.services.cover_service import regenerate_covers
from app.services.drive_service import DriveService
from app.services.library_index_service import regenerate_library_index
from app.services.scan_service import get_scan_service
from app.schemas.scan import ScanJobState

logger = logging.getLogger(__name__)

JOB_KIND = "nightly"

# In-process guard against the scheduled job overlapping itself — most
# importantly the APScheduler misfire/coalesce edge, or the scheduler firing
# while a `python -m app.jobs.nightly` started from the same process tree is
# still going. Cross-process collisions (scheduler in the server vs. a
# Scheduled Task) are caught by job_run_service.has_active_run instead.
_nightly_lock = asyncio.Lock()

_RECONNECT_HINT = "reconnect Google in Settings"


def reset_nightly_lock() -> None:
    """Test-only. asyncio.Lock binds to the event loop of its first acquire;
    pytest-asyncio gives each test its own loop, so a lock reused across
    tests raises "bound to a different event loop". conftest.py calls this
    before every test."""
    global _nightly_lock
    _nightly_lock = asyncio.Lock()


@dataclass
class NightlyResult:
    ok: bool
    summary: str
    error: str | None = None
    skipped: bool = False
    steps: list[str] = field(default_factory=list)


async def _pull_local_folder(creds: Credentials, inbox_folder_id: str) -> str | None:
    """Copy anything new in the watched Torrents folder into the Drive inbox
    so the scan below picks it up. Best-effort — a failure here (folder gone,
    a locked file) must not stop the rest of the run."""
    settings = get_settings()
    try:
        async with async_session_factory() as session:
            pending = await local_scan_service.scan_local_folder(
                session, settings.torrents_watch_folder
            )
            if not pending:
                return None
            provider = DriveProvider(build_drive_service(creds))
            result = await local_scan_service.copy_to_drive(
                session, [row.id for row in pending], provider, inbox_folder_id
            )
        return f"torrents: {result['copied']} copied, {result['failed']} failed"
    except Exception:
        logger.exception("nightly: local Torrents pull failed")
        return "torrents: pull failed (see logs)"


async def _scan_phase(creds: Credentials, inbox_folder_id: str) -> str:
    """Runs the same scan the "Start scan" button does — including its
    tail-end auto-organize of everything that cleared the threshold."""
    service = get_scan_service()
    job = service.create_job()
    await service.run_scan(job.job_id, creds, inbox_folder_id)
    status = service.get_status(job.job_id)
    if status is None:  # pragma: no cover - defensive
        return "scan: no status"
    if status.status == ScanJobState.failed:
        raise RuntimeError(f"scan failed: {status.detail}")
    return f"scan: {status.detail}"


async def run_nightly(
    creds: Credentials,
    *,
    inbox_folder_id: str,
    library_folder_id: str | None,
    pull_local_folder: bool = True,
) -> NightlyResult:
    """The credential-and-folder-free core. Assumes `creds` is live and the
    folder ids are resolved. Raises nothing it can help — each phase is
    logged and folded into the summary."""
    steps: list[str] = []

    if pull_local_folder:
        pulled = await _pull_local_folder(creds, inbox_folder_id)
        if pulled:
            steps.append(pulled)

    steps.append(await _scan_phase(creds, inbox_folder_id))

    if library_folder_id:
        cover_counts = await regenerate_covers(creds, library_folder_id)
        steps.append(
            f"covers: {cover_counts['done']} new, {cover_counts['nocover']} no-cover, "
            f"{cover_counts['failed']} failed"
        )
        index_count = await regenerate_library_index(creds, library_folder_id)
        steps.append(
            f"index: {index_count} books" if index_count is not None else "index: skipped"
        )
    else:
        steps.append("covers/index: skipped (no library folder)")

    return NightlyResult(ok=True, summary="; ".join(steps), steps=steps)


async def run_nightly_job(*, trigger: str) -> NightlyResult:
    """Orchestrator used by both the scheduler and the standalone entrypoint.
    Resolves credentials + folders, guards against a colliding run, and
    writes a `job_runs` row. Never raises."""
    if _nightly_lock.locked():
        logger.info("nightly: skipped, already running in this process")
        await job_run_service.record_skipped(JOB_KIND, trigger, "already running in this process")
        return NightlyResult(ok=True, skipped=True, summary="skipped — already running")

    async with _nightly_lock:
        if get_scan_service().has_running_job():
            logger.info("nightly: skipped, a manual scan is in flight")
            await job_run_service.record_skipped(JOB_KIND, trigger, "a manual scan is in flight")
            return NightlyResult(ok=True, skipped=True, summary="skipped — manual scan in flight")

        if await job_run_service.has_active_run(JOB_KIND):
            logger.info("nightly: skipped, another nightly run is active")
            await job_run_service.record_skipped(JOB_KIND, trigger, "another nightly run is active")
            return NightlyResult(ok=True, skipped=True, summary="skipped — another run active")

        async with async_session_factory() as session:
            settings_repo = SettingsRepository(session)
            try:
                creds = await get_auth_service().get_credentials(settings_repo)
            except GoogleAuthError as exc:
                msg = f"Google token could not be refreshed — {_RECONNECT_HINT}."
                logger.error("nightly: %s (%s)", msg, exc)
                await _record_failed(trigger, msg)
                return NightlyResult(ok=False, summary=msg, error=msg)

            if creds is None:
                msg = f"Google Drive is not connected — {_RECONNECT_HINT}."
                logger.error("nightly: %s", msg)
                await _record_failed(trigger, msg)
                return NightlyResult(ok=False, summary=msg, error=msg)

            inbox = await DriveService.get_inbox_folder_config(settings_repo)
            library = await DriveService.get_library_folder_config(settings_repo)

        if inbox is None:
            msg = "No inbox folder is configured — set one in Settings."
            logger.error("nightly: %s", msg)
            await _record_failed(trigger, msg)
            return NightlyResult(ok=False, summary=msg, error=msg)

        run_id = await job_run_service.start_run(JOB_KIND, trigger)
        try:
            result = await run_nightly(
                creds,
                inbox_folder_id=inbox.folder_id,
                library_folder_id=library.folder_id if library else None,
            )
        except Exception as exc:
            logger.exception("nightly: run failed")
            await job_run_service.finish_run(
                run_id, status=JobRunStatus.failed, error=str(exc)
            )
            return NightlyResult(ok=False, summary=f"failed: {exc}", error=str(exc))

        await job_run_service.finish_run(
            run_id, status=JobRunStatus.success, summary=result.summary
        )
        logger.info("nightly: done — %s", result.summary)
        return result


async def _record_failed(trigger: str, message: str) -> None:
    run_id = await job_run_service.start_run(JOB_KIND, trigger)
    await job_run_service.finish_run(run_id, status=JobRunStatus.failed, error=message)


# --------------------------------------------------------------------------
# Standalone entrypoint: `python -m app.jobs.nightly`
# --------------------------------------------------------------------------

_LOG_PATH = Path(__file__).resolve().parents[2] / "nightly-runs.log"


def _configure_standalone_logging() -> None:
    handler = RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
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
    logger.info("nightly: standalone run starting (log: %s)", _LOG_PATH)
    result = asyncio.run(run_nightly_job(trigger="cli"))
    if result.skipped:
        logger.info("nightly: %s", result.summary)
        return 0
    if not result.ok:
        logger.error("nightly: run did not complete — %s", result.error)
        return 1
    logger.info("nightly: %s", result.summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
