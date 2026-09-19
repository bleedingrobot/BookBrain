import httpx
import pytest
import respx

from app.providers.metadata.hardcover import ENDPOINT
from app.services.hardcover_prompts_service import fetch_prompts


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "tok")


@respx.mock
async def test_fetch_prompts_joins_questions_to_their_books() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        q = json.loads(request.content)["query"]
        if "order_by" in q:  # the top-prompts list
            return httpx.Response(
                200,
                json={
                    "data": {
                        "prompts": [
                            {"id": 1, "question": "Best sense of place?", "slug": "place"},
                            {"id": 2, "question": "Unreliable narrator?", "slug": "narrator"},
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "prompts": [
                        {"id": 1, "prompt_books": [{"book_id": 10}, {"book_id": 11}]},
                        {"id": 2, "prompt_books": [{"book_id": 11}]},
                    ]
                }
            },
        )

    respx.post(ENDPOINT).mock(side_effect=handler)
    out = await fetch_prompts()
    assert out == [
        {"id": 1, "question": "Best sense of place?", "slug": "place", "bookIds": [10, 11]},
        {"id": 2, "question": "Unreliable narrator?", "slug": "narrator", "bookIds": [11]},
    ]


@respx.mock
async def test_fetch_prompts_empty_when_no_prompts() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {"prompts": []}}))
    assert await fetch_prompts() == []


async def test_fetch_prompts_no_token(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "hardcover_api_token", "")
    assert await fetch_prompts() == []
