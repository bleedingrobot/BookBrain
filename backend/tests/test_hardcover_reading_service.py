import json

import httpx
import pytest
import respx

from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_reading_service import apply_pending, fetch_goal, fetch_reading


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


def _ub(
    status_id,
    *,
    title="A Book",
    author="An Author",
    isbn="9990000000001",
    rating=None,
    date=None,
    pages=None,
    progress_pages=None,
):
    return {
        "status_id": status_id,
        "rating": rating,
        "last_read_date": date,
        "first_read_date": None,
        "read_count": 1 if status_id == 3 else 0,
        "user_book_reads": (
            [{"progress_pages": progress_pages}] if progress_pages is not None else []
        ),
        "book": {
            "title": title,
            "pages": pages,
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
async def test_maps_reader_progress_as_a_fraction() -> None:
    _route([[_ub(2, title="Mid", pages=400, progress_pages=100)]])
    rows, _ = await fetch_reading()
    assert rows[0]["progress"] == 0.25
    # no pages → no fraction
    _route([[_ub(2, title="NoPages", pages=None, progress_pages=100)]])
    rows, _ = await fetch_reading()
    assert rows[0]["progress"] is None


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


# --- prompts/30 Phase 3: write-back --------------------------------------------


def _change(drive_id="d1", *, isbn="9990000000001", status="read", title="A Book"):
    return {
        "driveFileId": drive_id,
        "isbn13": isbn,
        "title": title,
        "author": "An Author",
        "status": status,
        "at": "2026-09-09T00:00:00Z",
        "by": "James",
    }


def _writeback_route(*, existing_status_id=None, book_id=555, mutations: list | None = None):
    """Routes the ISBN lookup, the existing-user_book query, and the mutation."""
    mutations = mutations if mutations is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q = body["query"]
        if "editions(" in q and "isbn" in q:
            return httpx.Response(200, json={"data": {"editions": [{"book": {"id": book_id}}]}})
        if "user_books(where:" in q:
            ubs = (
                [{"id": 42, "status_id": existing_status_id}]
                if existing_status_id is not None
                else []
            )
            return httpx.Response(200, json={"data": {"me": [{"user_books": ubs}]}})
        if "update_user_book" in q:
            mutations.append(("update", body["variables"]))
            return httpx.Response(200, json={"data": {"update_user_book": {"id": 42}}})
        if "insert_user_book" in q:
            mutations.append(("insert", body["variables"]))
            return httpx.Response(200, json={"data": {"insert_user_book": {"id": 99}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)
    return mutations


@respx.mock
async def test_apply_pending_inserts_when_no_existing_user_book() -> None:
    muts = _writeback_route(existing_status_id=None)
    result = await apply_pending([_change(status="read")])
    assert result == {"applied": ["d1"], "failed": []}
    assert muts[0][0] == "insert"
    assert muts[0][1]["obj"]["book_id"] == 555
    assert muts[0][1]["obj"]["status_id"] == 3
    assert "last_read_date" in muts[0][1]["obj"]


@respx.mock
async def test_apply_pending_updates_when_status_differs() -> None:
    muts = _writeback_route(existing_status_id=2)  # currently "reading"
    result = await apply_pending([_change(status="read")])
    assert result == {"applied": ["d1"], "failed": []}
    assert muts[0][0] == "update"
    assert muts[0][1]["id"] == 42
    assert muts[0][1]["obj"]["status_id"] == 3


@respx.mock
async def test_apply_pending_is_idempotent_when_already_matching() -> None:
    muts = _writeback_route(existing_status_id=3)  # already "read"
    result = await apply_pending([_change(status="read")])
    assert result == {"applied": ["d1"], "failed": []}
    assert muts == []  # no mutation issued


@respx.mock
async def test_apply_pending_reports_failure_when_book_cannot_be_resolved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q = body["query"]
        if "editions(" in q:
            return httpx.Response(200, json={"data": {"editions": []}})
        if "search(" in q:
            return httpx.Response(200, json={"data": {"search": {"results": {"hits": []}}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)
    result = await apply_pending([_change(isbn=None)])
    assert result == {"applied": [], "failed": ["d1"]}


@respx.mock
async def test_apply_pending_no_token_is_noop() -> None:
    from app.core.config import get_settings

    get_settings().hardcover_api_token = ""
    try:
        assert await apply_pending([_change()]) == {"applied": [], "failed": []}
    finally:
        get_settings().hardcover_api_token = "tok"


# --- prompts/31 Part I: reading-progress write-back --------------------------


def _progress_route(*, pages=400, existing_read=None, muts=None):
    muts = muts if muts is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q = body["query"]
        if "editions(" in q and "isbn" in q:
            return httpx.Response(200, json={"data": {"editions": [{"book": {"id": 555}}]}})
        if "user_books(where:" in q:
            reads = [{"id": 7, "progress_pages": existing_read}] if existing_read is not None else []
            return httpx.Response(
                200,
                json={
                    "data": {
                        "me": [
                            {
                                "user_books": [
                                    {
                                        "id": 42,
                                        "status_id": 2,
                                        "book": {"pages": pages},
                                        "user_book_reads": reads,
                                    }
                                ]
                            }
                        ]
                    }
                },
            )
        if "update_user_book_read" in q:
            muts.append(("update_read", body["variables"]))
            return httpx.Response(200, json={"data": {"update_user_book_read": {"id": 7}}})
        if "insert_user_book_read" in q:
            muts.append(("insert_read", body["variables"]))
            return httpx.Response(200, json={"data": {"insert_user_book_read": {"id": 8}}})
        return httpx.Response(200, json={"data": {}})

    respx.post(ENDPOINT).mock(side_effect=handler)
    return muts


def _prog_change(pct):
    return {
        "driveFileId": "d1",
        "isbn13": "9990000000001",
        "title": "A Book",
        "author": "An Author",
        "progressPercent": pct,
        "at": "2026-09-10T00:00:00Z",
        "by": "James",
    }


@respx.mock
async def test_progress_inserts_a_read_row_when_none_exists() -> None:
    muts = _progress_route(pages=400, existing_read=None)
    assert await apply_pending([_prog_change(0.5)]) == {"applied": ["d1"], "failed": []}
    assert muts[0][0] == "insert_read"
    assert muts[0][1]["obj"]["progress_pages"] == 200


@respx.mock
async def test_progress_advances_an_existing_read_row() -> None:
    muts = _progress_route(pages=400, existing_read=100)
    assert await apply_pending([_prog_change(0.75)]) == {"applied": ["d1"], "failed": []}
    assert muts[0][0] == "update_read"
    assert muts[0][1]["obj"]["progress_pages"] == 300


@respx.mock
async def test_progress_never_goes_backwards_or_makes_a_trivial_move() -> None:
    muts = _progress_route(pages=400, existing_read=300)
    # 0.72 → 288 pages, which is < the current 300 → no-op, still "applied"
    assert await apply_pending([_prog_change(0.72)]) == {"applied": ["d1"], "failed": []}
    assert muts == []


# --- prompts/31 Part C: reading goal -----------------------------------------


@respx.mock
async def test_fetch_goal_picks_the_current_book_goal() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "me": [
                        {
                            "goals": [
                                {  # an old, finished goal
                                    "goal": 40,
                                    "progress": 40,
                                    "metric": "book",
                                    "start_date": "2024-12-31",
                                    "end_date": "2025-12-30",
                                },
                                {  # a page goal — ignored
                                    "goal": 12000,
                                    "progress": 5000,
                                    "metric": "page",
                                    "start_date": "2025-12-31",
                                    "end_date": "2026-12-30",
                                },
                                {  # the current book goal
                                    "goal": 50,
                                    "progress": 48.0,
                                    "metric": "book",
                                    "start_date": "2025-12-31",
                                    "end_date": "2026-12-30",
                                },
                            ]
                        }
                    ]
                }
            },
        )
    )
    assert await fetch_goal() == {"year": 2026, "target": 50, "progress": 48}


@respx.mock
async def test_fetch_goal_returns_none_when_no_book_goal() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"data": {"me": [{"goals": []}]}})
    )
    assert await fetch_goal() is None
