import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate, split_series_and_number

BOOKS_ENDPOINT = "https://openlibrary.org/api/books"
SEARCH_ENDPOINT = "https://openlibrary.org/search.json"
OL_ORIGIN = "https://openlibrary.org"

# Search-doc `fields` we actually read — Open Library returns a huge doc by
# default, and `series` / `subject` are not in the trimmed set unless asked for.
_SEARCH_FIELDS = "title,author_name,first_publish_year,language,isbn,series,subject"


class OpenLibraryProvider(BookMetadataProvider):
    name = "open_library"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)
        # One follow-up GET per edition key, at most, for the run's lifetime.
        self._edition_series_cache: dict[str, str | None] = {}

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        params = {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}
        try:
            response = await self._client.get(BOOKS_ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        entry = response.json().get(f"ISBN:{isbn}")
        if not entry:
            return []
        candidate = self._book_to_candidate(entry)
        if candidate.series is None:
            # jscmd=data doesn't carry a clean series field; the edition record
            # (`/books/OL…M.json`) does. One extra request, cached.
            edition_series = await self._edition_series(entry.get("key"))
            if edition_series:
                candidate.series, candidate.series_number = split_series_and_number(
                    edition_series
                )
        return [candidate]

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        params: dict[str, str | int] = {"title": title, "limit": 5, "fields": _SEARCH_FIELDS}
        if author:
            params["author"] = author

        try:
            response = await self._client.get(SEARCH_ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        return [self._doc_to_candidate(doc) for doc in response.json().get("docs", [])[:5]]

    async def _edition_series(self, key: str | None) -> str | None:
        """`key` is like ``/books/OL61173763M`` (from the jscmd=data payload)."""
        if not key:
            return None
        if key in self._edition_series_cache:
            return self._edition_series_cache[key]
        series: str | None = None
        try:
            response = await self._client.get(f"{OL_ORIGIN}{key}.json")
            response.raise_for_status()
            raw = response.json().get("series") or []
            series = next((s for s in raw if isinstance(s, str) and s.strip()), None)
        except (httpx.HTTPError, ValueError):
            series = None
        self._edition_series_cache[key] = series
        return series

    def _book_to_candidate(self, entry: dict) -> MetadataCandidate:
        identifiers = entry.get("identifiers", {})
        series, series_number = _series_from_subjects(entry.get("subjects"))
        return MetadataCandidate(
            title=entry.get("title"),
            authors=[a["name"] for a in entry.get("authors", []) if a.get("name")],
            series=series,
            series_number=series_number,
            genre=_genre_from_subjects(entry.get("subjects")),
            first_published=entry.get("publish_date"),
            isbn13=(identifiers.get("isbn_13") or [None])[0],
            isbn10=(identifiers.get("isbn_10") or [None])[0],
            source=self.name,
        )

    def _doc_to_candidate(self, doc: dict) -> MetadataCandidate:
        isbns = doc.get("isbn") or []
        languages = doc.get("language") or []
        first_year = doc.get("first_publish_year")
        raw_series = doc.get("series") or []
        series, series_number = split_series_and_number(
            next((s for s in raw_series if isinstance(s, str) and s.strip()), None)
        )
        return MetadataCandidate(
            title=doc.get("title"),
            authors=doc.get("author_name", []),
            series=series,
            series_number=series_number,
            genre=_genre_from_subjects(doc.get("subject")),
            first_published=str(first_year) if first_year else None,
            language=languages[0] if languages else None,
            isbn13=next((i for i in isbns if len(i) == 13), None),
            isbn10=next((i for i in isbns if len(i) == 10), None),
            source=self.name,
        )


def _subject_names(subjects: object) -> list[str]:
    """Open Library subjects come as bare strings (search `subject`) or as
    ``{"name": ...}`` dicts (jscmd=data `subjects`)."""
    if not isinstance(subjects, list):
        return []
    out: list[str] = []
    for item in subjects:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(item["name"])
    return out


def _series_from_subjects(subjects: object) -> tuple[str | None, float | None]:
    for name in _subject_names(subjects):
        if name.lower().startswith("series:"):
            return split_series_and_number(name.split(":", 1)[1].strip())
    return None, None


def _genre_from_subjects(subjects: object) -> str | None:
    for name in _subject_names(subjects):
        if name.lower().startswith("genre:"):
            return name.split(":", 1)[1].strip() or None
    return None
