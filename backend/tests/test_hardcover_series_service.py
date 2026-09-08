import json

import httpx
import pytest
import respx

from app.data.models import Author, Book, File, FileStatus, Series
from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_series_service import refresh_series_catalog


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


def _route(search_hits: list[dict], series_books: dict | None):
    """One respx handler for both queries, dispatched on the query text."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q = body["query"]
        if "SeriesSearch" in q or "query_type" in q:
            return httpx.Response(
                200, json={"data": {"search": {"results": {"hits": [{"document": d} for d in search_hits]}}}}
            )
        if "SeriesBooks" in q:
            return httpx.Response(200, json={"data": {"series": [series_books] if series_books else []}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)


async def _seed_series(db_session, name: str, author_name: str | None = "Brandon Sanderson") -> Series:
    author = Author(name=author_name) if author_name else None
    series = Series(name=name)
    db_session.add(series)
    if author:
        db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title=f"{name} bk", series_id=series.id, author_id=author.id if author else None)
    db_session.add(book)
    await db_session.flush()
    db_session.add(
        File(
            drive_file_id=f"d-{name}",
            filename=f"{name}.epub",
            sha256=name,
            size_bytes=1,
            status=FileStatus.organised,
            book_id=book.id,
        )
    )
    await db_session.commit()
    return series


@respx.mock
async def test_matches_and_stores_the_canonical_book_list(db_session) -> None:
    await _seed_series(db_session, "Mistborn")
    _route(
        search_hits=[
            {"id": "5452", "name": "The Mistborn Saga", "author_name": "Brandon Sanderson", "slug": "the-mistborn-saga"},
            {"id": "10001", "name": "The Mistborn Saga: The Original Trilogy", "author_name": "Brandon Sanderson"},
        ],
        series_books={
            "id": 5452,
            "name": "The Mistborn Saga",
            "slug": "the-mistborn-saga",
            "primary_books_count": 3,
            "book_series": [
                {"position": 0.5, "book": {"title": "The Eleventh Metal"}},
                {"position": 1, "book": {"title": "The Final Empire"}},
                {"position": 2, "book": {"title": "The Well of Ascension"}},
                {"position": None, "book": {"title": "junk"}},
            ],
        },
    )

    counts = await refresh_series_catalog(db_session)
    assert counts["matched"] == 1 and counts["refreshed"] == 1

    series = (await db_session.execute(_select_series("Mistborn"))).scalar_one()
    hc = series.hardcover_json
    # picked the shorter "The Mistborn Saga", not the "...Original Trilogy" one
    assert hc["id"] == 5452
    assert hc["match"] == "auto"
    assert hc["slug"] == "the-mistborn-saga"
    # null position dropped; 0.5 kept
    assert [b["position"] for b in hc["books"]] == [0.5, 1.0, 2.0]
    assert series.hardcover_synced_at is not None


@respx.mock
async def test_no_match_records_none_and_stops_re_searching(db_session) -> None:
    await _seed_series(db_session, "Some Obscure Series")
    _route(search_hits=[{"id": "1", "name": "Totally Different", "author_name": "Someone Else"}], series_books=None)

    counts = await refresh_series_catalog(db_session)
    assert counts["unmatched"] == 1

    series = (await db_session.execute(_select_series("Some Obscure Series"))).scalar_one()
    assert series.hardcover_json == {"match": "none"}
    assert series.hardcover_synced_at is not None


@respx.mock
async def test_manual_match_is_not_re_searched(db_session) -> None:
    series = await _seed_series(db_session, "Pinned")
    series.hardcover_json = {"id": 999, "name": "Pinned By Hand", "slug": "pinned", "match": "manual"}
    await db_session.commit()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["query"]
        calls.append("search" if "query_type" in q else "books")
        return httpx.Response(
            200,
            json={"data": {"series": [{"id": 999, "name": "Pinned By Hand", "slug": "pinned",
                                       "primary_books_count": 2,
                                       "book_series": [{"position": 1, "book": {"title": "One"}}]}]}},
        )

    respx.post(ENDPOINT).mock(side_effect=handler)

    await refresh_series_catalog(db_session)

    assert "search" not in calls  # only the book-list query ran
    hc = (await db_session.execute(_select_series("Pinned"))).scalar_one().hardcover_json
    assert hc["id"] == 999 and hc["match"] == "manual"
    assert hc["books"] == [{"position": 1.0, "title": "One"}]


@respx.mock
async def test_fresh_series_are_skipped(db_session) -> None:
    series = await _seed_series(db_session, "Recent")
    from datetime import UTC, datetime

    series.hardcover_json = {"match": "none"}
    series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
    await db_session.commit()

    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))
    counts = await refresh_series_catalog(db_session, stale_after_days=30)

    assert route.call_count == 0
    assert counts == {"matched": 0, "refreshed": 0, "unmatched": 0, "failed": 0}


async def test_no_token_is_a_no_op(db_session, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "")
    await _seed_series(db_session, "Whatever")
    assert await refresh_series_catalog(db_session) == {"skipped": "no HARDCOVER_API_TOKEN"}


def _select_series(name: str):
    from sqlalchemy import select

    return select(Series).where(Series.name == name)
