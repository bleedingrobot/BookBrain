import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Author, Book, Identifier, IdentifierType, Series, SeriesAlias
from app.services.text_match import (
    normalize_person_name,
    normalize_title_strict,
    normalize_words,
    person_sort_name,
)

_ARTICLES = frozenset({"the", "a", "an"})


def _series_match_key(name: str | None) -> frozenset[str]:
    """Word set with a leading article dropped so "The Stormlight Archive" and
    "Stormlight Archive" don't fork on the first scan (prompts/15 Stage J)."""
    return normalize_words(name) - _ARTICLES

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

    # normalize_title_strict, not exact string equality and not the loose
    # normalize_title: different uploads of the same book routinely differ in
    # casing/punctuation ("The Hob's Bargain" vs "The Hob's bargain") — each
    # variant must reuse the first-seen canonical row, or they fragment into
    # separate Book records. But the *loose* normalize_title strips a ':'/';'
    # subtitle, which for the very common "<Series>: <Book Title>" title
    # format ("Mistborn: The Final Empire" / "Mistborn: The Well of
    # Ascension", both by the same author) collapses two genuinely different
    # books onto one row — from there detect_same_book_duplicates flags the
    # "extra" as a duplicate and the bulk clear can trash it. The strict
    # normalizer keeps the full title so those stay distinct.
    #
    # Accepted trade-off: "The Hobbit" and "The Hobbit: There and Back Again"
    # now resolve to two rows rather than one — a lesser harm (two visible
    # records, nothing hidden or trashed) than merging two different books.
    query = select(Book)
    query = (
        query.where(Book.author_id == author_row.id)
        if author_row is not None
        else query.where(Book.author_id.is_(None))
    )
    target_title = normalize_title_strict(title)
    book_row = next(
        (
            b
            for b in (await session.execute(query)).scalars().all()
            if normalize_title_strict(b.canonical_title) == target_title
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
    # prompts/15 Stage J: match on normalize_person_name so "J.R.R. Tolkien",
    # "J. R. R. Tolkien" and "Tolkien, J.R.R." reuse one row instead of forking
    # three. Fall back to the old word-set match for a name that normalises to
    # nothing (so junk names don't all collapse onto one empty key).
    key = normalize_person_name(name)
    fallback = normalize_words(name)
    for existing in (await session.execute(select(Author))).scalars().all():
        matched = (
            normalize_person_name(existing.name) == key
            if key
            else normalize_words(existing.name) == fallback
        )
        if matched:
            if existing.sort_name is None:
                existing.sort_name = person_sort_name(existing.name) or None
            return existing
    # Display name is kept verbatim (first-seen) — collaboration credits vary
    # too much to safely rewrite ("… & Gardner Dozois (editors)" must survive).
    # The match key above already collapses spelling variants of one author.
    row = Author(name=name, sort_name=person_sort_name(name) or None)
    session.add(row)
    await session.flush()
    return row


async def resolve_series(session: AsyncSession, name: str | None) -> Series | None:
    """Public wrapper — a hand correction sets a book's series directly
    (resolve_book only sets series on newly-created rows, not existing ones)."""
    return await _find_or_create_series(session, name) if name else None


async def _find_or_create_series(session: AsyncSession, name: str) -> Series:
    # Word-set match, not exact string equality: the same series shows up
    # phrased differently across providers/AI calls — "Cirque Du Freak (The
    # Saga of Darren Shan)" vs "The Saga of Darren Shan (Cirque Du Freak)"
    # vs the same without punctuation/casing — and each variant must reuse
    # the first-seen canonical row, not fork a new series (and a new Drive
    # folder on organize) every time the wording shifts slightly.
    # prompts/15 Stage J: match ignoring a leading article ("The Stormlight
    # Archive" == "Stormlight Archive"), and consult SeriesAlias (populated by
    # series-merge) so a re-fork of a name already merged away can't happen.
    target = _series_match_key(name)
    exact = normalize_words(name)
    alias = (
        await session.execute(
            select(SeriesAlias).where(SeriesAlias.alias == name.strip())
        )
    ).scalar_one_or_none()
    if alias is not None:
        found = await session.get(Series, alias.series_id)
        if found is not None:
            return found
    for existing in (await session.execute(select(Series))).scalars().all():
        if _series_match_key(existing.name) == target or normalize_words(existing.name) == exact:
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
