from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    AIDecision,
    Book,
    BookCandidate,
    File,
    FileStatus,
    FileStatusReason,
    MetadataSource,
    Operation,
    Review,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup
from app.services.book_repository import get_book_write_lock, resolve_book

_ACTIVE_STATUSES = (FileStatus.inbox, FileStatus.review, FileStatus.organised)
_TITLE_SOURCES = {"epub", "comic"}


class DuplicateNotClearableError(Exception):
    """The file isn't a status=duplicate row, or (for the bulk path) it's a
    same_book row that must be handled per-row, not swept."""


async def detect_same_book_duplicates(session: AsyncSession) -> int:
    """Flags every file beyond the best copy of the same book as a
    duplicate. sha256-based detection (the scan-time check) only catches
    byte-identical re-uploads; a different edition or re-conversion of the
    same book has different bytes entirely and sails right past it, while
    still cluttering the same Drive folder with 2-3 copies of one title.
    Primary is chosen by quality_score, then the oldest (first discovered)
    file wins a tie — assumed to have been trusted/organized longest."""
    files = (
        (
            await session.execute(
                select(File).where(File.book_id.is_not(None), File.status.in_(_ACTIVE_STATUSES))
            )
        )
        .scalars()
        .all()
    )

    groups: dict[int, list[File]] = defaultdict(list)
    for f in files:
        groups[f.book_id].append(f)

    flagged = 0
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda f: (-(f.quality_score or 0), f.discovered_at, f.id))
        for extra in group[1:]:
            extra.status = FileStatus.duplicate
            extra.status_reason = FileStatusReason.same_book
            flagged += 1

    return flagged


async def list_duplicate_groups(session: AsyncSession) -> list[DuplicateGroup]:
    duplicates = (
        (await session.execute(select(File).where(File.status == FileStatus.duplicate)))
        .scalars()
        .all()
    )

    groups: list[DuplicateGroup] = []
    for dup in duplicates:
        primary = (
            (
                await session.execute(
                    select(File).where(File.sha256 == dup.sha256, File.status != FileStatus.duplicate)
                )
            )
            .scalars()
            .first()
        )
        # sha256 differs for a same_book duplicate by definition (different
        # bytes, same book) — the exact-content lookup above can't find it,
        # so fall back to the book it was actually flagged against.
        if primary is None and dup.book_id is not None:
            primary = (
                (
                    await session.execute(
                        select(File).where(
                            File.book_id == dup.book_id, File.status != FileStatus.duplicate
                        )
                    )
                )
                .scalars()
                .first()
            )
        groups.append(
            DuplicateGroup(
                duplicate_file_id=dup.id,
                duplicate_filename=dup.filename,
                quality_score=dup.quality_score,
                primary_file_id=primary.id if primary else None,
                primary_filename=primary.filename if primary else None,
                status_reason=dup.status_reason.value if dup.status_reason else None,
                sha256=dup.sha256,
            )
        )
    return groups


async def _trash_and_delete(session: AsyncSession, provider: DriveProvider, dup: File) -> bool:
    try:
        provider.trash_file(dup.drive_file_id)
    except Exception:
        return False
    for model in (Review, Operation, AIDecision, BookCandidate, MetadataSource):
        await session.execute(delete(model).where(model.file_id == dup.id))
    await session.execute(delete(File).where(File.id == dup.id))
    return True


async def clear_duplicates(session: AsyncSession, provider: DriveProvider) -> ClearDuplicatesResult:
    """Trashes each exact-content duplicate's Drive file (recoverable via
    Drive's own Trash, not a permanent delete) and drops its DB record. Never
    touches the primary — only rows with status=duplicate are candidates —
    and never touches same_book rows: those are a *resolved-book* match, not a
    byte match, so a bad identification could put a real, different book in
    that bucket. They're cleared one at a time through clear_one_duplicate
    after the user has seen the title."""
    duplicates = (
        (
            await session.execute(
                select(File).where(
                    File.status == FileStatus.duplicate,
                    File.status_reason.is_distinct_from(FileStatusReason.same_book),
                )
            )
        )
        .scalars()
        .all()
    )

    cleared = 0
    failed = 0
    for dup in duplicates:
        if await _trash_and_delete(session, provider, dup):
            cleared += 1
        else:
            failed += 1

    await session.commit()
    return ClearDuplicatesResult(cleared=cleared, failed=failed)


async def clear_one_duplicate(
    session: AsyncSession, provider: DriveProvider, file_id: int
) -> ClearDuplicatesResult:
    """Per-row trash for a single status=duplicate file (the only path that
    clears a same_book row)."""
    dup = (
        await session.execute(select(File).where(File.id == file_id))
    ).scalar_one_or_none()
    if dup is None or dup.status != FileStatus.duplicate:
        raise DuplicateNotClearableError(f"file {file_id} is not a duplicate")

    ok = await _trash_and_delete(session, provider, dup)
    await session.commit()
    return ClearDuplicatesResult(cleared=1 if ok else 0, failed=0 if ok else 1)


async def unflag_duplicate(session: AsyncSession, file_id: int) -> None:
    """"Not a duplicate" — the file was flagged same_book against a book it
    doesn't actually belong to (a stale false merge, or a bad identification).
    Re-derive its own title and split it onto a fresh Book row, then send it
    back through the pipeline. No AI, no Drive."""
    file_row = (
        await session.execute(
            select(File)
            .where(File.id == file_id)
            .options(
                selectinload(File.metadata_sources),
                selectinload(File.book).selectinload(Book.author),
                selectinload(File.book).selectinload(Book.series),
            )
        )
    ).scalar_one_or_none()
    if file_row is None or file_row.status != FileStatus.duplicate:
        raise DuplicateNotClearableError(f"file {file_id} is not a flagged duplicate")

    book = file_row.book
    own_title = next(
        (
            s.value
            for s in file_row.metadata_sources
            if s.field_name == "title" and s.source in _TITLE_SOURCES and s.value
        ),
        None,
    ) or (file_row.filename.rsplit(".", 1)[0].strip() or file_row.filename)

    async with get_book_write_lock():
        new_book = await resolve_book(
            session,
            title=own_title,
            author=book.author.name if book and book.author else None,
            series=book.series.name if book and book.series else None,
            series_number=book.series_number if book else None,
            isbn13=None,
            isbn10=None,
        )
    file_row.book_id = new_book.id
    file_row.status = FileStatus.inbox
    file_row.status_reason = None
    await session.commit()
