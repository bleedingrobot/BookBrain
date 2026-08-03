from sqlalchemy import select

from app.data.models import Author, Book, Identifier, Series
from app.services.book_repository import resolve_book


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
