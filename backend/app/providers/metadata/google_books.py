import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate

ENDPOINT = "https://www.googleapis.com/books/v1/volumes"


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

        return MetadataCandidate(
            title=info.get("title"),
            authors=info.get("authors", []),
            description=info.get("description"),
            language=info.get("language"),
            first_published=info.get("publishedDate"),
            isbn13=isbn13,
            isbn10=isbn10,
            source=self.name,
        )
