"""One-off repair for books that a too-loose title match merged together.

Before `book_repository.resolve_book` was tightened to `normalize_title_strict`,
two genuinely different same-author books whose titles shared a pre-colon prefix
("Mistborn: The Final Empire" / "Mistborn: The Well of Ascension") resolved to a
single `Book` row. `run_rebuild` will not fix those — it skips files already in
`files` by `drive_file_id` — so this pass re-derives each file's own title from
its stored `metadata_sources` and splits any file whose strict-title key differs
from its book's onto a fresh `Book` row.

Reads the DB only — no Drive, no Anthropic calls.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    Book,
    File,
    FileStatus,
    MetadataSource,
)
from app.schemas.library_audit import TitleMergeRepairResult
from app.services.book_repository import get_book_write_lock, resolve_book
from app.services.text_match import normalize_title_strict

_TITLE_SOURCES = {"epub", "comic"}


def _own_title(file_row: File) -> str | None:
    """The title this file's own metadata claims, independent of whatever
    `Book` row it currently points at. epub/comic-embedded title first, then
    the filename as a weak fallback."""
    embedded = next(
        (
            s.value
            for s in file_row.metadata_sources
            if s.field_name == "title" and s.source in _TITLE_SOURCES and s.value
        ),
        None,
    )
    if embedded:
        return embedded
    stem = file_row.filename.rsplit(".", 1)[0].strip()
    return stem or None


async def repair_title_merges(session: AsyncSession) -> TitleMergeRepairResult:
    books = (
        (
            await session.execute(
                select(Book)
                .options(
                    selectinload(Book.author),
                    selectinload(Book.series),
                    selectinload(Book.files).selectinload(File.metadata_sources),
                )
            )
        )
        .scalars()
        .all()
    )

    books_split = 0
    files_moved = 0

    for book in books:
        files = [f for f in book.files if f.book_id == book.id]
        if len(files) <= 1:
            continue

        # Same tie-break as duplicate_service.detect_same_book_duplicates —
        # the primary keeps the existing row, the rest are re-checked.
        files.sort(key=lambda f: (-(f.quality_score or 0), f.discovered_at, f.id))
        primary, *rest = files
        primary_key = normalize_title_strict(book.canonical_title)

        split_here = False
        for extra in rest:
            own = _own_title(extra)
            if not own or normalize_title_strict(own) == primary_key:
                continue

            async with get_book_write_lock():
                new_book = await resolve_book(
                    session,
                    title=own,
                    author=book.author.name if book.author else None,
                    series=book.series.name if book.series else None,
                    series_number=book.series_number,
                    isbn13=None,
                    isbn10=None,
                )
            if new_book.id == book.id:
                continue

            extra.book_id = new_book.id
            # A file the false merge got flagged as a duplicate dropped out of
            # organize and the library index — send it back through the
            # pipeline. An untouched-status file just had its book_id corrected;
            # leave it where it is (the index reads through book_id live).
            if extra.status == FileStatus.duplicate:
                extra.status = FileStatus.inbox
                extra.status_reason = None
            files_moved += 1
            split_here = True

        if split_here:
            books_split += 1

    await session.commit()
    return TitleMergeRepairResult(books_split=books_split, files_moved=files_moved)
