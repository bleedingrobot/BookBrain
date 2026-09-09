import json

import httpx
import pytest
import respx

from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_reading_service import fetch_reading


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


def _ub(status_id, *, title="A Book", author="An Author", isbn="9990000000001", rating=None, date=None):
    return {
        "status_id": status_id,
        "rating": rating,
        "last_read_date": date,
        "first_read_date": None,
        "read_count": 1 if status_id == 3 else 0,
        "book": {
            "title": title,
            "contributions": [{"author": {"name": author}}] if author else [],
            "editions": [{"isbn_13": isbn}] if isbn else [],
        },
    }


def _route(pages: list[list[dict]]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        rows = pages[i] if i < len(pages) else []
        return httpx.Response(200, json={"data": {"me": [{"username": "bleedrobot", "user_books": rows}]}})

    respx.post(ENDPOINT).mock(side_effect=handler)


@respx.mock
async def test_maps_statuses_and_paginates() -> None:
    # 100 on page 1 forces a page 2 (short → stop).
    page1 = [_ub(3, title=f"Read {i}", rating=4.0) for i in range(100)]
    page2 = [
        _ub(1, title="Want it"),
        _ub(2, title="Mid-read"),
        _ub(5, title="Bailed"),
        _ub(99, title="Unknown status", rating=None),  # dropped (no status, no rating)
        _ub(99, title="Unknown but rated", rating=3.5),  # kept — has a rating
    ]
    _route([page1, page2])

    rows, user = await fetch_reading()

    assert user == "bleedrobot"
    by_status = {}
    for r in rows:
        by_status.setdefault(r["status"], 0)
        by_status[r["status"]] += 1
    assert by_status == {"read": 100, "want": 1, "reading": 1, "dnf": 1, None: 1}
    assert len(rows) == 104  # the no-status-no-rating row is gone
    read = next(r for r in rows if r["status"] == "read")
    assert read["rating"] == 4.0 and read["readCount"] == 1


@respx.mock
async def test_no_token_no_request() -> None:
    from app.core.config import get_settings

    get_settings().hardcover_api_token = ""
    try:
        rows, user = await fetch_reading()
        assert rows == [] and user is None
    finally:
        get_settings().hardcover_api_token = "tok"


@respx.mock
async def test_a_failure_keeps_what_came_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["variables"]["offset"] == 0:
            return httpx.Response(200, json={"data": {"me": [{"username": "u", "user_books": [_ub(3)] * 100}]}})
        return httpx.Response(500, json={})

    respx.post(ENDPOINT).mock(side_effect=handler)
    rows, user = await fetch_reading()
    assert len(rows) == 100  # page 1 survived the page-2 failure
