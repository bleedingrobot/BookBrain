from contextlib import contextmanager

from sqlalchemy import event, select

from app.data.models import Author, Book, Identifier, Series
from app.services.book_repository import build_match_cache, resolve_book


async def test_creates_author_series_book_and_identifier(db_session) -> None:
    book = await resolve_book(
        db_session,
        title="Dune",
        author="Frank Herbert",
        series="Dune Chronicles",
        series_number=1.0,
        isbn13="9780441172719",
        isbn10=None,
    )

    assert book.canonical_title == "Dune"
    assert (await db_session.execute(select(Author))).scalars().one().name == "Frank Herbert"
    assert (await db_session.execute(select(Series))).scalars().one().name == "Dune Chronicles"
    identifiers = (await db_session.execute(select(Identifier))).scalars().all()
    assert [i.value for i in identifiers] == ["9780441172719"]


async def test_resolving_same_book_twice_does_not_duplicate(db_session) -> None:
    first = await resolve_book(
        db_session,
        title="Dune",
        author="Frank Herbert",
        series=None,
        series_number=None,
        isbn13="9780441172719",
        isbn10=None,
    )
    second = await resolve_book(
        db_session,
        title="Dune",
        author="Frank Herbert",
        series=None,
        series_number=None,
        isbn13="9780441172719",
        isbn10=None,
    )

    assert first.id == second.id
    assert len((await db_session.execute(select(Book))).scalars().all()) == 1
    assert len((await db_session.execute(select(Author))).scalars().all()) == 1
    # re-resolving with the same ISBN doesn't create a duplicate identifier
    assert len((await db_session.execute(select(Identifier))).scalars().all()) == 1


async def test_same_title_different_author_creates_separate_books(db_session) -> None:
    first = await resolve_book(
        db_session,
        title="Common Title",
        author="Author One",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )
    second = await resolve_book(
        db_session,
        title="Common Title",
        author="Author Two",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )

    assert first.id != second.id


async def test_book_without_author_resolves(db_session) -> None:
    book = await resolve_book(
        db_session,
        title="Anonymous Work",
        author=None,
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )

    assert book.author_id is None


async def test_series_variants_reuse_the_same_row(db_session) -> None:
    # Regression: these four phrasings of the same series (reordered words,
    # with/without parens, differing case) were each creating a separate
    # Series row — and a separate Drive folder on organize.
    variants = [
        "Cirque Du Freak  The Saga of Darren Shan",
        "Cirque Du Freak (The Saga of Darren Shan)",
        "The Saga of Darren Shan (Cirque Du Freak)",
        "The Saga of Darren Shan (Cirque du Freak)",
    ]

    for i, series_name in enumerate(variants):
        await resolve_book(
            db_session,
            title=f"Book {i}",
            author="Darren Shan",
            series=series_name,
            series_number=float(i + 1),
            isbn13=None,
            isbn10=None,
        )

    series_rows = (await db_session.execute(select(Series))).scalars().all()
    assert len(series_rows) == 1
    # first-seen phrasing wins as the canonical stored name
    assert series_rows[0].name == variants[0]

    books = (await db_session.execute(select(Book))).scalars().all()
    assert len(books) == 4
    assert all(b.series_id == series_rows[0].id for b in books)


async def test_author_variants_reuse_the_same_row(db_session) -> None:
    await resolve_book(
        db_session,
        title="Book One",
        author="Frank Herbert",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )
    await resolve_book(
        db_session,
        title="Book Two",
        author="frank   herbert",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )

    authors = (await db_session.execute(select(Author))).scalars().all()
    assert len(authors) == 1


async def test_author_initial_and_lastfirst_variants_reuse_one_row(db_session) -> None:
    # prompts/15 Stage J
    for who in ("J.R.R. Tolkien", "J. R. R. Tolkien", "Tolkien, J.R.R.", "Iain M. Banks"):
        await resolve_book(
            db_session, title=f"Book {who}", author=who,
            series=None, series_number=None, isbn13=None, isbn10=None,
        )
    for who in ("Iain Banks",):
        await resolve_book(
            db_session, title=f"Book {who}", author=who,
            series=None, series_number=None, isbn13=None, isbn10=None,
        )

    authors = {a.name for a in (await db_session.execute(select(Author))).scalars().all()}
    assert authors == {"J.R.R. Tolkien", "Iain M. Banks"}  # 2 rows, not 5


async def test_different_authors_sharing_initials_stay_separate(db_session) -> None:
    for who in ("James Smith", "Jane Smith"):
        await resolve_book(
            db_session, title=f"Book by {who}", author=who,
            series=None, series_number=None, isbn13=None, isbn10=None,
        )
    assert len((await db_session.execute(select(Author))).scalars().all()) == 2


async def test_sort_name_is_populated(db_session) -> None:
    await resolve_book(
        db_session, title="Elantris", author="Brandon Sanderson",
        series=None, series_number=None, isbn13=None, isbn10=None,
    )
    author = (await db_session.execute(select(Author))).scalars().one()
    assert author.sort_name == "Sanderson, Brandon"


async def test_coauthored_book_files_under_the_primary_author(db_session) -> None:
    # REVIEW-2026-09-08 policy: "A & B" resolves to A's row, and the row is
    # named with the clean solo form — deterministically, whatever the scan
    # order.
    b1 = await resolve_book(
        db_session, title="Frankenstein: Prodigal Son",
        author="Dean Koontz & Kevin J. Anderson",
        series=None, series_number=None, isbn13=None, isbn10=None,
    )
    b2 = await resolve_book(
        db_session, title="Watchers", author="Dean Koontz",
        series=None, series_number=None, isbn13=None, isbn10=None,
    )
    authors = (await db_session.execute(select(Author))).scalars().all()
    assert len(authors) == 1
    assert authors[0].name == "Dean Koontz"
    assert b1.author_id == b2.author_id == authors[0].id


async def test_collab_first_then_solo_upgrades_the_display_name(db_session) -> None:
    # The comma form doesn't carry a co-author separator, so a lone
    # "Dean Koontz, Kevin J. Anderson" scan lands verbatim; the next solo
    # scan upgrades the row to the clean name.
    await resolve_book(
        db_session, title="Prodigal Son", author="Dean Koontz; Kevin J. Anderson",
        series=None, series_number=None, isbn13=None, isbn10=None,
    )
    await resolve_book(
        db_session, title="Watchers", author="Dean Koontz",
        series=None, series_number=None, isbn13=None, isbn10=None,
    )
    author = (await db_session.execute(select(Author))).scalars().one()
    assert author.name == "Dean Koontz"


async def test_series_leading_article_does_not_fork(db_session) -> None:
    # prompts/15 Stage J — "The Stormlight Archive" == "Stormlight Archive".
    for name in ("The Stormlight Archive", "Stormlight Archive"):
        await resolve_book(
            db_session, title=f"vol for {name}", author="Brandon Sanderson",
            series=name, series_number=1.0, isbn13=None, isbn10=None,
        )
    series_rows = (await db_session.execute(select(Series))).scalars().all()
    assert len(series_rows) == 1
    assert series_rows[0].name == "The Stormlight Archive"  # first-seen kept


async def test_series_alias_resolves_to_the_aliased_row(db_session) -> None:
    from app.data.models import SeriesAlias

    book = await resolve_book(
        db_session, title="A book", author="Some Author",
        series="Mistborn", series_number=1.0, isbn13=None, isbn10=None,
    )
    canonical = (await db_session.execute(select(Series))).scalars().one()
    db_session.add(SeriesAlias(series_id=canonical.id, alias="The Mistborn Saga"))
    await db_session.flush()

    await resolve_book(
        db_session, title="Another book", author="Some Author",
        series="The Mistborn Saga", series_number=2.0, isbn13=None, isbn10=None,
    )
    assert len((await db_session.execute(select(Series))).scalars().all()) == 1


async def test_title_casing_variants_reuse_the_same_book(db_session) -> None:
    # Regression: different uploads of the same book routinely differ in
    # AI-extracted title casing ("The Hob's Bargain" vs "The Hob's bargain")
    # — each was forking a separate Book row instead of resolving to the
    # same one, even though author matching already deduped correctly.
    first = await resolve_book(
        db_session,
        title="The Hob's Bargain",
        author="Patricia Briggs",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )
    second = await resolve_book(
        db_session,
        title="The Hob's bargain",
        author="Patricia Briggs",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )

    assert first.id == second.id
    books = (await db_session.execute(select(Book))).scalars().all()
    assert len(books) == 1


async def test_same_author_series_prefix_titles_stay_separate_books(db_session) -> None:
    # Regression (P0): "<Series>: <Book Title>" is a very common epub title
    # format. The loose normalize_title stripped everything from the colon on,
    # so two different books by the same author collapsed onto one Book row —
    # from there detect_same_book_duplicates flags one as a duplicate and the
    # bulk clear can trash it.
    first = await resolve_book(
        db_session,
        title="Mistborn: The Final Empire",
        author="Brandon Sanderson",
        series="Mistborn",
        series_number=1.0,
        isbn13=None,
        isbn10=None,
    )
    second = await resolve_book(
        db_session,
        title="Mistborn: The Well of Ascension",
        author="Brandon Sanderson",
        series="Mistborn",
        series_number=2.0,
        isbn13=None,
        isbn10=None,
    )

    assert first.id != second.id
    assert len((await db_session.execute(select(Book))).scalars().all()) == 2


@contextmanager
def _capture_full_table_scans(db_session):
    """Records every `SELECT ... FROM <table>` with no WHERE — the O(rows)
    load-them-all match loop in `_find_or_create_*`, as opposed to the O(1)
    `session.get(..., id)` a cache hit does."""
    seen: list[str] = []

    def _listen(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split()).lower()
        for table in ("authors", "series"):
            if f"from {table}" in flat and "where" not in flat:
                seen.append(flat)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _listen)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _listen)


async def test_match_cache_skips_the_per_file_full_table_scan(db_session) -> None:
    # REVIEW-2026-09-08 F6: without the cache, every file reloads all Author +
    # Series rows to fuzzy-match. Primed once per batch, repeat resolves for a
    # known author/series must not scan the tables again.
    for who in ("Alice Author", "Bob Author"):
        await resolve_book(
            db_session, title=f"seed {who}", author=who,
            series="Seed Cycle", series_number=1.0, isbn13=None, isbn10=None,
        )
    await db_session.commit()

    cache = await build_match_cache(db_session)

    with _capture_full_table_scans(db_session) as scans:
        for i in range(5):
            await resolve_book(
                db_session, title=f"vol {i}", author="Alice Author",
                series="Seed Cycle", series_number=float(i + 2),
                isbn13=None, isbn10=None, match_cache=cache,
            )

    assert scans == []
    # all five volumes still landed on the one seeded author/series
    assert len((await db_session.execute(select(Author))).scalars().all()) == 2
    assert len((await db_session.execute(select(Series))).scalars().all()) == 1


async def test_match_cache_miss_still_falls_back_to_the_full_scan(db_session) -> None:
    # A row created by a concurrent path in the same batch won't be in the
    # cache yet — a miss must fall through to the real match, never wrongly
    # create a duplicate.
    cache = await build_match_cache(db_session)
    first = await resolve_book(
        db_session, title="Book One", author="Fresh Name",
        series=None, series_number=None, isbn13=None, isbn10=None, match_cache=cache,
    )
    second = await resolve_book(
        db_session, title="Book Two", author="fresh   name",
        series=None, series_number=None, isbn13=None, isbn10=None, match_cache=cache,
    )
    assert first.author_id == second.author_id
    assert len((await db_session.execute(select(Author))).scalars().all()) == 1


async def test_expanse_style_subtitles_still_resolve_separately(db_session) -> None:
    # These already worked (the distinguishing word is before the colon) —
    # make sure the strict matcher doesn't regress them either.
    a = await resolve_book(
        db_session,
        title="Leviathan Wakes: Book One of The Expanse",
        author="James S. A. Corey",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )
    b = await resolve_book(
        db_session,
        title="Caliban's War: Book Two of The Expanse",
        author="James S. A. Corey",
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
    )
    assert a.id != b.id


# -- prompts/28 Phase 2: scan-time Hardcover person-id resolution -------------


async def _book(db_session, title, author, person_id=None):
    return await resolve_book(
        db_session,
        title=title,
        author=author,
        series=None,
        series_number=None,
        isbn13=None,
        isbn10=None,
        author_person_id=person_id,
    )


async def test_person_id_reuses_a_row_across_a_pen_name(db_session) -> None:
    # "J.K. Rowling" and "Robert Galbraith" are different normalize_person_name
    # keys, so without the person id they fork. Hardcover says both are person
    # 80626 → one row.
    await _book(db_session, "Harry Potter", "J.K. Rowling", person_id=80626)
    await _book(db_session, "The Cuckoo's Calling", "Robert Galbraith", person_id=80626)

    authors = (await db_session.execute(select(Author))).scalars().all()
    assert len(authors) == 1
    book_authors = {b.author_id for b in (await db_session.execute(select(Book))).scalars().all()}
    assert book_authors == {authors[0].id}


async def test_person_id_stamps_an_existing_name_match(db_session) -> None:
    await _book(db_session, "Neverwhere", "Neil Gaiman")
    await _book(db_session, "Stardust", "Neil Gaiman", person_id=555)

    author = (await db_session.execute(select(Author))).scalar_one()
    assert author.hardcover_person_id == 555


async def test_person_id_never_overwrites_a_stored_one(db_session) -> None:
    await _book(db_session, "A", "Some Author", person_id=111)
    await _book(db_session, "B", "Some Author", person_id=222)  # bogus later hit

    author = (await db_session.execute(select(Author))).scalar_one()
    assert author.hardcover_person_id == 111


async def test_no_person_id_forks_the_pen_name_as_before(db_session) -> None:
    await _book(db_session, "Harry Potter", "J.K. Rowling")
    await _book(db_session, "The Cuckoo's Calling", "Robert Galbraith")

    assert len((await db_session.execute(select(Author))).scalars().all()) == 2
