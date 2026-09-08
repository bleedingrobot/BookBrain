import httpx
import pytest
import respx

from app.providers.metadata.hardcover import (
    ENDPOINT,
    HardcoverProvider,
    HardcoverRateLimited,
    _TokenBucket,
    hardcover_graphql,
)


def _fast_bucket(provider: HardcoverProvider) -> None:
    # Don't actually sleep out the token bucket in tests.
    provider._bucket._rate = 1_000_000.0
    provider._bucket._tokens = 1_000_000.0


@respx.mock
async def test_search_by_isbn_returns_candidate_with_curated_series() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "editions": [
                        {
                            "isbn_13": "9780765311788",
                            "isbn_10": "0765311788",
                            "book": {
                                "id": 42,
                                "title": "The Final Empire",
                                "subtitle": None,
                                "description": "Ash falls from the sky.",
                                "release_year": 2006,
                                "contributions": [{"author": {"name": "Brandon Sanderson"}}],
                                "book_series": [
                                    {"position": 1, "series": {"name": "Mistborn"}}
                                ],
                            },
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        results = await provider.search_by_isbn("9780765311788")

    assert len(results) == 1
    c = results[0]
    assert c.title == "The Final Empire"
    assert c.authors == ["Brandon Sanderson"]
    assert c.series == "Mistborn"
    assert c.series_number == 1.0
    assert c.isbn13 == "9780765311788"
    assert c.first_published == "2006"
    assert c.source == "hardcover"


@respx.mock
async def test_search_by_isbn_without_series_does_not_crash() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "editions": [
                        {
                            "isbn_13": "9780000000001",
                            "isbn_10": None,
                            "book": {
                                "id": 7,
                                "title": "A Standalone",
                                "contributions": [{"author": {"name": "Someone"}}],
                                "book_series": [],
                            },
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        results = await provider.search_by_isbn("9780000000001")

    assert results[0].series is None
    assert results[0].series_number is None


@respx.mock
async def test_search_by_title_author_parses_typesense_hits() -> None:
    # Real Typesense document shape: featured_series is a nested object, and
    # `isbns` is an unordered pile of every edition's ISBN (not used).
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "search": {
                        "results": {
                            "found": 1,
                            "hits": [
                                {
                                    "document": {
                                        "title": "Leviathan Wakes",
                                        "author_names": ["James S. A. Corey"],
                                        "featured_series": {
                                            "position": 1.0,
                                            "series": {"name": "The Expanse", "id": 42},
                                        },
                                        "featured_series_position": 1.0,
                                        "isbns": ["0316129089", "9780316129084"],
                                        "release_year": 2011,
                                        "description": "The solar system is colonised.",
                                    }
                                }
                            ],
                        }
                    }
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        results = await provider.search_by_title_author("Leviathan Wakes", "James S. A. Corey")

    assert len(results) == 1
    c = results[0]
    assert c.title == "Leviathan Wakes"
    assert c.series == "The Expanse"
    assert c.series_number == 1.0
    assert c.first_published == "2011"
    assert c.isbn13 is None  # search path never sets an ISBN
    assert c.source == "hardcover"


@respx.mock
async def test_search_hit_without_series_is_fine() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "search": {
                        "results": {
                            "hits": [
                                {
                                    "document": {
                                        "title": "A Standalone",
                                        "author_names": ["Someone"],
                                        "featured_series": {},
                                        "featured_series_position": None,
                                        "series_names": [],
                                    }
                                }
                            ]
                        }
                    }
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        results = await provider.search_by_title_author("A Standalone", None)

    assert results[0].series is None
    assert results[0].series_number is None


@respx.mock
async def test_graphql_errors_response_yields_empty() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "field 'foo' not found"}]}
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        assert await provider.search_by_isbn("9780765311788") == []


@respx.mock
async def test_rate_limited_retries_once_then_gives_up() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={})
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        results = await provider.search_by_isbn("9780765311788")

    assert results == []
    assert route.call_count == 2


@respx.mock
async def test_daily_limit_raises_and_provider_degrades_to_empty() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "60000", "x-ratelimit-daily-remaining": "0"},
            json={},
        )
    )
    bucket = _TokenBucket(rate_per_sec=1e6, burst=1e6)

    async with httpx.AsyncClient() as client:
        # the shared helper surfaces the daily limit so a bulk caller can stop
        with pytest.raises(HardcoverRateLimited):
            await hardcover_graphql(client, "t", "query{x}", {}, bucket)
        assert route.call_count == 1  # no pointless retry

        # a live scan through the provider still just degrades to []
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        assert await provider.search_by_isbn("9780765311788") == []


async def test_no_token_makes_no_request() -> None:
    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="", client=client)
        # respx is not active here; a real request would raise. It must not.
        assert await provider.search_by_isbn("9780765311788") == []
        assert await provider.search_by_title_author("Dune", "Herbert") == []


@respx.mock
async def test_response_is_cached_per_key() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"editions": [{"isbn_13": "9780765311788", "book": {"id": 1, "title": "X", "book_series": []}}]}},
        )
    )

    async with httpx.AsyncClient() as client:
        provider = HardcoverProvider(token="t", client=client)
        _fast_bucket(provider)
        await provider.search_by_isbn("9780765311788")
        await provider.search_by_isbn("9780765311788")

    assert route.call_count == 1
