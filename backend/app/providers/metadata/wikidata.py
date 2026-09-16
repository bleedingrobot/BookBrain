import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
SEARCH_ENDPOINT = "https://www.wikidata.org/w/api.php"

# Wikimedia asks every API consumer to identify itself (unlabelled traffic is
# the first thing rate-limited) — no key involved, just a descriptive header.
_USER_AGENT = "BookBrain/1.0 (self-hosted personal library manager)"

# The one thing the other providers can't give us directly: structured series
# membership *and* position, as a qualifier on the "part of the series"
# statement rather than buried in a free-text field. wdt: shortcuts give the
# simple value; the p:/ps:/pq: triple is needed to reach that qualifier.
_DETAIL_WHERE = """
  OPTIONAL {{ ?item wdt:P50 ?author . }}
  OPTIONAL {{ ?item wdt:P212 ?isbn13v . }}
  OPTIONAL {{ ?item wdt:P957 ?isbn10v . }}
  OPTIONAL {{
    ?item p:P179 ?seriesStmt .
    ?seriesStmt ps:P179 ?series .
    OPTIONAL {{ ?seriesStmt pq:P1545 ?ordinal . }}
  }}
  OPTIONAL {{ ?item wdt:P577 ?pubdate . }}
  OPTIONAL {{ ?item wdt:P136 ?genre . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul". }}
"""

_ISBN_QUERY = """
SELECT ?item ?itemLabel ?authorLabel ?isbn13v ?isbn10v ?seriesLabel ?ordinal ?pubdate ?genreLabel WHERE {{
  VALUES ?isbn {{ "{isbn}" }}
  {{ ?item wdt:P212 ?isbn . }} UNION {{ ?item wdt:P957 ?isbn . }}
  {where}
}}
LIMIT 50
"""

_ENTITY_QUERY = """
SELECT ?item ?itemLabel ?authorLabel ?isbn13v ?isbn10v ?seriesLabel ?ordinal ?pubdate ?genreLabel WHERE {{
  VALUES ?item {{ {items} }}
  {where}
}}
LIMIT 100
"""


class WikidataProvider(BookMetadataProvider):
    """Free, no API key. Structured "part of the series" + position-in-series
    (a qualifier the free-text series fields the other providers expose don't
    carry), plus one more independent vote on title/author for the confidence
    scorer's provider-agreement check (confidence_service.py)."""

    name = "wikidata"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": _USER_AGENT}
        )

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        query = _ISBN_QUERY.format(isbn=isbn, where=_DETAIL_WHERE)
        bindings = await self._run_sparql(query)
        return _bindings_to_candidates(bindings, self.name)

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        search_term = f"{title} {author}" if author else title
        qids = await self._search_entity_ids(search_term)
        if not qids:
            return []
        items = " ".join(f"wd:{qid}" for qid in qids)
        query = _ENTITY_QUERY.format(items=items, where=_DETAIL_WHERE)
        bindings = await self._run_sparql(query)
        return _bindings_to_candidates(bindings, self.name)

    async def _search_entity_ids(self, term: str) -> list[str]:
        params = {
            "action": "wbsearchentities",
            "search": term,
            "language": "en",
            "type": "item",
            "limit": "5",
            "format": "json",
        }
        try:
            response = await self._client.get(SEARCH_ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return [hit["id"] for hit in response.json().get("search", []) if hit.get("id")]

    async def _run_sparql(self, query: str) -> list[dict]:
        try:
            response = await self._client.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        try:
            return response.json()["results"]["bindings"]
        except (KeyError, ValueError):
            return []


def _val(binding: dict, key: str) -> str | None:
    v = binding.get(key)
    return v.get("value") if v else None


def _qid_from_uri(uri: str | None) -> str | None:
    """``http://www.wikidata.org/entity/Q170622`` -> ``Q170622`` — the join
    key across rows, since a book with several authors or genres comes back
    as one row per combination and needs folding back into one candidate."""
    if not uri:
        return None
    return uri.rsplit("/", 1)[-1]


def _bindings_to_candidates(bindings: list[dict], source: str) -> list[MetadataCandidate]:
    by_item: dict[str, dict] = {}
    order: list[str] = []
    for b in bindings:
        item = _qid_from_uri(_val(b, "item"))
        if not item:
            continue
        if item not in by_item:
            by_item[item] = {
                "title": _val(b, "itemLabel"),
                "authors": set(),
                "isbn13": None,
                "isbn10": None,
                "series": None,
                "ordinal": None,
                "pubdate": None,
                "genres": set(),
            }
            order.append(item)
        entry = by_item[item]
        author = _val(b, "authorLabel")
        if author:
            entry["authors"].add(author)
        entry["isbn13"] = entry["isbn13"] or _val(b, "isbn13v")
        entry["isbn10"] = entry["isbn10"] or _val(b, "isbn10v")
        if entry["series"] is None:
            series = _val(b, "seriesLabel")
            if series:
                entry["series"] = series
                entry["ordinal"] = _val(b, "ordinal")
        entry["pubdate"] = entry["pubdate"] or _val(b, "pubdate")
        genre = _val(b, "genreLabel")
        if genre:
            entry["genres"].add(genre)

    candidates: list[MetadataCandidate] = []
    for item in order:
        e = by_item[item]
        ordinal: float | None = None
        if e["ordinal"]:
            try:
                ordinal = float(e["ordinal"])
            except ValueError:
                ordinal = None
        # "2008-01-01T00:00:00Z" -> "2008-01-01" — a bare year is common too
        # and already fine as-is.
        pubdate = e["pubdate"].split("T", 1)[0] if e["pubdate"] else None
        candidates.append(
            MetadataCandidate(
                title=e["title"],
                authors=sorted(e["authors"]),
                series=e["series"],
                series_number=ordinal,
                genre=next(iter(e["genres"]), None),
                first_published=pubdate,
                isbn13=e["isbn13"],
                isbn10=e["isbn10"],
                source=source,
            )
        )
    return candidates
