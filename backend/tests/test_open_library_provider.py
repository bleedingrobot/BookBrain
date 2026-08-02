import httpx
import respx

from app.providers.metadata.open_library import BOOKS_ENDPOINT, SEARCH_ENDPOINT, OpenLibraryProvider


@respx.mock
async def test_search_by_isbn_returns_candidate() -> None:
    respx.get(BOOKS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "ISBN:9780441172719": {
                    "title": "Dune",
                    "authors": [{"name": "Frank Herbert"}],
                    "publish_date": "1965",
                    "identifiers": {"isbn_13": ["9780441172719"], "isbn_10": ["0441172717"]},
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = OpenLibraryProvider(client=client)
        results = await provider.search_by_isbn("9780441172719")

    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].authors == ["Frank Herbert"]
    assert results[0].isbn13 == "9780441172719"
    assert results[0].source == "open_library"


@respx.mock
async def test_search_by_isbn_missing_key_returns_empty() -> None:
    respx.get(BOOKS_ENDPOINT).mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient() as client:
        provider = OpenLibraryProvider(client=client)
        results = await provider.search_by_isbn("0000000000000")

    assert results == []


@respx.mock
async def test_search_by_title_author_maps_docs() -> None:
    respx.get(SEARCH_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "docs": [
                    {
                        "title": "Dune",
                        "author_name": ["Frank Herbert"],
                        "first_publish_year": 1965,
                        "language": ["eng"],
                        "isbn": ["0441172717", "9780441172719"],
                    }
                ]
            },
        )
    )

    async with httpx.AsyncClient() as client:
        provider = OpenLibraryProvider(client=client)
        results = await provider.search_by_title_author("Dune", "Frank Herbert")

    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].isbn13 == "9780441172719"
    assert results[0].isbn10 == "0441172717"
    assert results[0].first_published == "1965"


@respx.mock
async def test_http_error_returns_empty_list_not_raise() -> None:
    respx.get(SEARCH_ENDPOINT).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        provider = OpenLibraryProvider(client=client)
        results = await provider.search_by_title_author("Dune", None)

    assert results == []
