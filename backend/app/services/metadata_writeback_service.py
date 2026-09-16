"""Writes BookBrain's resolved title / author / series (and, when the file
has none, a cover) into each organised `.epub`'s embedded OPF metadata, so a
Kobo / Calibre / any other reader shows what BookBrain resolved instead of
the messy original the misidentification came from.

Deliberately a **separate opt-in pass** (a Library-page button + bulk
endpoint), not a step wired into every organize — lower blast radius, and it
only needs to sweep the ~2200-book library once, then pick up individual
books as their metadata changes.

## The sha256 gotcha (SPEC §1)

`files.sha256` keys exact-duplicate detection *and* sticky corrections.
Rewriting the epub changes that hash. Handled by:

* stashing the pre-rewrite hash in `files.original_sha256` and matching it
  too in `_find_primary_by_sha256` / sticky-correction lookup — a re-upload
  of the pristine original still resolves to the same book and inherits any
  human correction;
* keeping `drive_file_id` stable (in-place `update_file_content`), so a
  rebuild recognises the file by id and never re-identifies it;
* `files.embedded_metadata_key` — a re-run skips a file already stamped
  with its current resolved metadata, so no repeated hash churn.

The rewrite logs a `write_metadata` `operations` row (Activity log) and is
**not app-undoable** — BookBrain doesn't keep the original bytes. Drive's
own file revision history is the only fallback (kept ~30 days).
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import (
    Book,
    File,
    FileStatus,
    Operation,
    OperationAction,
    OperationStatus,
)
from app.providers.drive.client import EPUB_MIME_TYPE, build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.errors import EpubWriteError
from app.providers.epub.parser import extract_cover
from app.providers.epub.writer import EpubMetadata, write_metadata
from app.schemas.metadata_writeback import (
    MetadataWritebackJobState,
    MetadataWritebackJobStatus,
)

logger = logging.getLogger(__name__)

_EPUB_EXTENSIONS = (".epub", ".kpub")
_CONCURRENCY = 4
_KEY_SEP = "\x1f"

# Serializes this service's own SQLite writes in-process — same reason
# OrganizeService has one (a slow fsync on Windows can make one commit hold
# the write lock long enough that concurrent commits exhaust busy_timeout).
# conftest.py resets it per test (a Lock binds to its first acquire's loop).
_write_lock = asyncio.Lock()


def reset_write_lock() -> None:
    """Test-only. See conftest.py / the OrganizeService equivalent."""
    global _write_lock
    _write_lock = asyncio.Lock()


def _is_epub(filename: str) -> bool:
    return filename.lower().endswith(_EPUB_EXTENSIONS)


def _metadata_key(book: Book) -> str:
    return _KEY_SEP.join(
        [
            book.canonical_title or "",
            book.author.name if book.author else "",
            book.series.name if book.series else "",
            "" if book.series_number is None else f"{book.series_number:g}",
        ]
    )


def _epub_metadata(book: Book) -> EpubMetadata:
    return EpubMetadata(
        title=book.canonical_title,
        author=book.author.name if book.author else None,
        series=book.series.name if book.series else None,
        series_number=book.series_number,
    )


def _operation_reason(book: Book) -> str:
    parts = [book.canonical_title]
    if book.author:
        parts.append(book.author.name)
    if book.series:
        s = book.series.name
        if book.series_number is not None:
            s += f" #{book.series_number:g}"
        parts.append(s)
    return "embedded metadata → " + " · ".join(parts)


def _covers_folder_index(provider: DriveProvider, library_folder_id: str) -> dict[str, str]:
    """`{drive_file_id: covers/<id>.jpg file id}` — the thumbnails
    cover_service already generated, used as the cover source for epubs that
    embed none. One listing call up front; the bytes are pulled lazily."""
    covers = next(
        (f for f in provider.list_folders(library_folder_id) if f["name"] == "covers"), None
    )
    if covers is None:
        return {}
    out: dict[str, str] = {}
    for f in provider.list_files_in_folder(covers["id"]):
        name = f["name"]
        if name.endswith(".jpg"):
            out[name.removesuffix(".jpg")] = f["id"]
    return out


def _write_one(
    provider: DriveProvider,
    *,
    drive_file_id: str,
    meta: EpubMetadata,
    cover_source_id: str | None,
) -> tuple[str, bytes] | str:
    """Pure-ish worker (all I/O through `provider`): returns `"skipped"` if
    the rewrite is a no-op, else `(new_sha256, new_bytes)`. Raises
    EpubWriteError if the file can't be rewritten safely."""
    settings = get_settings()
    raw = provider.download_file(drive_file_id)

    cover_bytes: bytes | None = None
    if cover_source_id is not None:
        has_cover = (
            extract_cover(
                raw,
                max_entry_bytes=settings.epub_max_entry_bytes,
                max_total_bytes=settings.epub_max_total_bytes,
                max_entries=settings.epub_max_entries,
            )
            is not None
        )
        if not has_cover:
            cover_bytes = provider.download_file(cover_source_id)

    new_bytes = write_metadata(raw, meta, cover_bytes)
    if new_bytes == raw:
        return "skipped"
    return hashlib.sha256(new_bytes).hexdigest(), new_bytes


async def backfill_embedded_metadata(
    creds: Credentials | None,
    library_folder_id: str | None,
    *,
    dry_run: bool,
    limit: int | None = None,
    on_progress: Callable[[dict[str, int], int], None] | None = None,
    provider_factory: Callable[[], DriveProvider] | None = None,
) -> dict[str, int]:
    """Rewrite embedded metadata for every organised epub whose file doesn't
    already carry its resolved title/author/series. `dry_run` logs what
    *would* change (a `write_metadata` dry-run `operations` row per file)
    and touches neither Drive nor any hash. Best-effort — never raises into
    the caller."""
    counts = {"updated": 0, "skipped": 0, "failed": 0, "remaining": 0}
    if not dry_run and (creds is None and provider_factory is None):
        return counts

    build_provider: Callable[[], DriveProvider] = provider_factory or (
        lambda: DriveProvider(build_drive_service(creds))
    )

    try:
        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(File)
                        .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                        .options(
                            selectinload(File.book).selectinload(Book.author),
                            selectinload(File.book).selectinload(Book.series),
                        )
                    )
                )
                .scalars()
                .all()
            )

        work: list[int] = []
        for f in rows:
            if not _is_epub(f.filename) or f.book is None:
                continue
            if f.embedded_metadata_key == _metadata_key(f.book):
                counts["skipped"] += 1
            else:
                work.append(f.id)

        todo = work if limit is None else work[:limit]
        counts["remaining"] = len(work) - len(todo)
        total = len(todo)
        if total == 0:
            return counts

        covers_index: dict[str, str] = {}
        if not dry_run and library_folder_id:
            try:
                covers_index = await asyncio.to_thread(
                    _covers_folder_index, build_provider(), library_folder_id
                )
            except Exception:
                logger.exception("metadata writeback: could not list covers/ folder")

        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(file_id: int) -> None:
            async with sem:
                try:
                    await _process_file(
                        file_id,
                        dry_run=dry_run,
                        build_provider=build_provider,
                        covers_index=covers_index,
                        counts=counts,
                    )
                except EpubWriteError as exc:
                    logger.warning("metadata writeback: skipped file %s — %s", file_id, exc)
                    counts["failed"] += 1
                except Exception:
                    logger.exception("metadata writeback failed for file %s", file_id)
                    counts["failed"] += 1
                if on_progress is not None:
                    on_progress(counts, total)

        await asyncio.gather(*(one(fid) for fid in todo))
        logger.info("metadata writeback pass (dry_run=%s): %s", dry_run, counts)
    except Exception:
        logger.exception("metadata writeback pass failed")

    return counts


async def _process_file(
    file_id: int,
    *,
    dry_run: bool,
    build_provider: Callable[[], DriveProvider],
    covers_index: dict[str, str],
    counts: dict[str, int],
) -> None:
    async with async_session_factory() as session:
        file_row = (
            await session.execute(
                select(File)
                .where(File.id == file_id)
                .options(
                    selectinload(File.book).selectinload(Book.author),
                    selectinload(File.book).selectinload(Book.series),
                )
            )
        ).scalar_one_or_none()
        if file_row is None or file_row.book is None or file_row.status != FileStatus.organised:
            return
        book = file_row.book
        key = _metadata_key(book)
        if file_row.embedded_metadata_key == key:
            counts["skipped"] += 1
            return
        drive_file_id = file_row.drive_file_id
        filename = file_row.filename
        meta = _epub_metadata(book)
        reason = _operation_reason(book)

    if dry_run:
        async with _write_lock, async_session_factory() as session:
            session.add(
                Operation(
                    file_id=file_id,
                    action=OperationAction.write_metadata,
                    original_name=filename,
                    new_name=filename,
                    reason=reason,
                    status=OperationStatus.done,
                    dry_run=True,
                )
            )
            await session.commit()
        counts["updated"] += 1
        return

    provider = build_provider()
    result = await asyncio.to_thread(
        _write_one,
        provider,
        drive_file_id=drive_file_id,
        meta=meta,
        cover_source_id=covers_index.get(drive_file_id),
    )

    if result == "skipped":
        # The rewrite would be a byte-for-byte no-op — still record the key
        # so this file isn't downloaded again on the next pass.
        async with _write_lock, async_session_factory() as session:
            fr = await session.get(File, file_id)
            if fr is not None:
                fr.embedded_metadata_key = key
                await session.commit()
        counts["skipped"] += 1
        return

    new_sha256, new_bytes = result
    await asyncio.to_thread(
        provider.update_file_content,
        drive_file_id,
        new_name=filename,
        data=new_bytes,
        mime_type=EPUB_MIME_TYPE,
    )

    async with _write_lock, async_session_factory() as session:
        fr = await session.get(File, file_id)
        if fr is None:
            return
        if fr.original_sha256 is None:
            fr.original_sha256 = fr.sha256
        fr.sha256 = new_sha256
        fr.size_bytes = len(new_bytes)
        fr.embedded_metadata_key = key
        session.add(
            Operation(
                file_id=file_id,
                action=OperationAction.write_metadata,
                original_name=filename,
                new_name=filename,
                reason=reason,
                status=OperationStatus.done,
                dry_run=False,
            )
        )
        await session.commit()
    counts["updated"] += 1


class MetadataWritebackService:
    def __init__(self) -> None:
        self._jobs: dict[str, MetadataWritebackJobStatus] = {}

    def create_job(self, *, dry_run: bool) -> MetadataWritebackJobStatus:
        job_id = str(uuid.uuid4())
        status = MetadataWritebackJobStatus(
            job_id=job_id, status=MetadataWritebackJobState.running, dry_run=dry_run
        )
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> MetadataWritebackJobStatus | None:
        return self._jobs.get(job_id)

    async def run(
        self,
        job_id: str,
        creds: Credentials,
        library_folder_id: str | None,
        *,
        dry_run: bool,
    ) -> None:
        def progress(counts: dict[str, int], total: int) -> None:
            self._jobs[job_id] = MetadataWritebackJobStatus(
                job_id=job_id,
                status=MetadataWritebackJobState.running,
                dry_run=dry_run,
                updated=counts["updated"],
                skipped=counts["skipped"],
                failed=counts["failed"],
                remaining=total - counts["updated"] - counts["failed"],
            )

        counts = await backfill_embedded_metadata(
            creds, library_folder_id, dry_run=dry_run, on_progress=progress
        )
        self._jobs[job_id] = MetadataWritebackJobStatus(
            job_id=job_id,
            status=MetadataWritebackJobState.done,
            dry_run=dry_run,
            updated=counts["updated"],
            skipped=counts["skipped"],
            failed=counts["failed"],
            remaining=counts["remaining"],
        )


_service = MetadataWritebackService()


def get_metadata_writeback_service() -> MetadataWritebackService:
    return _service
