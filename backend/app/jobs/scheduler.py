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
from apscheduler.triggers.interval import IntervalTrigger

from app.core.settings_keys import (
    BACKUP_RUN_ENABLED,
    BACKUP_RUN_HOUR,
    LLM_TAGGING_ENABLED,
    NIGHTLY_RUN_ENABLED,
    NIGHTLY_RUN_HOUR,
    OPENBOOKS_AUTOGET_ENABLED,
    TORRENT_AUTOGET_ENABLED,
)
from app.data.db import async_session_factory
from app.data.repositories.settings_repository import SettingsRepository
from app.jobs.backup_job import run_backup_job
from app.jobs.nightly import run_nightly_job

logger = logging.getLogger(__name__)

_NIGHTLY_JOB_ID = "nightly-run"
_BACKUP_JOB_ID = "backup-run"
_AUTOGET_JOB_ID = "openbooks-autoget"
_LIBGEN_AUTOGET_JOB_ID = "libgen-autoget"
_LLM_TAGGING_JOB_ID = "llm-tagging"
_TORRENT_SUBMIT_JOB_ID = "torrent-submit"
_TORRENT_POLL_JOB_ID = "torrent-poll"
_TORRENT_LOCAL_SCAN_JOB_ID = "torrent-local-scan"
DEFAULT_NIGHTLY_HOUR = 2
DEFAULT_BACKUP_HOUR = 3
# Slow and steady — the #ebook bots rate-limit a nick on search and download,
# so one careful acquisition every few minutes (auto-get itself caps searches
# per hour on top of this). Jittered so ticks aren't metronomic.
AUTOGET_INTERVAL_SECONDS = 360
AUTOGET_INTERVAL_JITTER = 90
# Libgen's own cycle, deliberately a different period AND phase from
# OpenBooks' — it's a plain HTTP scraper (self-throttled inside
# LibgenProvider) with no shared IRC bot to be careful with, so there's no
# reason to run it on OpenBooks' slow clock or only ever search the same
# book at the same moment as OpenBooks does. Running the two out of sync
# also means one being down/busy never blocks the other's turn.
LIBGEN_AUTOGET_INTERVAL_SECONDS = 150
LIBGEN_AUTOGET_INTERVAL_JITTER = 45
# The torrent subsystem's three independent legs. Submitting is still
# slower than the other two cycles' searches — each hit is a real download
# commitment (bandwidth, disk, a Librarr/qBittorrent slot), not a cheap HTTP
# call — but James's own experience is that public-domain/small ebooks come
# through fast, so 5 min (down from an initial 15) rather than something
# tighter still; the 3-torrent concurrency cap (_MAX_CONCURRENT_TORRENTS in
# torrent_service.py) is the real backstop against overlap regardless of
# this interval. Polling and the local-scan handoff are both cheap, local-
# network-ish calls (Librarr's own API; a directory walk + Drive upload), so
# they run tight and independently of the submit toggle — see
# torrent_service.py's module docstring for why.
TORRENT_SUBMIT_INTERVAL_SECONDS = 300
TORRENT_SUBMIT_INTERVAL_JITTER = 45
TORRENT_POLL_INTERVAL_SECONDS = 60
TORRENT_POLL_INTERVAL_JITTER = 10
TORRENT_LOCAL_SCAN_INTERVAL_SECONDS = 120
TORRENT_LOCAL_SCAN_INTERVAL_JITTER = 20
# prompts/38 — one Ollama call per tick (one map/reduce step of a book's
# full-text pass). Originally 300s on the assumption ticks would mostly be
# no-ops between rare windows opening — James wants full throughput instead:
# back-to-back as fast as Ollama actually responds (~8-10s/chunk measured).
# A tiny interval + max_instances=1 + coalesce achieves that: APScheduler
# fires again the instant the previous tick frees up rather than waiting out
# the interval, so the real pacing is just "how long the last call took."
# tick() itself still no-ops outside allowed windows / while Ollama's down.
LLM_TAGGING_INTERVAL_SECONDS = 2
LLM_TAGGING_INTERVAL_JITTER = 0


async def _run_scheduled_nightly() -> None:
    await run_nightly_job(trigger="scheduler")


async def _run_scheduled_backup() -> None:
    await run_backup_job(trigger="scheduler")


async def _run_scheduled_autoget() -> None:
    from app.services import acquisition_service

    try:
        await acquisition_service.autoget_tick(trigger="scheduler")
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("openbooks auto-get tick failed")


async def _run_scheduled_libgen_autoget() -> None:
    from app.services import acquisition_service

    try:
        await acquisition_service.libgen_autoget_tick(trigger="scheduler")
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("libgen auto-get tick failed")


async def _run_scheduled_torrent_submit() -> None:
    from app.services import torrent_service

    try:
        await torrent_service.submit_tick(trigger="scheduler")
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("torrent submit tick failed")


async def _run_scheduled_torrent_poll() -> None:
    from app.services import torrent_service

    try:
        await torrent_service.poll_tick(trigger="scheduler")
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("torrent poll tick failed")


async def _run_scheduled_torrent_local_scan() -> None:
    from app.services import torrent_service

    try:
        await torrent_service.local_scan_tick(trigger="scheduler")
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("torrent local-scan tick failed")


async def _run_scheduled_llm_tagging() -> None:
    from app.services import llm_tagging_service

    try:
        await llm_tagging_service.tick()
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("llm tagging tick failed")


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler()


async def _read_schedule(enabled_key: str, hour_key: str, default_hour: int) -> tuple[bool, int]:
    """(enabled, hour) from the settings table, with sane fallbacks."""
    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        enabled = (await repo.get(enabled_key)) == "true"
        raw_hour = await repo.get(hour_key)
    try:
        hour = int(raw_hour) if raw_hour is not None else default_hour
    except ValueError:
        hour = default_hour
    return enabled, min(23, max(0, hour))


async def _sync_schedule(
    scheduler: AsyncIOScheduler,
    *,
    job_id: str,
    name: str,
    func,
    enabled: bool,
    hour: int,
) -> None:
    existing = scheduler.get_job(job_id)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(job_id)
            logger.info("%s: disabled", name)
        return
    trigger = CronTrigger(hour=hour, minute=0)
    if existing is None:
        scheduler.add_job(
            func, trigger=trigger, id=job_id, name=name,
            max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    else:
        scheduler.reschedule_job(job_id, trigger=trigger)
    logger.info("%s: enabled, fires daily at %02d:00 local time", name, hour)


async def read_nightly_config() -> tuple[bool, int]:
    return await _read_schedule(NIGHTLY_RUN_ENABLED, NIGHTLY_RUN_HOUR, DEFAULT_NIGHTLY_HOUR)


async def read_backup_config() -> tuple[bool, int]:
    return await _read_schedule(BACKUP_RUN_ENABLED, BACKUP_RUN_HOUR, DEFAULT_BACKUP_HOUR)


async def sync_nightly_schedule(scheduler: AsyncIOScheduler) -> None:
    """Bring the registered job in line with the current settings. Called at
    startup and again whenever the Settings toggle/hour changes."""
    enabled, hour = await read_nightly_config()
    await _sync_schedule(
        scheduler, job_id=_NIGHTLY_JOB_ID, name="BookBrain nightly run",
        func=_run_scheduled_nightly, enabled=enabled, hour=hour,
    )


async def sync_backup_schedule(scheduler: AsyncIOScheduler) -> None:
    enabled, hour = await read_backup_config()
    await _sync_schedule(
        scheduler, job_id=_BACKUP_JOB_ID, name="BookBrain backup",
        func=_run_scheduled_backup, enabled=enabled, hour=hour,
    )


async def read_autoget_enabled() -> bool:
    async with async_session_factory() as session:
        return (await SettingsRepository(session).get(OPENBOOKS_AUTOGET_ENABLED)) == "true"


async def sync_autoget_schedule(scheduler: AsyncIOScheduler) -> None:
    """An interval job (every ~60s) that downloads one confident 'Books to
    get' candidate when idle. Registered only while the toggle is on."""
    enabled = await read_autoget_enabled()
    existing = scheduler.get_job(_AUTOGET_JOB_ID)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(_AUTOGET_JOB_ID)
            logger.info("openbooks auto-get: disabled")
        return
    trigger = IntervalTrigger(seconds=AUTOGET_INTERVAL_SECONDS, jitter=AUTOGET_INTERVAL_JITTER)
    if existing is None:
        scheduler.add_job(
            _run_scheduled_autoget, trigger=trigger, id=_AUTOGET_JOB_ID,
            name="OpenBooks auto-get", max_instances=1, coalesce=True, misfire_grace_time=120,
        )
        logger.info("openbooks auto-get: enabled, ~one acquisition per %ds when idle", AUTOGET_INTERVAL_SECONDS)
    else:
        scheduler.reschedule_job(_AUTOGET_JOB_ID, trigger=trigger)


async def sync_libgen_autoget_schedule(scheduler: AsyncIOScheduler) -> None:
    """Libgen's own auto-get cycle. Shares the master on/off toggle with
    OpenBooks' cycle (one "auto-get" switch in Settings) but not its
    interval — see LIBGEN_AUTOGET_INTERVAL_SECONDS for why. Also off
    whenever the libgen provider itself is disabled."""
    from app.core.config import get_settings

    enabled = await read_autoget_enabled() and get_settings().libgen_enabled
    existing = scheduler.get_job(_LIBGEN_AUTOGET_JOB_ID)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(_LIBGEN_AUTOGET_JOB_ID)
            logger.info("libgen auto-get: disabled")
        return
    trigger = IntervalTrigger(
        seconds=LIBGEN_AUTOGET_INTERVAL_SECONDS, jitter=LIBGEN_AUTOGET_INTERVAL_JITTER
    )
    if existing is None:
        scheduler.add_job(
            _run_scheduled_libgen_autoget, trigger=trigger, id=_LIBGEN_AUTOGET_JOB_ID,
            name="Libgen auto-get", max_instances=1, coalesce=True, misfire_grace_time=120,
        )
        logger.info(
            "libgen auto-get: enabled, ~one acquisition per %ds when idle",
            LIBGEN_AUTOGET_INTERVAL_SECONDS,
        )
    else:
        scheduler.reschedule_job(_LIBGEN_AUTOGET_JOB_ID, trigger=trigger)


async def read_torrent_autoget_enabled() -> bool:
    async with async_session_factory() as session:
        return (await SettingsRepository(session).get(TORRENT_AUTOGET_ENABLED)) == "true"


async def sync_torrent_submit_schedule(scheduler: AsyncIOScheduler) -> None:
    """The torrent subsystem's submit leg — its own toggle (separate from
    OPENBOOKS_AUTOGET_ENABLED, see settings_keys.TORRENT_AUTOGET_ENABLED),
    and off entirely whenever settings.torrent_enabled is false."""
    from app.core.config import get_settings

    enabled = await read_torrent_autoget_enabled() and get_settings().torrent_enabled
    existing = scheduler.get_job(_TORRENT_SUBMIT_JOB_ID)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(_TORRENT_SUBMIT_JOB_ID)
            logger.info("torrent submit: disabled")
        return
    trigger = IntervalTrigger(
        seconds=TORRENT_SUBMIT_INTERVAL_SECONDS, jitter=TORRENT_SUBMIT_INTERVAL_JITTER
    )
    if existing is None:
        scheduler.add_job(
            _run_scheduled_torrent_submit, trigger=trigger, id=_TORRENT_SUBMIT_JOB_ID,
            name="Torrent submit", max_instances=1, coalesce=True, misfire_grace_time=300,
        )
        logger.info(
            "torrent submit: enabled, ~one submission per %ds when idle",
            TORRENT_SUBMIT_INTERVAL_SECONDS,
        )
    else:
        scheduler.reschedule_job(_TORRENT_SUBMIT_JOB_ID, trigger=trigger)


async def sync_torrent_poll_schedule(scheduler: AsyncIOScheduler) -> None:
    """The torrent subsystem's poll leg — deliberately independent of
    TORRENT_AUTOGET_ENABLED (only gated on settings.torrent_enabled): turning
    auto-get off should stop *starting* new torrents, not strand ones
    already in flight with nothing polling them to completion."""
    from app.core.config import get_settings

    existing = scheduler.get_job(_TORRENT_POLL_JOB_ID)
    if not get_settings().torrent_enabled:
        if existing is not None:
            scheduler.remove_job(_TORRENT_POLL_JOB_ID)
            logger.info("torrent poll: disabled")
        return
    trigger = IntervalTrigger(seconds=TORRENT_POLL_INTERVAL_SECONDS, jitter=TORRENT_POLL_INTERVAL_JITTER)
    if existing is None:
        scheduler.add_job(
            _run_scheduled_torrent_poll, trigger=trigger, id=_TORRENT_POLL_JOB_ID,
            name="Torrent poll", max_instances=1, coalesce=True, misfire_grace_time=60,
        )
        logger.info("torrent poll: enabled, checking every %ds", TORRENT_POLL_INTERVAL_SECONDS)
    else:
        scheduler.reschedule_job(_TORRENT_POLL_JOB_ID, trigger=trigger)


async def sync_torrent_local_scan_schedule(scheduler: AsyncIOScheduler) -> None:
    """The torrent subsystem's handoff leg — runs the existing torrents-
    watch-folder scan/match/upload on a short interval instead of only
    nightly. Same independence from TORRENT_AUTOGET_ENABLED as the poll leg,
    for the same reason (files already sitting in the folder shouldn't wait
    on the submit toggle)."""
    from app.core.config import get_settings

    existing = scheduler.get_job(_TORRENT_LOCAL_SCAN_JOB_ID)
    if not get_settings().torrent_enabled:
        if existing is not None:
            scheduler.remove_job(_TORRENT_LOCAL_SCAN_JOB_ID)
            logger.info("torrent local-scan: disabled")
        return
    trigger = IntervalTrigger(
        seconds=TORRENT_LOCAL_SCAN_INTERVAL_SECONDS, jitter=TORRENT_LOCAL_SCAN_INTERVAL_JITTER
    )
    if existing is None:
        scheduler.add_job(
            _run_scheduled_torrent_local_scan, trigger=trigger, id=_TORRENT_LOCAL_SCAN_JOB_ID,
            name="Torrent local-scan handoff", max_instances=1, coalesce=True, misfire_grace_time=60,
        )
        logger.info("torrent local-scan: enabled, checking every %ds", TORRENT_LOCAL_SCAN_INTERVAL_SECONDS)
    else:
        scheduler.reschedule_job(_TORRENT_LOCAL_SCAN_JOB_ID, trigger=trigger)


async def read_llm_tagging_enabled() -> bool:
    async with async_session_factory() as session:
        return (await SettingsRepository(session).get(LLM_TAGGING_ENABLED)) == "true"


async def sync_llm_tagging_schedule(scheduler: AsyncIOScheduler) -> None:
    """A tight-interval job (effectively back-to-back — see the constants
    above) that does one Ollama-tagging unit of work when a tick lands
    inside an allowed window. Registered only while the toggle is on —
    llm_tagging_service.tick() does the actual window/reachability gating
    on top of that."""
    enabled = await read_llm_tagging_enabled()
    existing = scheduler.get_job(_LLM_TAGGING_JOB_ID)
    if not enabled:
        if existing is not None:
            scheduler.remove_job(_LLM_TAGGING_JOB_ID)
            logger.info("llm tagging: disabled")
        return
    trigger = IntervalTrigger(
        seconds=LLM_TAGGING_INTERVAL_SECONDS, jitter=LLM_TAGGING_INTERVAL_JITTER
    )
    if existing is None:
        scheduler.add_job(
            _run_scheduled_llm_tagging, trigger=trigger, id=_LLM_TAGGING_JOB_ID,
            name="LLM tagging", max_instances=1, coalesce=True, misfire_grace_time=120,
        )
        logger.info("llm tagging: enabled, running back-to-back inside allowed windows")
    else:
        scheduler.reschedule_job(_LLM_TAGGING_JOB_ID, trigger=trigger)
