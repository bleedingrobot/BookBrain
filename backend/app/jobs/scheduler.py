"""In-process APScheduler wiring for the nightly run.

This is layer A of task 2: if the FastAPI server happens to be up at the
configured hour, the nightly job fires here with no OS-level config. Layer B
— `python -m app.jobs.nightly` driven by a Windows Scheduled Task — covers
the (common) case where the server is down overnight.

The scheduler is created and started in `app.main`'s lifespan, which only
runs in the uvicorn *worker* process, not the `--reload` supervisor — so the
job is registered once, not twice. The job itself also carries
`max_instances=1` and `coalesce=True`, and `run_nightly_job` has its own
in-process + DB guards, so a misfire storm still can't stack runs.

Times are the machine's local time (APScheduler's default timezone).
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.settings_keys import NIGHTLY_RUN_ENABLED, NIGHTLY_RUN_HOUR
from app.data.db import async_session_factory
from app.data.repositories.settings_repository import SettingsRepository
from app.jobs.nightly import run_nightly_job

logger = logging.getLogger(__name__)

_NIGHTLY_JOB_ID = "nightly-run"
DEFAULT_NIGHTLY_HOUR = 2


async def _run_scheduled_nightly() -> None:
    await run_nightly_job(trigger="scheduler")


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler()


async def read_nightly_config() -> tuple[bool, int]:
    """(enabled, hour) from the settings table, with sane fallbacks."""
    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        enabled = (await repo.get(NIGHTLY_RUN_ENABLED)) == "true"
        raw_hour = await repo.get(NIGHTLY_RUN_HOUR)
    try:
        hour = int(raw_hour) if raw_hour is not None else DEFAULT_NIGHTLY_HOUR
    except ValueError:
        hour = DEFAULT_NIGHTLY_HOUR
    hour = min(23, max(0, hour))
    return enabled, hour


async def sync_nightly_schedule(scheduler: AsyncIOScheduler) -> None:
    """Bring the registered job in line with the current settings. Called at
    startup and again whenever the Settings toggle/hour changes."""
    enabled, hour = await read_nightly_config()
    existing = scheduler.get_job(_NIGHTLY_JOB_ID)

    if not enabled:
        if existing is not None:
            scheduler.remove_job(_NIGHTLY_JOB_ID)
            logger.info("nightly schedule: disabled")
        return

    trigger = CronTrigger(hour=hour, minute=0)
    if existing is None:
        scheduler.add_job(
            _run_scheduled_nightly,
            trigger=trigger,
            id=_NIGHTLY_JOB_ID,
            name="BookBrain nightly run",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    else:
        scheduler.reschedule_job(_NIGHTLY_JOB_ID, trigger=trigger)
    logger.info("nightly schedule: enabled, fires daily at %02d:00 local time", hour)
