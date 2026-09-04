"""Extracts a small cover thumbnail from each organised EPUB and drops it in
a `covers/` folder inside the Drive library root, named `<driveFileId>.jpg`.
The static library-viewer lists that folder once and shows the thumbnails
inline. Purely additive — a missing `covers/` folder just means the viewer
falls back to an Open Library cover (by ISBN) or a placeholder."""

import asyncio
import io
import logging
import uuid
from collections.abc import Callable

from google.oauth2.credentials import Credentials
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import File, FileStatus
from app.providers.comic.archive import extract_comic_cover, is_comic_archive
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.parser import extract_cover
from app.schemas.covers import CoverJobState, CoverJobStatus

logger = logging.getLogger(__name__)

COVERS_FOLDER_NAME = "covers"
_COVER_MAX_PX = 320
_COVER_MIME = "image/jpeg"
_COVER_CONCURRENCY = 4
_NO_COVER_EXT = ".nocover"  # 0-byte marker: this EPUB has no extractable cover


def _thumbnail(raw: bytes) -> bytes | None:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((_COVER_MAX_PX, _COVER_MAX_PX))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def _ensure_covers_folder(provider: DriveProvider, library_folder_id: str) -> str:
    existing = next(
        (f for f in provider.list_folders(library_folder_id) if f["name"] == COVERS_FOLDER_NAME),
        None,
    )
    if existing is not None:
        return existing["id"]
    return provider.create_folder(COVERS_FOLDER_NAME, parent_id=library_folder_id)["id"]


def _make_one(
    provider: DriveProvider, covers_folder_id: str, drive_file_id: str, filename: str
) -> str:
    settings = get_settings()
    raw_book = provider.download_file(drive_file_id)
    extract = extract_comic_cover if is_comic_archive(filename) else extract_cover
    cover_raw = extract(
        raw_book,
        max_entry_bytes=settings.epub_max_entry_bytes,
        max_total_bytes=settings.epub_max_total_bytes,
        max_entries=settings.epub_max_entries,
    )
    thumb = _thumbnail(cover_raw) if cover_raw is not None else None
    if thumb is None:
        # Leave a marker so this EPUB isn't re-downloaded on every run just
        # to rediscover it has no usable cover.
        provider.upload_new_file(
            name=f"{drive_file_id}{_NO_COVER_EXT}",
            data=b"",
            parent_id=covers_folder_id,
            mime_type="text/plain",
        )
        return "nocover"
    provider.upload_new_file(
        name=f"{drive_file_id}.jpg",
        data=thumb,
        parent_id=covers_folder_id,
        mime_type=_COVER_MIME,
    )
    return "done"


async def regenerate_covers(
    creds: Credentials | None,
    library_folder_id: str | None,
    *,
    limit: int | None = None,
    on_progress: Callable[[dict[str, int], int], None] | None = None,
) -> dict[str, int]:
    """Generate covers for organised books that don't have one yet. Bounded
    by `limit` (the organize hook passes a small number so it just chips
    away); the manual endpoint leaves it None for a full backfill.
    `on_progress(counts, total)` fires after each file so a long backfill
    can report progress. Best-effort — never raises into the caller."""
    counts = {"done": 0, "nocover": 0, "failed": 0, "remaining": 0}
    if creds is None or not library_folder_id:
        return counts
    try:
        lister = DriveProvider(build_drive_service(creds))
        covers_folder_id = await asyncio.to_thread(
            _ensure_covers_folder, lister, library_folder_id
        )
        # A book is "handled" once it has either a .jpg thumbnail or a
        # .nocover marker.
        have = {
            f["name"].removesuffix(".jpg").removesuffix(_NO_COVER_EXT)
            for f in await asyncio.to_thread(lister.list_files_in_folder, covers_folder_id)
        }

        async with async_session_factory() as session:
            rows = await session.execute(
                select(File.drive_file_id, File.filename).where(
                    File.status == FileStatus.organised, File.book_id.is_not(None)
                )
            )
            missing = [(r[0], r[1]) for r in rows.all() if r[0] not in have]

        todo = missing if limit is None else missing[:limit]
        counts["remaining"] = len(missing) - len(todo)
        total = len(todo)

        sem = asyncio.Semaphore(_COVER_CONCURRENCY)

        async def run(entry: tuple[str, str]) -> None:
            drive_id, filename = entry
            async with sem:
                # Fresh provider per file — httplib2 isn't safe to share
                # across the threads asyncio.to_thread hands work to.
                provider = DriveProvider(build_drive_service(creds))
                try:
                    counts[
                        await asyncio.to_thread(
                            _make_one, provider, covers_folder_id, drive_id, filename
                        )
                    ] += 1
                except Exception:
                    logger.exception("cover generation failed for %s", drive_id)
                    counts["failed"] += 1
                if on_progress is not None:
                    on_progress(counts, total)

        await asyncio.gather(*(run(d) for d in todo))
        logger.info("covers pass: %s", counts)
    except Exception:
        logger.exception("cover regeneration failed")
    return counts


class CoverService:
    def __init__(self) -> None:
        self._jobs: dict[str, CoverJobStatus] = {}

    def create_job(self) -> CoverJobStatus:
        job_id = str(uuid.uuid4())
        status = CoverJobStatus(job_id=job_id, status=CoverJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> CoverJobStatus | None:
        return self._jobs.get(job_id)

    async def run(
        self, job_id: str, creds: Credentials, library_folder_id: str
    ) -> None:
        def progress(counts: dict[str, int], total: int) -> None:
            self._jobs[job_id] = CoverJobStatus(
                job_id=job_id,
                status=CoverJobState.running,
                generated=counts["done"],
                no_cover=counts["nocover"],
                failed=counts["failed"],
                remaining=total - counts["done"] - counts["nocover"] - counts["failed"],
            )

        counts = await regenerate_covers(creds, library_folder_id, on_progress=progress)
        self._jobs[job_id] = CoverJobStatus(
            job_id=job_id,
            status=CoverJobState.done,
            generated=counts["done"],
            no_cover=counts["nocover"],
            failed=counts["failed"],
            remaining=counts["remaining"],
        )


_cover_service = CoverService()


def get_cover_service() -> CoverService:
    return _cover_service
