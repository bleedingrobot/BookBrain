import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.jobs.scheduler import create_scheduler, sync_nightly_schedule

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
        scheduler.start()
    except Exception:  # a broken schedule must never stop the API booting
        logger.exception("nightly scheduler failed to start")
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # already stopped / never started
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
