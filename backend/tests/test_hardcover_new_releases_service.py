import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.data.models import Author, Book, File, FileStatus
from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_new_releases_service import (
    fetch_global_anticipated,
    real_release_date,
    refresh_new_releases,
)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


async def _seed_author(db_session, name: str) -> Author:
    author = Author(name=name)
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title=f"{name} owned book", author_id=author.id)
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
    return author


def _iso(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def _route(books: list[dict], *, status: int = 200, global_books: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["query"]
        if "AuthorReleases" in q:
            if status != 200:
                return httpx.Response(status, json={})
            return httpx.Response(200, json={"data": {"books": books}})
        if "GlobalAnticipated" in q:
            return httpx.Response(200, json={"data": {"books": global_books or []}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)


def _book(title: str, release_date: str, isbn: str | None = "9990000000000", **extra) -> dict:
    return {
        "title": title,
        "release_date": release_date,
        "book_category_id": extra.get("category_id", 1),
        "editions": [{"isbn_13": isbn}] if isbn else [],
        "cached_tags": {"Genre": [{"tag": g} for g in extra.get("genres", [])]},
    }


def test_real_release_date_rejects_placeholders_and_far_future() -> None:
    assert real_release_date("Untitled Series #7", "2035-01-01") is None
    assert real_release_date("A Real Book", "2099-01-01") is None
    assert real_release_date("A Real Book", "not-a-date") is None
    assert real_release_date("A Real Book", "2024-05-01") is not None


@respx.mock
async def test_stores_recent_and_future_books_per_author(db_session) -> None:
    await _seed_author(db_session, "Rebecca Yarros")
    _route(
        [
            _book("Onyx Storm", _iso(-30), genres=["Fantasy", "Romance"]),
            _book("Threshing Day", _iso(200)),
            _book("Untitled Empyrean #5", _iso(400)),  # placeholder, dropped
            _book("Onyx Storm", _iso(-30)),  # dup title, dropped
        ]
    )

    counts = await refresh_new_releases(db_session)
    assert counts["with_books"] == 1

    author = (await db_session.execute(_select("Rebecca Yarros"))).scalar_one()
    titles = [b["title"] for b in author.hardcover_json["books"]]
    assert titles == ["Onyx Storm", "Threshing Day"]
    assert author.hardcover_json["books"][0]["genres"] == ["Fantasy", "Romance"]
    assert author.hardcover_synced_at is not None


@respx.mock
async def test_a_transient_call_failure_leaves_the_author_untouched(db_session) -> None:
    author = await _seed_author(db_session, "Someone")
    author.hardcover_json = {"books": [{"title": "kept", "releaseDate": "2024-01-01"}]}
    await db_session.commit()
    _route([], status=500)

    counts = await refresh_new_releases(db_session)
    assert counts["failed"] == 1

    refreshed = (await db_session.execute(_select("Someone"))).scalar_one()
    assert refreshed.hardcover_json == {"books": [{"title": "kept", "releaseDate": "2024-01-01"}]}


@respx.mock
async def test_only_stale_authors_are_picked(db_session) -> None:
    fresh = await _seed_author(db_session, "Fresh")
    fresh.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
    await _seed_author(db_session, "Stale")
    await db_session.commit()
    _route([_book("New Book", _iso(-5))])

    counts = await refresh_new_releases(db_session, stale_after_days=14)
    assert counts["authors"] == 1  # only "Stale"


@respx.mock
async def test_fetch_global_anticipated_maps_author_and_drops_placeholders() -> None:
    _route(
        [],
        global_books=[
            {
                "title": "A Court of Splintered Harmony",
                "release_date": _iso(60),
                "book_category_id": 1,
                "contributions": [{"author": {"name": "Sarah J. Maas"}}],
                "editions": [{"isbn_13": "9990000000009"}],
                "cached_tags": {"Genre": [{"tag": "Fantasy"}]},
            },
            {
                "title": "Untitled Stormlight Archive #6",
                "release_date": "2031-12-01",  # placeholder → dropped
                "contributions": [],
                "editions": [],
                "cached_tags": {},
            },
        ],
    )

    out = await fetch_global_anticipated(limit=10)
    assert [b["title"] for b in out] == ["A Court of Splintered Harmony"]
    assert out[0]["author"] == "Sarah J. Maas"
    assert out[0]["isbn13"] == "9990000000009"


def _select(name: str):
    from sqlalchemy import select

    return select(Author).where(Author.name == name)
