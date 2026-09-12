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
    sync_nightly_schedule,
)

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
    allow_origins=[settings.frontend_origin],
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
