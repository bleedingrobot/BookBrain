import json

import httpx
import pytest
import respx

from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_lists_service import fetch_list_candidates


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


def _book(bid, title, author="A", isbn=None):
    return {
        "book": {
            "id": bid,
            "title": title,
            "contributions": [{"author": {"name": author}}] if author else [],
            "editions": [{"isbn_13": isbn}] if isbn else [],
        }
    }


@respx.mock
async def test_owned_books_drive_candidates_from_the_rest_of_the_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = json.loads(request.content)["query"]
        if "list_books:" in q:  # the lists query
            return httpx.Response(
                200,
                json={"data": {"lists": [{"id": 3, "name": "Best SFF", "slug": "sff", "books_count": 4}]}},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "lists": [
                        {
                            "id": 3,
                            "name": "Best SFF",
                            "slug": "sff",
                            "list_books": [
                                _book(10, "Owned One"),
                                _book(11, "Owned Two"),
                                _book(20, "New One", "Z", "9990000000001"),
                                _book(21, "New Two", "Y"),
                                _book(21, "New Two", "Y"),  # dup → collapsed
                            ],
                        }
                    ]
                }
            },
        )

    respx.post(ENDPOINT).mock(side_effect=handler)
    result = await fetch_list_candidates({10, 11})

    assert result["lists"] == [{"name": "Best SFF", "slug": "sff", "owned": 2, "total": 5}]
    assert [c["title"] for c in result["candidates"]] == ["New One", "New Two"]
    assert result["candidates"][0] == {
        "title": "New One",
        "author": "Z",
        "isbn13": "9990000000001",
        "fromList": "Best SFF",
    }


@respx.mock
async def test_no_lists_returns_empty() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {"lists": []}}))
    assert await fetch_list_candidates({1, 2}) == {"lists": [], "candidates": []}


async def test_no_owned_ids_is_a_noop() -> None:
    assert await fetch_list_candidates(set()) == {"lists": [], "candidates": []}
