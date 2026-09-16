"""Extracts a small cover thumbnail from each organised EPUB and drops it in
a `covers/` folder inside the Drive library root, named `<driveFileId>.jpg`.
The static library-viewer lists that folder once and shows the thumbnails
inline. Purely additive — a missing `covers/` folder just means the viewer
falls back to an Open Library cover (by ISBN) or a placeholder.

When an EPUB has no embedded cover at all, a free keyless fallback is tried
first: Apple's public iTunes Search API carries ebook artwork for most trade
titles. Only accepted when the top hit's title is a close match to the
book's — an unrelated cover is worse than none, and this runs unattended."""

import asyncio
import io
import logging
import uuid
from collections.abc import Callable

import httpx
import imagehash
from google.oauth2.credentials import Credentials
from PIL import Image
from sqlalchemy import case, select, update

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import Author, Book, File, FileStatus
from app.providers.comic.archive import extract_comic_cover, is_comic_archive
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.parser import extract_cover
from app.schemas.covers import CoverJobState, CoverJobStatus
from app.services.text_match import title_similarity

logger = logging.getLogger(__name__)

COVERS_FOLDER_NAME = "covers"
_COVER_MAX_PX = 320
_COVER_MIME = "image/jpeg"
_COVER_CONCURRENCY = 4
_NO_COVER_EXT = ".nocover"  # 0-byte marker: this EPUB has no extractable cover

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_RESULT_LIMIT = 5
# difflib ratio on the strict title comparator: "Mistborn: The Final Empire"
# vs "...The Well of Ascension" (same series, different book) scores ~0.6, so
# the bar sits well above that.
_ITUNES_MATCH_THRESHOLD = 0.8
_ITUNES_ARTWORK_SIZE = "5000x5000bb"


def _thumbnail(raw: bytes) -> tuple[bytes, str] | None:
    """(JPEG thumbnail bytes, perceptual-hash hex) or None if `raw` isn't a
    usable image. The pHash is computed from the same decoded image, so it
    costs nothing beyond one small DCT."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((_COVER_MAX_PX, _COVER_MAX_PX))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82, optimize=True)
        return out.getvalue(), str(imagehash.phash(img))
    except Exception:
        return None


def _phash_of_jpg(raw: bytes) -> str | None:
    """pHash of an already-rendered cover JPEG — for backfilling cover_phash
    on files whose thumbnail exists in Drive but predates this column,
    without re-downloading the whole book."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        return str(imagehash.phash(img))
    except Exception:
        return None


def _itunes_cover(title: str, author: str | None) -> bytes | None:
    """Best-effort keyless cover fallback via Apple's iTunes Search API.
    Its ebook results normally carry only a 100x100 thumbnail URL, but
    swapping the size token in that URL for `5000x5000bb` returns the
    full-resolution artwork with no extra request. Returns None on any
    failure or when no result's title is a confident match."""
    term = f"{title} {author}" if author else title
    try:
        resp = httpx.get(
            _ITUNES_SEARCH_URL,
            params={"term": term, "country": "gb", "entity": "ebook", "limit": _ITUNES_RESULT_LIMIT},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (httpx.HTTPError, ValueError):
        return None

    best_url: str | None = None
    best_score = 0.0
    for r in results:
        track, artwork = r.get("trackName"), r.get("artworkUrl100")
        if not track or not artwork:
            continue
        score = title_similarity(track, title)
        if score > best_score:
            best_url, best_score = artwork, score
    if best_url is None or best_score < _ITUNES_MATCH_THRESHOLD:
        return None

    full_url = best_url.replace("100x100bb", _ITUNES_ARTWORK_SIZE)
    try:
        img_resp = httpx.get(full_url, timeout=15.0)
        img_resp.raise_for_status()
        return img_resp.content
    except httpx.HTTPError:
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
    provider: DriveProvider,
    covers_folder_id: str,
    drive_file_id: str,
    filename: str,
    title: str | None = None,
    author: str | None = None,
) -> tuple[str, str | None]:
    """(status, cover_phash) — status is "done" or "nocover"; phash is the
    thumbnail's perceptual hash for a "done" cover, None otherwise. Runs in a
    worker thread with no DB session — the caller writes the hash back after
    the gather."""
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
    if thumb is None and title:
        itunes_raw = _itunes_cover(title, author)
        if itunes_raw is not None:
            thumb = _thumbnail(itunes_raw)
    if thumb is None:
        # Leave a marker so this EPUB isn't re-downloaded on every run just
        # to rediscover it has no usable cover.
        provider.upload_new_file(
            name=f"{drive_file_id}{_NO_COVER_EXT}",
            data=b"",
            parent_id=covers_folder_id,
            mime_type="text/plain",
        )
        return "nocover", None
    thumb_bytes, phash = thumb
    provider.upload_new_file(
        name=f"{drive_file_id}.jpg",
        data=thumb_bytes,
        parent_id=covers_folder_id,
        mime_type=_COVER_MIME,
    )
    return "done", phash


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
    counts = {"done": 0, "nocover": 0, "failed": 0, "remaining": 0, "rehashed": 0}
    if creds is None or not library_folder_id:
        return counts
    try:
        lister = DriveProvider(build_drive_service(creds))
        covers_folder_id = await asyncio.to_thread(
            _ensure_covers_folder, lister, library_folder_id
        )
        # A book is "handled" once it has either a .jpg thumbnail or a
        # .nocover marker. `jpg_ids` also maps drive-id -> the thumbnail's
        # own Drive file id, so an existing cover can be re-hashed in place.
        handled: set[str] = set()
        jpg_ids: dict[str, str] = {}
        for f in await asyncio.to_thread(lister.list_files_in_folder, covers_folder_id):
            name = f["name"]
            if name.endswith(".jpg"):
                drive_id = name[:-4]
                jpg_ids[drive_id] = f["id"]
                handled.add(drive_id)
            elif name.endswith(_NO_COVER_EXT):
                handled.add(name[: -len(_NO_COVER_EXT)])

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        File.drive_file_id,
                        File.filename,
                        File.cover_phash,
                        Book.canonical_title,
                        Author.name,
                    )
                    .join(Book, Book.id == File.book_id)
                    .outerjoin(Author, Author.id == Book.author_id)
                    .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                )
            ).all()

        missing = [(r[0], r[1], r[3], r[4]) for r in rows if r[0] not in handled]
        # Covers that exist but predate the cover_phash column — re-hash from
        # the thumbnail already in Drive, no book download.
        rehash = [r[0] for r in rows if r[2] is None and r[0] in jpg_ids]

        todo = missing if limit is None else missing[:limit]
        counts["remaining"] = len(missing) - len(todo)
        total = len(todo)
        rehash_budget = None if limit is None else max(0, limit - len(todo))
        rehash_todo = rehash if rehash_budget is None else rehash[:rehash_budget]

        sem = asyncio.Semaphore(_COVER_CONCURRENCY)
        hashed: dict[str, str] = {}

        async def run(entry: tuple[str, str, str, str | None]) -> None:
            drive_id, filename, title, author = entry
            async with sem:
                # Fresh provider per file — httplib2 isn't safe to share
                # across the threads asyncio.to_thread hands work to.
                provider = DriveProvider(build_drive_service(creds))
                try:
                    status, phash = await asyncio.to_thread(
                        _make_one, provider, covers_folder_id, drive_id, filename, title, author
                    )
                    counts[status] += 1
                    if phash is not None:
                        hashed[drive_id] = phash
                except Exception:
                    logger.exception("cover generation failed for %s", drive_id)
                    counts["failed"] += 1
                if on_progress is not None:
                    on_progress(counts, total)

        async def rehash_one(drive_id: str) -> None:
            async with sem:
                provider = DriveProvider(build_drive_service(creds))
                try:
                    raw = await asyncio.to_thread(provider.download_file, jpg_ids[drive_id])
                    phash = await asyncio.to_thread(_phash_of_jpg, raw)
                    if phash is not None:
                        hashed[drive_id] = phash
                        counts["rehashed"] += 1
                except Exception:
                    logger.exception("cover re-hash failed for %s", drive_id)

        await asyncio.gather(*(run(d) for d in todo))
        await asyncio.gather(*(rehash_one(d) for d in rehash_todo))

        if hashed:
            async with async_session_factory() as session:
                await session.execute(
                    update(File)
                    .where(File.drive_file_id.in_(list(hashed)))
                    .values(cover_phash=case(hashed, value=File.drive_file_id))
                )
                await session.commit()

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
