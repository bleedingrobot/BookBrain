import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate

BOOKS_ENDPOINT = "https://openlibrary.org/api/books"
SEARCH_ENDPOINT = "https://openlibrary.org/search.json"


class OpenLibraryProvider(BookMetadataProvider):
    name = "open_library"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        params = {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}
        try:
            response = await self._client.get(BOOKS_ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        entry = response.json().get(f"ISBN:{isbn}")
        return [self._book_to_candidate(entry)] if entry else []

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        params: dict[str, str | int] = {"title": title, "limit": 5}
        if author:
            params["author"] = author

        try:
            response = await self._client.get(SEARCH_ENDPOINT, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        return [self._doc_to_candidate(doc) for doc in response.json().get("docs", [])[:5]]

    def _book_to_candidate(self, entry: dict) -> MetadataCandidate:
        identifiers = entry.get("identifiers", {})
        return MetadataCandidate(
            title=entry.get("title"),
            authors=[a["name"] for a in entry.get("authors", []) if a.get("name")],
            first_published=entry.get("publish_date"),
            isbn13=(identifiers.get("isbn_13") or [None])[0],
            isbn10=(identifiers.get("isbn_10") or [None])[0],
            source=self.name,
        )

    def _doc_to_candidate(self, doc: dict) -> MetadataCandidate:
        isbns = doc.get("isbn") or []
        languages = doc.get("language") or []
        first_year = doc.get("first_publish_year")
        return MetadataCandidate(
            title=doc.get("title"),
            authors=doc.get("author_name", []),
            first_published=str(first_year) if first_year else None,
            language=languages[0] if languages else None,
            isbn13=next((i for i in isbns if len(i) == 13), None),
            isbn10=next((i for i in isbns if len(i) == 10), None),
            source=self.name,
        )
