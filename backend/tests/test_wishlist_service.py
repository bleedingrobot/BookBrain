import pytest
from sqlalchemy import select

from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    WishlistItem,
    WishlistStatus,
)
from app.providers.ai.types import AIBookRequestResult
from app.providers.metadata.types import MetadataCandidate
from app.schemas.wishlist import WishlistItemCreate
from app.services import wishlist_service


class _FakeAI:
    def __init__(self, result: AIBookRequestResult) -> None:
        self._result = result

    async def resolve_book_request(self, text: str) -> AIBookRequestResult:
        return self._result


class _FakeCandidates:
    def __init__(self, candidates: list[MetadataCandidate]) -> None:
        self._candidates = candidates

    async def generate_candidates(self, **_kwargs) -> list[MetadataCandidate]:
        return self._candidates


def _mock(monkeypatch, *, ai: AIBookRequestResult, candidates: list[MetadataCandidate] | None = None):
    monkeypatch.setattr(wishlist_service, "AnthropicIdentificationClient", lambda: _FakeAI(ai))
    monkeypatch.setattr(
        wishlist_service, "default_candidate_service", lambda: _FakeCandidates(candidates or [])
    )


async def _seed_library_book(db_session, *, title, author_name, isbn13=None, status=FileStatus.organised):
    author = Author(name=author_name)
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title=title, author_id=author.id)
    db_session.add(book)
    await db_session.flush()
    if isbn13:
        db_session.add(Identifier(book_id=book.id, type=IdentifierType.isbn13, value=isbn13))
    file_row = File(
        drive_file_id=f"d-{title}",
        filename=f"{title}.epub",
        sha256=f"s-{title}",
        size_bytes=1,
        status=status,
        book_id=book.id,
    )
    db_session.add(file_row)
    await db_session.commit()
    return file_row


async def test_resolve_request_firms_up_from_candidates(db_session, monkeypatch) -> None:
    _mock(
        monkeypatch,
        ai=AIBookRequestResult(True, "way of kings", "sanderson", "Stormlight", 1.0, None, "pretty sure"),
        candidates=[
            MetadataCandidate(
                title="The Way of Kings",
                authors=["Brandon Sanderson"],
                isbn13="9780765326355",
                source="google_books",
            )
        ],
    )
    result = await wishlist_service.resolve_request(db_session, "that huge sanderson one about the knight")
    assert result.found is True
    assert result.resolved.title == "The Way of Kings"
    assert result.resolved.author == "Brandon Sanderson"
    assert result.resolved.isbn13 == "9780765326355"
    assert result.resolved.series == "Stormlight"
    assert result.resolved.cover_url and "9780765326355" in result.resolved.cover_url
    assert result.already_in_library is None


async def test_resolve_request_flags_a_book_already_in_the_library_by_isbn(db_session, monkeypatch) -> None:
    await _seed_library_book(
        db_session, title="Mistborn", author_name="Brandon Sanderson", isbn13="9780765311788"
    )
    _mock(
        monkeypatch,
        ai=AIBookRequestResult(True, "Mistborn", "Brandon Sanderson", None, None, "9780765311788", None),
    )
    result = await wishlist_service.resolve_request(db_session, "mistborn book 1")
    assert result.already_in_library is not None
    assert result.already_in_library.filename == "Mistborn.epub"


async def test_resolve_request_matches_library_by_fuzzy_title_author(db_session, monkeypatch) -> None:
    await _seed_library_book(db_session, title="The Final Empire", author_name="Brandon Sanderson")
    _mock(
        monkeypatch,
        ai=AIBookRequestResult(True, "Final Empire", "brandon sanderson", None, None, None, None),
    )
    result = await wishlist_service.resolve_request(db_session, "final empire")
    assert result.already_in_library is not None


async def test_resolve_request_returns_not_found_when_ai_cant_identify(db_session, monkeypatch) -> None:
    _mock(monkeypatch, ai=AIBookRequestResult(False, None, None, None, None, None, "too vague"))
    result = await wishlist_service.resolve_request(db_session, "a book with a blue cover")
    assert result.found is False
    assert result.note == "too vague"


async def test_add_item_dedupes_on_title_author(db_session) -> None:
    body = WishlistItemCreate(raw_request="x", title="Elantris", author="Brandon Sanderson")
    a = await wishlist_service.add_item(db_session, body)
    b = await wishlist_service.add_item(
        db_session, WishlistItemCreate(raw_request="y", title="elantris", author="brandon sanderson")
    )
    assert a.id == b.id
    assert len((await db_session.execute(select(WishlistItem))).scalars().all()) == 1


async def test_reconcile_flips_wanted_items_that_are_now_in_the_library(db_session) -> None:
    item = await wishlist_service.add_item(
        db_session,
        WishlistItemCreate(raw_request="x", title="Warbreaker", author="Brandon Sanderson"),
    )
    assert item.status == WishlistStatus.wanted

    file_row = await _seed_library_book(
        db_session, title="Warbreaker", author_name="Brandon Sanderson"
    )
    changed = await wishlist_service.reconcile(db_session)
    await db_session.commit()
    await db_session.refresh(item)
    assert changed == 1
    assert item.status == WishlistStatus.acquired
    assert item.acquired_file_id == file_row.id


async def test_set_status_back_to_wanted_clears_the_acquisition(db_session) -> None:
    item = await wishlist_service.add_item(
        db_session, WishlistItemCreate(raw_request="x", title="Skyward", author="Brandon Sanderson")
    )
    await wishlist_service.set_status(db_session, item.id, "acquired")
    await db_session.refresh(item)
    assert item.acquired_at is not None
    await wishlist_service.set_status(db_session, item.id, "wanted")
    await db_session.refresh(item)
    assert item.acquired_at is None and item.acquired_file_id is None


async def test_set_status_unknown_id_raises(db_session) -> None:
    with pytest.raises(wishlist_service.WishlistItemNotFound):
        await wishlist_service.set_status(db_session, 999, "acquired")
