import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Author, Book, Identifier, IdentifierType, Series
from app.services.text_match import normalize_title, normalize_words

# Process-wide, not scoped to any one caller: every write path that can
# fuzzy-match-or-create an Author/Series/Book (scan's per-file pipeline,
# review_service.correct()) must serialize against every other one, not just
# against itself. A lock private to a single scan batch only stops two files
# *within that batch* from both creating "J.R.R. Tolkien" — two overlapping
# scan jobs, or a scan racing a human correcting a review, each holding their
# own private lock, would reopen exactly the race this exists to prevent.
_book_write_lock = asyncio.Lock()


def get_book_write_lock() -> asyncio.Lock:
    return _book_write_lock


def reset_book_write_lock() -> None:
    """Test-only. asyncio.Lock binds to the event loop of its first real
    `acquire()`, and pytest-asyncio gives each test function its own loop by
    default — reusing this module-level singleton across tests raises
    "Lock is bound to a different event loop" the moment a second test's
    loop actually acquires it. Call from an autouse fixture between tests;
    production never needs this (the app has exactly one event loop for its
    whole life)."""
    global _book_write_lock
    _book_write_lock = asyncio.Lock()


async def resolve_book(
    session: AsyncSession,
    *,
    title: str,
    author: str | None,
    series: str | None,
    series_number: float | None,
    isbn13: str | None,
    isbn10: str | None,
) -> Book:
    author_row = await _find_or_create_author(session, author) if author else None
    series_row = await _find_or_create_series(session, series) if series else None

    # normalize_title, not exact string equality: different uploads of the
    # same book routinely differ in casing/punctuation/subtitle formatting
    # in the AI-extracted title ("The Hob's Bargain" vs "The Hob's bargain")
    # — each variant must reuse the first-seen canonical row, same as the
    # author/series matching above, or they fragment into separate Book
    # records that then can't be recognized as the same book at all.
    query = select(Book)
    query = (
        query.where(Book.author_id == author_row.id)
        if author_row is not None
        else query.where(Book.author_id.is_(None))
    )
    target_title = normalize_title(title)
    book_row = next(
        (
            b
            for b in (await session.execute(query)).scalars().all()
            if normalize_title(b.canonical_title) == target_title
        ),
        None,
    )

    if book_row is None:
        book_row = Book(
            canonical_title=title,
            author_id=author_row.id if author_row else None,
            series_id=series_row.id if series_row else None,
            series_number=series_number,
        )
        session.add(book_row)
        await session.flush()

    if isbn13:
        await _ensure_identifier(session, book_row.id, IdentifierType.isbn13, isbn13)
    if isbn10:
        await _ensure_identifier(session, book_row.id, IdentifierType.isbn10, isbn10)

    return book_row


async def _find_or_create_author(session: AsyncSession, name: str) -> Author:
    target = normalize_words(name)
    for existing in (await session.execute(select(Author))).scalars().all():
        if normalize_words(existing.name) == target:
            return existing
    row = Author(name=name)
    session.add(row)
    await session.flush()
    return row


async def _find_or_create_series(session: AsyncSession, name: str) -> Series:
    # Word-set match, not exact string equality: the same series shows up
    # phrased differently across providers/AI calls — "Cirque Du Freak (The
    # Saga of Darren Shan)" vs "The Saga of Darren Shan (Cirque Du Freak)"
    # vs the same without punctuation/casing — and each variant must reuse
    # the first-seen canonical row, not fork a new series (and a new Drive
    # folder on organize) every time the wording shifts slightly.
    target = normalize_words(name)
    for existing in (await session.execute(select(Series))).scalars().all():
        if normalize_words(existing.name) == target:
            return existing
    row = Series(name=name)
    session.add(row)
    await session.flush()
    return row


async def _ensure_identifier(
    session: AsyncSession, book_id: int, type_: IdentifierType, value: str
) -> None:
    existing = await session.execute(
        select(Identifier).where(
            Identifier.book_id == book_id,
            Identifier.type == type_,
            Identifier.value == value,
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(Identifier(book_id=book_id, type=type_, value=value, source="identification"))
