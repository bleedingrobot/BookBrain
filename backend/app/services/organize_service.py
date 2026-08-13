import asyncio
import logging
import re
import uuid
from collections import defaultdict

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings_keys import ORGANIZE_DRY_RUN
from app.data.db import async_session_factory
from app.data.models import Book, File, FileStatus, Operation, OperationAction, OperationStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.organize import OrganizeFailure, OrganizeJobState, OrganizeJobStatus

logger = logging.getLogger(__name__)


async def get_organize_dry_run(settings_repo: SettingsRepository) -> bool:
    """SPEC.md §1: dry-run defaults true until explicitly flipped (Milestone
    6a gate) — a missing setting or anything other than the literal string
    "false" means dry-run. Centralized so the three call sites that need
    this can't drift on what "missing" means."""
    return (await settings_repo.get(ORGANIZE_DRY_RUN)) != "false"

# Comma is included alongside the OS-reserved characters because it's also
# the delimiter build_target_path joins title/author/series/part with below
# — a title or author containing one (e.g. "Title: Part One, Volume 2")
# would otherwise land in a filename indistinguishable from an extra field,
# which the library-viewer's filename parser (a plain comma-split, with no
# backend to ask) can't disambiguate.
_INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|,]')
_ORGANIZE_CONCURRENCY = 6


def _sanitize(segment: str) -> str:
    cleaned = _INVALID_CHARS_RE.sub(" ", segment).strip()
    return cleaned or "Untitled"


def build_target_path(
    *, title: str, author_name: str | None, series_name: str | None, series_number: float | None
) -> tuple[list[str], str]:
    """Returns (folder_segments, filename). `folder_segments` is relative to
    the configured library root — e.g. ["Frank Herbert", "Dune Chronicles"].
    `filename` is "Author, Title, Series, Part N.epub", trimmed down to
    whichever of those fields are actually known."""
    folders: list[str] = []
    if author_name:
        folders.append(_sanitize(author_name))
    if series_name:
        folders.append(_sanitize(series_name))

    parts: list[str] = []
    if author_name:
        parts.append(_sanitize(author_name))
    parts.append(_sanitize(title))
    if series_name:
        parts.append(_sanitize(series_name))
        if series_number is not None:
            parts.append(f"{series_number:g}")
    filename = ", ".join(parts) + ".epub"

    return folders, filename


class FolderPathCache:
    """Find-or-create for a Drive folder path. Without this, two files
    organizing concurrently into the same not-yet-existing folder (e.g. two
    books by an author with no folder yet) can both see "not found" and
    both create it — Drive doesn't enforce folder name uniqueness, so
    that's a silent duplicate-folder bug, not an error. A cache hit (the
    common case once a path exists) never touches any lock at all; only a
    genuine miss serializes, and only against other misses resolving that
    *same* segment — a per-partial-path lock, not one lock for the whole
    cache, so a stuck/slow Drive call resolving one author's folder can't
    stall every other file's organize, only files that happen to need that
    same not-yet-cached folder.

    A single process-wide instance (get_folder_path_cache()) is what
    production code actually uses — one per organize *batch* only stopped
    the race within that batch; a scan's auto-organize overlapping a
    manually-triggered organize (each with its own private cache) could
    still both miss and both create the same folder. The cache key includes
    root_id so a library-folder reconfiguration mid-run can't return a
    folder ID that belongs under a different root."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, ...], str] = {}
        self._locks: dict[tuple[str, ...], asyncio.Lock] = defaultdict(asyncio.Lock)

    def clear(self) -> None:
        """Test-only reset. Also replaces the locks: asyncio.Lock binds to
        the event loop of its first real acquisition, and pytest-asyncio
        gives each test its own loop by default, so reusing this singleton
        across tests raises "bound to a different event loop" the moment a
        second test's loop hits a genuine cache miss for a path an earlier
        test's loop already touched."""
        self._cache.clear()
        self._locks = defaultdict(asyncio.Lock)

    async def resolve(self, provider: DriveProvider, root_id: str, segments: list[str]) -> str:
        key = (root_id, *segments)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        current = root_id
        for i, segment in enumerate(segments):
            partial_key = (root_id, *segments[: i + 1])
            partial = self._cache.get(partial_key)
            if partial is not None:
                current = partial
                continue

            async with self._locks[partial_key]:
                # Re-check under the lock: another coroutine may have
                # resolved this exact segment while this one was waiting.
                partial = self._cache.get(partial_key)
                if partial is not None:
                    current = partial
                    continue
                folders = await asyncio.to_thread(provider.list_folders, current)
                match = next((f for f in folders if f["name"] == segment), None)
                current = (
                    match["id"]
                    if match
                    else (await asyncio.to_thread(provider.create_folder, segment, parent_id=current))["id"]
                )
                self._cache[partial_key] = current

        self._cache[key] = current
        return current


_folder_path_cache = FolderPathCache()


def get_folder_path_cache() -> FolderPathCache:
    return _folder_path_cache


class OrganizeService:
    """SPEC.md's Milestone 6/6a: builds move/rename/logging, wired only to a
    dry-run flag. Dry runs never touch Drive — the target path is logged as
    a display string, not a real folder ID, since no folder is created to
    resolve one. Only a real (non-dry-run) run creates folders, moves the
    file, and flips `files.status` to `organised`.

    Eligible files are organized concurrently (bounded by
    _ORGANIZE_CONCURRENCY) — each gets its own DB session (a single
    AsyncSession isn't safe for concurrent use from multiple coroutines),
    and Drive calls run via asyncio.to_thread since googleapiclient is
    synchronous. FolderPathCache is the only shared, lock-protected state;
    everything else about one file's organize is independent of every
    other file's."""

    def __init__(self) -> None:
        self._jobs: dict[str, OrganizeJobStatus] = {}

    def create_job(self) -> OrganizeJobStatus:
        job_id = str(uuid.uuid4())
        status = OrganizeJobStatus(job_id=job_id, status=OrganizeJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> OrganizeJobStatus | None:
        return self._jobs.get(job_id)

    async def run_organize(
        self,
        job_id: str,
        creds: Credentials | None,
        library_root_folder_id: str | None,
        dry_run: bool,
    ) -> None:
        provider = DriveProvider(build_drive_service(creds)) if not dry_run and creds else None
        counts, failures = await self.organize_eligible_files(
            provider=provider, library_root_folder_id=library_root_folder_id, dry_run=dry_run
        )

        detail = (
            f"{counts['organized']} organized"
            f"{' (dry run — nothing changed in Drive)' if dry_run else ''}, "
            f"{counts['failed']} failed"
        )
        self._jobs[job_id] = OrganizeJobStatus(
            job_id=job_id, status=OrganizeJobState.done, detail=detail, failures=failures
        )

    async def organize_eligible_files(
        self,
        *,
        provider: DriveProvider | None,
        library_root_folder_id: str | None,
        dry_run: bool,
    ) -> tuple[dict[str, int], list[OrganizeFailure]]:
        """The shared core behind both the manual "Organize" button
        (run_organize) and ScanService's auto-organize-after-scan. Returns
        ({"organized": n, "failed": n}, failures) rather than writing job
        status itself, so callers with different reporting needs (a
        standalone job vs. one line in a scan's summary) don't have to fake
        a job_id."""
        counts = {"organized": 0, "failed": 0}
        failures: list[OrganizeFailure] = []

        async with async_session_factory() as session:
            result = await session.execute(
                select(File.id).where(File.status == FileStatus.inbox, File.book_id.is_not(None))
            )
            file_ids = [row[0] for row in result.all()]

        if not file_ids:
            return counts, failures

        # The process-wide singleton, not a fresh cache per call — this is
        # what actually closes the cross-job race: a scan's auto-organize and
        # a manually-triggered organize each used to get their own private
        # cache, so the exact "two coroutines miss and both create the
        # folder" bug the cache exists to prevent could still happen between
        # two overlapping jobs, just not within a single one.
        folder_cache = get_folder_path_cache() if provider is not None else None
        semaphore = asyncio.Semaphore(_ORGANIZE_CONCURRENCY)

        async def organize_one(file_id: int) -> None:
            filename = f"file {file_id}"  # overwritten once the row is fetched; a fallback for a failure before that
            async with semaphore:
                try:
                    async with async_session_factory() as session:
                        # A plain select(), not session.get(...,
                        # options=...) — get() only applies loader options
                        # when it actually issues a query; if this file_id
                        # is already in the session's identity map (a fresh
                        # session in production, but session reuse is
                        # exactly what tests need to fake to share one
                        # in-memory DB), it silently returns the cached
                        # object with book/author/series still unloaded,
                        # and the lazy-load that follows fails outright in
                        # an async context.
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
                        # Already handled by a previous iteration/concurrent
                        # task, or its status changed underneath us — not a
                        # failure, just nothing left to do.
                        if file_row is None or file_row.status != FileStatus.inbox:
                            return
                        filename = file_row.filename
                        await self._organize_file(
                            session,
                            file_row,
                            provider=provider,
                            library_root_folder_id=library_root_folder_id,
                            dry_run=dry_run,
                            folder_cache=folder_cache,
                        )
                    counts["organized"] += 1
                except Exception as exc:
                    logger.exception("organize failed for %s", filename)
                    counts["failed"] += 1
                    failures.append(OrganizeFailure(filename=filename, reason=str(exc)))

        await asyncio.gather(*(organize_one(file_id) for file_id in file_ids))
        return counts, failures

    async def _organize_file(
        self,
        session: AsyncSession,
        file_row: File,
        *,
        provider: DriveProvider | None,
        library_root_folder_id: str | None,
        dry_run: bool,
        folder_cache: FolderPathCache | None = None,
    ) -> Operation:
        book = file_row.book
        author_name = book.author.name if book.author else None
        series_name = book.series.name if book.series else None
        folders, filename = build_target_path(
            title=book.canonical_title,
            author_name=author_name,
            series_name=series_name,
            series_number=book.series_number,
        )

        if dry_run:
            operation = Operation(
                file_id=file_row.id,
                action=OperationAction.move_and_rename,
                original_name=file_row.filename,
                original_parent_id=file_row.drive_parent_id,
                new_name=filename,
                new_parent_id="/".join(folders) or "(library root)",
                status=OperationStatus.done,
                dry_run=True,
                reason="dry run — no Drive changes made",
            )
            session.add(operation)
            await session.commit()
            return operation

        if provider is None or library_root_folder_id is None:
            raise RuntimeError("organize requires Drive credentials and a configured library folder")

        cache = folder_cache or get_folder_path_cache()
        target_folder_id = await cache.resolve(provider, library_root_folder_id, folders)
        await asyncio.to_thread(
            provider.move_and_rename,
            file_row.drive_file_id,
            old_parent_id=file_row.drive_parent_id,
            new_parent_id=target_folder_id,
            new_name=filename,
        )

        operation = Operation(
            file_id=file_row.id,
            action=OperationAction.move_and_rename,
            original_name=file_row.filename,
            original_parent_id=file_row.drive_parent_id,
            new_name=filename,
            new_parent_id=target_folder_id,
            status=OperationStatus.done,
            dry_run=False,
        )
        session.add(operation)

        file_row.filename = filename
        file_row.drive_parent_id = target_folder_id
        file_row.status = FileStatus.organised

        await session.commit()
        return operation


_organize_service = OrganizeService()


def get_organize_service() -> OrganizeService:
    return _organize_service
