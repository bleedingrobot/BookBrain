import re

import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate, split_series_and_number

ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

# "The Final Empire (Mistborn, #1)" / "Leviathan Wakes (The Expanse Book 1)" —
# Google Books routinely embeds the series in a trailing parenthetical on the
# title. seriesInfo carries a reliable *number* but no name, so this is where
# the name comes from.
_TITLE_SERIES_PAREN_RE = re.compile(
    r"""\(
        \s*(?P<name>[^()]+?)\s*
        (?:[,;:]?\s*(?:\#|book\s+|bk\.?\s*|vol(?:ume)?\.?\s*|no\.?\s*|part\s+)\s*
           (?P<num>\d+(?:\.\d+)?)\s*)?
        \)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class GoogleBooksProvider(BookMetadataProvider):
    name = "google_books"

    def __init__(self, api_key: str = "", client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        return await self._search(f"isbn:{isbn}")

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        query = f"intitle:{title}"
        if author:
            query += f"+inauthor:{author}"
        return await self._search(query)

    async def _search(self, query: str) -> list[MetadataCandidate]:
        params: dict[str, str | int] = {"q": query, "maxResults": 5}
        if self._api_key:
            params["key"] = self._api_key

        try:
            response = await self._client.get(ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        return [self._to_candidate(item) for item in response.json().get("items", [])]

    def _to_candidate(self, item: dict) -> MetadataCandidate:
        info = item.get("volumeInfo", {})
        isbn13 = isbn10 = None
        for ident in info.get("industryIdentifiers", []):
            if ident.get("type") == "ISBN_13":
                isbn13 = ident.get("identifier")
            elif ident.get("type") == "ISBN_10":
                isbn10 = ident.get("identifier")

        series, series_number = _series_from_volume_info(info)

        return MetadataCandidate(
            title=info.get("title"),
            authors=info.get("authors", []),
            series=series,
            series_number=series_number,
            genre=_first_genre(info.get("categories")),
            description=info.get("description"),
            language=info.get("language"),
            first_published=info.get("publishedDate"),
            isbn13=isbn13,
            isbn10=isbn10,
            source=self.name,
        )


def _series_number_from_series_info(info: dict) -> float | None:
    series_info = info.get("seriesInfo") or {}
    raw = series_info.get("bookDisplayNumber")
    if raw is None:
        volume_series = series_info.get("volumeSeries") or []
        raw = volume_series[0].get("orderNumber") if volume_series else None
    try:
        return float(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _series_from_volume_info(info: dict) -> tuple[str | None, float | None]:
    number = _series_number_from_series_info(info)

    # Require a number in the parenthetical: "(Mistborn, #1)" is a series,
    # "(Movie Tie-in Edition)" / "(Unabridged)" is not.
    match = _TITLE_SERIES_PAREN_RE.search(info.get("title") or "")
    if match and match.group("num"):
        name, parsed = split_series_and_number(match.group("name"), float(match.group("num")))
        if name:
            return name, number if number is not None else parsed

    # A subtitle like "Mistborn Book 1" is the series spelled out; a plain
    # descriptive subtitle ("A Novel") carries no number and is ignored.
    name, parsed = split_series_and_number(info.get("subtitle"))
    if name and parsed is not None:
        return name, number if number is not None else parsed

    # A bare number with no name we can trust is noise, not signal — drop it.
    return None, None


def _first_genre(categories: object) -> str | None:
    if not isinstance(categories, list) or not categories:
        return None
    first = categories[0]
    if not isinstance(first, str) or not first.strip():
        return None
    # BISAC headings are "Fiction / Fantasy / Epic" — keep the leaf.
    return first.split("/")[-1].strip() or None
