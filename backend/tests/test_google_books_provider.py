import httpx
import respx

from app.providers.metadata.google_books import ENDPOINT, GoogleBooksProvider


@respx.mock
async def test_search_by_isbn_returns_candidate() -> None:
    respx.get(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Dune",
                            "authors": ["Frank Herbert"],
                            "language": "en",
                            "publishedDate": "1965",
                            "industryIdentifiers": [
                                {"type": "ISBN_13", "identifier": "9780441172719"},
                                {"type": "ISBN_10", "identifier": "0441172717"},
                            ],
                        }
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = GoogleBooksProvider(client=client)
        results = await provider.search_by_isbn("9780441172719")

    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].authors == ["Frank Herbert"]
    assert results[0].isbn13 == "9780441172719"
    assert results[0].isbn10 == "0441172717"
    assert results[0].source == "google_books"


@respx.mock
async def test_search_by_title_author_builds_query() -> None:
    route = respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json={"items": []}))

    async with httpx.AsyncClient() as client:
        provider = GoogleBooksProvider(client=client)
        results = await provider.search_by_title_author("Dune", "Frank Herbert")

    assert results == []
    sent_query = dict(httpx.QueryParams(route.calls.last.request.url.query))["q"]
    assert sent_query == "intitle:Dune+inauthor:Frank Herbert"


@respx.mock
async def test_no_results_returns_empty_list() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient() as client:
        provider = GoogleBooksProvider(client=client)
        results = await provider.search_by_isbn("0000000000000")

    assert results == []


@respx.mock
async def test_http_error_returns_empty_list_not_raise() -> None:
    respx.get(ENDPOINT).mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        provider = GoogleBooksProvider(client=client)
        results = await provider.search_by_isbn("9780441172719")

    assert results == []


@respx.mock
async def test_api_key_included_when_configured() -> None:
    route = respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json={"items": []}))

    async with httpx.AsyncClient() as client:
        provider = GoogleBooksProvider(api_key="secret-key", client=client)
        await provider.search_by_isbn("9780441172719")

    assert dict(httpx.QueryParams(route.calls.last.request.url.query))["key"] == "secret-key"
