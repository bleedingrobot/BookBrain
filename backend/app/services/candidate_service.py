from app.core.config import get_settings
from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.google_books import GoogleBooksProvider
from app.providers.metadata.open_library import OpenLibraryProvider
from app.providers.metadata.types import MetadataCandidate


class CandidateService:
    """SPEC.md §5: ISBN lookup first, title+author fallback second. Queries
    *all* providers at whichever stage succeeds (not just the first
    responder) — the confidence system's conflict penalty (§13) needs
    multiple providers' answers to detect disagreement."""

    def __init__(self, providers: list[BookMetadataProvider]) -> None:
        self._providers = providers

    async def generate_candidates(
        self,
        *,
        isbn13: str | None = None,
        isbn10: str | None = None,
        title: str | None = None,
        authors: str | None = None,
    ) -> list[MetadataCandidate]:
        isbn = isbn13 or isbn10
        if isbn:
            results = await self._query_all(lambda p: p.search_by_isbn(isbn))
            if results:
                return results

        if title:
            return await self._query_all(lambda p: p.search_by_title_author(title, authors))

        return []

    async def _query_all(self, call) -> list[MetadataCandidate]:
        results: list[MetadataCandidate] = []
        for provider in self._providers:
            results.extend(await call(provider))
        return results


def default_candidate_service() -> CandidateService:
    settings = get_settings()
    return CandidateService(
        providers=[
            GoogleBooksProvider(api_key=settings.google_books_api_key),
            OpenLibraryProvider(),
        ]
    )
