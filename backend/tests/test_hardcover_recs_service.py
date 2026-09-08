import json

import httpx
import pytest
import respx

from app.data.models import Book, File, FileStatus, Identifier, IdentifierType
from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_recs_service import refresh_book_recs


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


async def _seed_book(db_session, title: str, isbn: str | None) -> Book:
    book = Book(canonical_title=title)
    db_session.add(book)
    await db_session.flush()
    db_session.add(
        File(
            drive_file_id=f"d-{title}",
            filename=f"{title}.epub",
            sha256=title,
            size_bytes=1,
            status=FileStatus.organised,
            book_id=book.id,
        )
    )
    if isbn:
        db_session.add(Identifier(book_id=book.id, type=IdentifierType.isbn13, value=isbn))
    await db_session.commit()
    return book


def _route(similar_ids: list[int], resolved: list[dict], book_extra: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["query"]
        if "SimilarByIsbn" in q:
            book = {"id": 999, "cached_similar_book_ids": similar_ids, **(book_extra or {})}
            return httpx.Response(200, json={"data": {"editions": [{"book": book}]}})
        if "ResolveBooks" in q:
            return httpx.Response(200, json={"data": {"books": resolved}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)


def _resolved(bid: int, title: str, author: str, isbn: str | None) -> dict:
    return {
        "id": bid,
        "title": title,
        "contributions": [{"author": {"name": author}}],
        "editions": [{"isbn_13": isbn}] if isbn else [],
    }


@respx.mock
async def test_resolves_and_ranks_recs(db_session) -> None:
    await _seed_book(db_session, "Mistborn", "9780765311788")
    _route(
        similar_ids=[10, 20, 999, 30],  # 999 is the book itself
        resolved=[
            _resolved(20, "The Way of Kings", "Brandon Sanderson", "9780765326355"),
            _resolved(10, "A Game of Thrones", "George R.R. Martin", "9780553588484"),
            _resolved(30, "Red Rising", "Pierce Brown", None),
        ],
    )

    counts = await refresh_book_recs(db_session)
    assert counts["resolved"] == 1

    book = (await db_session.execute(_sel("Mistborn"))).scalar_one()
    hc = book.hardcover_json
    assert hc["id"] == 999
    # ranked by the similar_ids order, self (999) dropped
    assert [r["title"] for r in hc["similar"]] == [
        "A Game of Thrones",
        "The Way of Kings",
        "Red Rising",
    ]
    assert hc["similar"][2]["isbn13"] is None
    assert book.hardcover_synced_at is not None


@respx.mock
async def test_stores_curated_meta(db_session) -> None:
    await _seed_book(db_session, "Mistborn", "9780765311788")
    _route(
        similar_ids=[10],
        resolved=[_resolved(10, "Elantris", "Brandon Sanderson", None)],
        book_extra={
            "rating": 4.4652,
            "ratings_count": 5645,
            "pages": 541,
            "book_category_id": 1,
            "literary_type_id": 1,
            "cached_tags": {
                "Genre": [{"tag": "Fantasy", "count": 9}, {"tag": "Epic Fantasy", "count": 4}],
                "Mood": [{"tag": "dark", "count": 3}],
            },
            "description": "  Kelsier\n\nrecruits   a crew.  ",
        },
    )

    await refresh_book_recs(db_session)

    meta = (await db_session.execute(_sel("Mistborn"))).scalar_one().hardcover_json["meta"]
    assert meta == {
        "rating": 4.47,
        "ratingsCount": 5645,
        "pages": 541,
        "category": "Book",
        "literaryType": "Fiction",
        "genres": ["Fantasy", "Epic Fantasy"],
        "moods": ["dark"],
        "description": "Kelsier recruits a crew.",
    }


@respx.mock
async def test_backfills_meta_for_a_pre_meta_row(db_session) -> None:
    from datetime import UTC, datetime

    book = await _seed_book(db_session, "Older", "9780765311789")
    book.hardcover_json = {"id": 999, "similar": [{"title": "x", "author": None, "isbn13": None}]}
    book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)  # "fresh"
    await db_session.commit()
    _route(similar_ids=[10], resolved=[_resolved(10, "x", "a", None)], book_extra={"pages": 100})

    counts = await refresh_book_recs(db_session, stale_after_days=45)
    assert counts["resolved"] == 1  # picked up despite the recent sync — it had no meta
    assert (await db_session.execute(_sel("Older"))).scalar_one().hardcover_json["meta"] == {
        "pages": 100
    }


@respx.mock
async def test_book_without_isbn_is_never_selected(db_session) -> None:
    await _seed_book(db_session, "NoIsbn", None)
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))

    counts = await refresh_book_recs(db_session)
    assert route.call_count == 0
    assert counts == {"resolved": 0, "empty": 0, "failed": 0}
    book = (await db_session.execute(_sel("NoIsbn"))).scalar_one()
    assert book.hardcover_json is None  # left alone — no ISBN, nothing to look up


@respx.mock
async def test_no_similar_ids_stores_empty_list(db_session) -> None:
    await _seed_book(db_session, "Lonely", "9780000000002")
    _route(similar_ids=[], resolved=[])

    counts = await refresh_book_recs(db_session)
    assert counts["empty"] == 1
    hc = (await db_session.execute(_sel("Lonely"))).scalar_one().hardcover_json
    assert hc["id"] == 999
    assert hc["similar"] == []


@respx.mock
async def test_fresh_books_are_skipped(db_session) -> None:
    from datetime import UTC, datetime

    book = await _seed_book(db_session, "Recent", "9780000000003")
    book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
    await db_session.commit()

    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))
    counts = await refresh_book_recs(db_session, stale_after_days=45)
    assert route.call_count == 0
    assert counts == {"resolved": 0, "empty": 0, "failed": 0}


async def test_no_token_is_a_no_op(db_session, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "")
    await _seed_book(db_session, "X", "9780000000004")
    assert await refresh_book_recs(db_session) == {"skipped": "no HARDCOVER_API_TOKEN"}


@respx.mock
async def test_daily_limit_stops_early_without_wiping_existing_recs(db_session) -> None:
    book = await _seed_book(db_session, "HasRecs", "9780000000010")
    book.hardcover_json = {
        "id": 5,
        "similar": [{"title": "keep me", "author": "A", "isbn13": None}],
        "meta": {"pages": 200},
    }
    await db_session.commit()

    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "60000", "x-ratelimit-daily-remaining": "0"}, json={}
        )
    )

    counts = await refresh_book_recs(db_session, stale_after_days=45)
    assert counts.get("rate_limited") is True

    hc = (await db_session.execute(_sel("HasRecs"))).scalar_one().hardcover_json
    assert [r["title"] for r in hc["similar"]] == ["keep me"]  # not wiped to []
    assert hc["meta"] == {"pages": 200}


@respx.mock
async def test_transient_failure_leaves_a_book_alone(db_session) -> None:
    book = await _seed_book(db_session, "Flaky", "9780000000011")
    book.hardcover_json = {"id": 9, "similar": [{"title": "x", "author": None, "isbn13": None}]}
    await db_session.commit()

    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"errors": [{"message": "boom"}]}))

    counts = await refresh_book_recs(db_session)
    assert counts["failed"] == 1 and counts["empty"] == 0

    hc = (await db_session.execute(_sel("Flaky"))).scalar_one().hardcover_json
    assert hc["similar"] == [{"title": "x", "author": None, "isbn13": None}]  # untouched


def _sel(title: str):
    from sqlalchemy import select

    return select(Book).where(Book.canonical_title == title)
