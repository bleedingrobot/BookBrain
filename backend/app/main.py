import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.jobs.scheduler import (
    create_scheduler,
    sync_autoget_schedule,
    sync_backup_schedule,
    sync_libgen_autoget_schedule,
    sync_llm_tagging_schedule,
    sync_nightly_schedule,
    sync_torrent_local_scan_schedule,
    sync_torrent_poll_schedule,
    sync_torrent_submit_schedule,
)

# Under `uvicorn app.main:app` (no --log-config), uvicorn only configures its
# own "uvicorn"/"uvicorn.access" loggers — it never touches the root logger.
# With nothing else configuring it either, every `app.*` logger.info(...) call
# was silently dropped: Python's default root level is WARNING, and the
# no-handlers-anywhere "last resort" fallback only prints WARNING+, never
# INFO. That's why e.g. acquisition_service's "auto-got" success line, and
# the nightly job's own summary, never showed up in `journalctl -u
# bookbrain.service` even though the work was actually happening (confirmed
# live 2026-09-18 by checking the DB directly). The INFO level is scoped to
# the "app" namespace, not the root logger, because third-party libraries are
# far chattier than we are: httpx logs an INFO line per HTTP request, which in
# a long-running server would bury our own output.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
_app_logger.propagate = False
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _app_logger.addHandler(_handler)

# The root logger gets the same formatter so that library output is visually
# identical to ours and greppable by the same rules — but at WARNING, for the
# httpx reason above. Before this, the root logger had no handler anywhere in
# its chain, so every non-`app.*` logger (apscheduler.*, sqlalchemy.*,
# websockets, httpx) fell through to Python's `logging.lastResort`: a bare
# WARNING StreamHandler with *no formatter*. The errors were all being
# printed, they just arrived with no timestamp, no level and no logger name.
# Measured over the 7 days to 2026-09-18: 202 `Traceback (most recent call
# last):` in the journal against exactly 1 line matching `ERROR app.`, so
# `journalctl -u bookbrain.service | grep -i error` found one line for the
# week while 32 `database is locked` failures went unnoticed. `app.*` keeps
# `propagate = False`, so it does not double-log through this handler.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.WARNING)
if not _root_logger.handlers:
    _root_handler = logging.StreamHandler()
    _root_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root_logger.addHandler(_root_handler)


class _MaxInstancesFilter(logging.Filter):
    """Drop APScheduler's max-instances skip line; keep everything else.

    47.5% of the journal (25,836 of 54,427 lines in 24h) was:

        Execution of job "LLM tagging (trigger: interval[0:00:02], ...)"
        skipped: maximum number of running instances reached (1)

    That is not a bug and the 2s interval is not a mistake — see the comment
    in jobs/scheduler.py: a tiny interval plus max_instances=1 plus coalesce
    is a deliberate "refire the instant the previous tick frees up" idiom, so
    the real pacing is however long Ollama takes. The message is misleveled
    noise, not a signal.

    It is safe to filter surgically because APScheduler puts it on a
    different logger from job errors: `apscheduler.scheduler` logs exactly
    two things at WARNING (this, and "Error getting due jobs from job
    store"), while `'Job "%s" raised an exception'` is logged by
    `apscheduler.executors.<alias>` and is untouched by this filter. A filter
    is used rather than raising the logger to ERROR so the jobstore-read
    warning survives.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "maximum number of running" not in str(record.msg)


logging.getLogger("apscheduler.scheduler").addFilter(_MaxInstancesFilter())

# NOT disabled: the uvicorn access log, which is another ~45% of the journal.
# Killing it is the obvious next move and it is the wrong one. BookBrain's own
# application logging is 456 lines per 7 days — one every ~22 minutes, with a
# routine 16-minute gap (p99 inter-line gap 3.97 min, max 16.2 min). On that
# stream alone, "silent for 10 minutes" is normal and a log-silence alarm is
# unbuildable. With the access log on, the p99 gap across all lines is 31s, so
# "nothing for 5 minutes" has a near-zero false-positive rate and would have
# caught the 14-minute event-loop hang on 2026-09-18. The dashboard's own 30s
# poll is BookBrain's de facto heartbeat, and the alerts block in dashboard.sh
# builds its silence alarm on top of it. Silencing the APScheduler noise above
# is safe precisely because the access log stays.

logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs in the uvicorn worker, not the --reload supervisor, so the
    # scheduler is created exactly once. `run_nightly_job` guards against
    # overlapping runs on its own.
    scheduler = create_scheduler()
    try:
        await sync_nightly_schedule(scheduler)
        await sync_backup_schedule(scheduler)
        await sync_autoget_schedule(scheduler)
        await sync_libgen_autoget_schedule(scheduler)
        await sync_torrent_submit_schedule(scheduler)
        await sync_torrent_poll_schedule(scheduler)
        await sync_torrent_local_scan_schedule(scheduler)
        await sync_llm_tagging_schedule(scheduler)
        scheduler.start()
    except Exception:  # a broken schedule must never stop the API booting
        logger.exception("scheduler failed to start")
    app.state.scheduler = scheduler
    if settings.openbooks_enabled:
        # Auto-start the OpenBooks child process so auto-get survives a
        # backend restart/reboot unattended, matching the scheduler above.
        # Runs as a managed child of this process (see
        # openbooks_process_service) rather than its own systemd unit, since
        # the app can only stop/restart a server it started itself on Linux.
        try:
            from app.services import openbooks_process_service

            await asyncio.to_thread(openbooks_process_service.start)
        except Exception:  # binary missing / port busy — don't block startup
            logger.exception("openbooks: failed to auto-start at boot")
    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # already stopped / never started
            pass
        try:
            from app.services import openbooks_service

            await openbooks_service.aclose()
        except Exception:  # never connected / already closed
            pass
        try:
            from app.services import openbooks_process_service

            openbooks_process_service.shutdown()
        except Exception:  # we never started it
            pass


app = FastAPI(title="EPUB Librarian API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # frontend_origin is the local admin UI; library_viewer_origin is the
    # family library-viewer (GitHub Pages) — only its passcode login path
    # calls this backend at all (Google sign-in talks to Drive directly).
    # allow_credentials=True is required for that login's session cookie,
    # which means allow_origins can't be "*" — both must be listed exactly.
    allow_origins=[o for o in (settings.frontend_origin, settings.library_viewer_origin) if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Serve the built admin frontend (frontend/dist, produced by `npm run build`)
# from this same always-on backend, so there is one permanent URL instead of
# a locally-launched dev server. Registered after api_router so /api/* still
# wins; falls back to index.html for any other path so client-side routing
# (react-router) keeps working on a hard refresh/direct link.
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_frontend_dist / "index.html")
