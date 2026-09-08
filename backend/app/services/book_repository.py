import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Author, Book, Identifier, IdentifierType, Series, SeriesAlias
from app.services.text_match import (
    is_collaboration,
    normalize_person_name,
    normalize_title_strict,
    normalize_words,
    person_sort_name,
    primary_author_name,
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


# A scan/rebuild batch passes one of these so the author/series find-or-create
# doesn't reload every row from the DB per file. Purely an optimisation: a miss
# always falls back to the full scan, so correctness never depends on it. Keyed
# "author:<normalize_person_name>" / "series:<sorted match-key words>" -> row id.
# Built once at batch start (build_match_cache) and updated in-memory whenever
# _find_or_create_* creates a row — all *under the write lock*
# (book_repository.get_book_write_lock), so no coroutine sees a half-populated
# entry and the concurrent-create race stays closed. One-off callers (a hand
# /correct, sticky resolution) pass None and hit the full scan every time.
MatchCache = dict[str, int]


async def build_match_cache(session: AsyncSession) -> MatchCache:
    """One `select(id, name)` per table, folded into the same match keys
    `_find_or_create_*` use. Call once per scan/rebuild batch, under the write
    lock, before processing files."""
    cache: MatchCache = {}
    for aid, aname in (
        await session.execute(select(Author.id, Author.name))
    ).all():
        key = normalize_person_name(aname)
        if key:
            cache.setdefault(f"author:{key}", aid)
    for sid, sname in (
        await session.execute(select(Series.id, Series.name))
    ).all():
        mk = _series_match_key(sname)
        if mk:
            cache.setdefault(_series_cache_key(mk), sid)
    return cache


async def resolve_book(
    session: AsyncSession,
    *,
    title: str,
    author: str | None,
    series: str | None,
    series_number: float | None,
    isbn13: str | None,
    isbn10: str | None,
    match_cache: MatchCache | None = None,
    author_person_id: int | None = None,
) -> Book:
    author_row = (
        await _find_or_create_author(session, author, match_cache, person_id=author_person_id)
        if author
        else None
    )
    series_row = await _find_or_create_series(session, series, match_cache) if series else None

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


def _display_name(name: str) -> str:
    """The name to store on an `Author` row for `name`. A solo credit is kept
    verbatim (first-seen). A collaboration ("A & B") is stored under its
    primary author's clean solo name — REVIEW-2026-09-08 policy: co-authored
    books are filed under the primary author, deterministically, so
    `_find_or_create_author` must never leave a row named "A & B"."""
    if is_collaboration(name):
        primary = primary_author_name(name).strip()
        if primary and not is_collaboration(primary):
            return primary
    return name


def _upgrade_author_row(row: Author, display: str) -> Author:
    # If this row is still named after a collaboration ("A & B") and we now
    # have a clean solo form, upgrade the display name — the row represents the
    # primary author. (Rewriting to a co-author *list* regressed the corpus in
    # Stage J; rewriting to the clean solo name is what the truth wants.)
    if is_collaboration(row.name) and not is_collaboration(display):
        row.name = display
    if row.sort_name is None:
        row.sort_name = person_sort_name(row.name) or None
    return row


def _stamp_person_id(row: Author, person_id: int | None) -> None:
    """Opportunistically backfill a matched row's Hardcover person id during a
    scan, so it doesn't have to wait for the nightly pass. Never overwrites."""
    if person_id is not None and row.hardcover_person_id is None:
        row.hardcover_person_id = person_id


async def _find_or_create_author(
    session: AsyncSession,
    name: str,
    cache: MatchCache | None = None,
    *,
    person_id: int | None = None,
) -> Author:
    # prompts/15 Stage J: match on normalize_person_name so "J.R.R. Tolkien",
    # "J. R. R. Tolkien" and "Tolkien, J.R.R." reuse one row instead of forking
    # three — and, since normalize_person_name keys a collaboration to its
    # primary author, "Dean Koontz, Kevin J. Anderson" resolves to the "Dean
    # Koontz" row (the review policy). Fall back to the old word-set match for
    # a name that normalises to nothing (so junk names don't all collapse onto
    # one empty key).
    #
    # prompts/28 Phase 2: if the name-key misses but Hardcover resolved this
    # author to a `person_id` we already have a row for (a pen name, or an
    # initial variant with no shared book), reuse that row rather than fork.
    key = normalize_person_name(name)
    fallback = normalize_words(name)
    display = _display_name(name)

    if cache is not None and key:
        cached_id = cache.get(f"author:{key}")
        if cached_id is not None:
            row = await session.get(Author, cached_id)
            if row is not None:
                _stamp_person_id(row, person_id)
                return _upgrade_author_row(row, display)

    authors = list((await session.execute(select(Author))).scalars().all())

    for existing in authors:
        matched = (
            normalize_person_name(existing.name) == key
            if key
            else normalize_words(existing.name) == fallback
        )
        if matched:
            if cache is not None and key:
                cache[f"author:{key}"] = existing.id
            _stamp_person_id(existing, person_id)
            return _upgrade_author_row(existing, display)

    if person_id is not None:
        if cache is not None:
            hit = cache.get(f"hcperson:{person_id}")
            if hit is not None:
                row = await session.get(Author, hit)
                if row is not None:
                    return _upgrade_author_row(row, display)
        for existing in authors:
            if existing.hardcover_person_id == person_id:
                if cache is not None:
                    cache[f"hcperson:{person_id}"] = existing.id
                    if key:
                        cache[f"author:{key}"] = existing.id
                return _upgrade_author_row(existing, display)

    row = Author(
        name=display,
        sort_name=person_sort_name(display) or None,
        hardcover_person_id=person_id,
    )
    session.add(row)
    await session.flush()
    if cache is not None and key:
        cache[f"author:{key}"] = row.id
    if cache is not None and person_id is not None:
        cache[f"hcperson:{person_id}"] = row.id
    return row


async def resolve_series(session: AsyncSession, name: str | None) -> Series | None:
    """Public wrapper — a hand correction sets a book's series directly
    (resolve_book only sets series on newly-created rows, not existing ones)."""
    return await _find_or_create_series(session, name) if name else None


def _series_cache_key(match_key: frozenset[str]) -> str:
    return "series:" + " ".join(sorted(match_key))


async def _find_or_create_series(
    session: AsyncSession, name: str, cache: MatchCache | None = None
) -> Series:
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
    ckey = _series_cache_key(target) if target else None

    if cache is not None and ckey:
        cached_id = cache.get(ckey)
        if cached_id is not None:
            row = await session.get(Series, cached_id)
            if row is not None:
                return row

    alias = (
        await session.execute(
            select(SeriesAlias).where(SeriesAlias.alias == name.strip())
        )
    ).scalar_one_or_none()
    if alias is not None:
        found = await session.get(Series, alias.series_id)
        if found is not None:
            # Deliberately not cached: an alias points a specific wording at a
            # row whose own match key may differ, so keying it by `target`
            # would mis-route a later non-alias name that shares that key.
            return found
    for existing in (await session.execute(select(Series))).scalars().all():
        if _series_match_key(existing.name) == target or normalize_words(existing.name) == exact:
            if cache is not None and ckey:
                cache[ckey] = existing.id
            return existing
    row = Series(name=name)
    session.add(row)
    await session.flush()
    if cache is not None and ckey:
        cache[ckey] = row.id
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
