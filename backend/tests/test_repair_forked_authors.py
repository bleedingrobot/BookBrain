"""prompts/15 Stage J — the forked-author repair script."""

import importlib

import pytest
from sqlalchemy import select

from app.data.models import Author, Book, Identifier

repair = importlib.import_module("scripts.repair_forked_authors")


@pytest.fixture(autouse=True)
def _route_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(repair, "async_session_factory", lambda: _CM())

    class _Lock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(repair, "get_book_write_lock", lambda: _Lock())
    return db_session


async def _add(session, author_name, title, isbn=None):
    a = Author(name=author_name)
    session.add(a)
    await session.flush()
    b = Book(canonical_title=title, author_id=a.id)
    session.add(b)
    await session.flush()
    if isbn:
        from app.data.models import IdentifierType

        session.add(Identifier(book_id=b.id, type=IdentifierType.isbn13, value=isbn, source="x"))
    await session.flush()
    return a, b


async def test_merges_forked_authors_sharing_a_book(db_session) -> None:
    await _add(db_session, "J.R.R. Tolkien", "The Hobbit", isbn="9780261103283")
    await _add(db_session, "J. R. R. Tolkien", "The Hobbit", isbn="9780261103283")
    await db_session.commit()

    await repair.main(write=True)

    authors = (await db_session.execute(select(Author))).scalars().all()
    assert len(authors) == 1
    assert authors[0].name == "J.R.R. Tolkien"  # shortest clean solo name wins
    books = (await db_session.execute(select(Book))).scalars().all()
    assert {b.author_id for b in books} == {authors[0].id}


async def test_does_not_merge_when_no_book_is_shared(db_session) -> None:
    # "J. Smith" and "J Smith" normalise the same, but their books have nothing
    # in common — could be two different people.
    await _add(db_session, "J. Smith", "A Novel", isbn="9780000000001")
    await _add(db_session, "J Smith", "A Different Novel", isbn="9780000000002")
    await db_session.commit()

    await repair.main(write=True)

    assert len((await db_session.execute(select(Author))).scalars().all()) == 2


async def test_folds_a_collaboration_credit_into_the_primary(db_session) -> None:
    # REVIEW-2026-09-08 policy: a co-authored book is filed under the primary
    # author. "Dean Koontz & Kevin J. Anderson" folds into the "Dean Koontz"
    # row; a solo variant with no shared book does not.
    a1, _ = await _add(db_session, "Dean Koontz", "Watchers")
    await _add(db_session, "Dean Koontz & Kevin J. Anderson", "Prodigal Son")
    await _add(db_session, "Dean R. Koontz", "Lightning")  # solo variant, no shared book
    await db_session.commit()

    await repair.main(write=True)

    authors = {a.name for a in (await db_session.execute(select(Author))).scalars().all()}
    assert authors == {"Dean Koontz", "Dean R. Koontz"}  # collab gone, solo variant kept
    books = (await db_session.execute(select(Book))).scalars().all()
    prodigal = next(b for b in books if b.canonical_title == "Prodigal Son")
    assert prodigal.author_id == a1.id


async def test_all_collab_group_synthesises_the_primary(db_session) -> None:
    await _add(db_session, "A & B", "One")
    await _add(db_session, "A and B", "Two")
    await db_session.commit()

    await repair.main(write=True)

    authors = [a.name for a in (await db_session.execute(select(Author))).scalars().all()]
    assert authors == ["A"]


async def test_dry_run_changes_nothing(db_session) -> None:
    await _add(db_session, "Iain M. Banks", "Consider Phlebas", isbn="9781857231380")
    await _add(db_session, "Iain Banks", "Consider Phlebas", isbn="9781857231380")
    await db_session.commit()

    await repair.main(write=False)

    assert len((await db_session.execute(select(Author))).scalars().all()) == 2
