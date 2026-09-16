import httpx
import respx

from app.providers.metadata.wikidata import SEARCH_ENDPOINT, SPARQL_ENDPOINT, WikidataProvider


def _binding(**kwargs: str) -> dict:
    return {k: {"type": "literal", "value": v} for k, v in kwargs.items()}


def _uri(key: str, value: str) -> dict:
    return {key: {"type": "uri", "value": value}}


@respx.mock
async def test_search_by_isbn_returns_series_and_ordinal() -> None:
    respx.get(SPARQL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            **_uri("item", "http://www.wikidata.org/entity/Q1785641"),
                            **_binding(
                                itemLabel="The Final Empire",
                                authorLabel="Brandon Sanderson",
                                isbn13v="9780765350381",
                                seriesLabel="Mistborn",
                                ordinal="1",
                                pubdate="2006-07-17T00:00:00Z",
                                genreLabel="high fantasy",
                            ),
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_isbn("9780765350381")

    assert len(results) == 1
    r = results[0]
    assert r.title == "The Final Empire"
    assert r.authors == ["Brandon Sanderson"]
    assert r.series == "Mistborn"
    assert r.series_number == 1.0
    assert r.first_published == "2006-07-17"
    assert r.genre == "high fantasy"
    assert r.isbn13 == "9780765350381"
    assert r.source == "wikidata"


@respx.mock
async def test_multiple_authors_and_genres_fold_into_one_candidate() -> None:
    respx.get(SPARQL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            **_uri("item", "http://www.wikidata.org/entity/Q123"),
                            **_binding(itemLabel="Good Omens", authorLabel="Terry Pratchett"),
                        },
                        {
                            **_uri("item", "http://www.wikidata.org/entity/Q123"),
                            **_binding(itemLabel="Good Omens", authorLabel="Neil Gaiman"),
                        },
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_isbn("9780575048837")

    assert len(results) == 1
    assert results[0].authors == ["Neil Gaiman", "Terry Pratchett"]  # folded + sorted


@respx.mock
async def test_no_isbn_match_returns_empty() -> None:
    respx.get(SPARQL_ENDPOINT).mock(return_value=httpx.Response(200, json={"results": {"bindings": []}}))

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_isbn("0000000000000")

    assert results == []


@respx.mock
async def test_title_author_search_uses_search_api_then_sparql() -> None:
    search_route = respx.get(SEARCH_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"search": [{"id": "Q1785641", "label": "The Final Empire"}]})
    )
    sparql_route = respx.get(SPARQL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            **_uri("item", "http://www.wikidata.org/entity/Q1785641"),
                            **_binding(itemLabel="The Final Empire", authorLabel="Brandon Sanderson"),
                        }
                    ]
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_title_author(
            "The Final Empire", "Brandon Sanderson"
        )

    assert len(results) == 1
    assert results[0].title == "The Final Empire"
    assert search_route.calls.last.request.url.params["search"] == "The Final Empire Brandon Sanderson"
    assert "Q1785641" in sparql_route.calls.last.request.url.params["query"]


@respx.mock
async def test_title_author_search_with_no_entity_hits_skips_sparql() -> None:
    respx.get(SEARCH_ENDPOINT).mock(return_value=httpx.Response(200, json={"search": []}))
    sparql_route = respx.get(SPARQL_ENDPOINT).mock(return_value=httpx.Response(200, json={"results": {"bindings": []}}))

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_title_author("Nothing Like This", None)

    assert results == []
    assert sparql_route.call_count == 0


@respx.mock
async def test_http_error_on_sparql_returns_empty_not_raise() -> None:
    respx.get(SPARQL_ENDPOINT).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_isbn("9780441172719")

    assert results == []


@respx.mock
async def test_http_error_on_search_returns_empty_not_raise() -> None:
    respx.get(SEARCH_ENDPOINT).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_title_author("Dune", "Frank Herbert")

    assert results == []


@respx.mock
async def test_malformed_sparql_response_returns_empty_not_raise() -> None:
    respx.get(SPARQL_ENDPOINT).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))

    async with httpx.AsyncClient() as client:
        results = await WikidataProvider(client=client).search_by_isbn("9780441172719")

    assert results == []


@respx.mock
async def test_sends_identifying_user_agent() -> None:
    route = respx.get(SPARQL_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )

    await WikidataProvider().search_by_isbn("9780441172719")

    assert "BookBrain" in route.calls.last.request.headers["User-Agent"]
