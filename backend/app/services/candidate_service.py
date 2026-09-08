import asyncio

from app.core.config import get_settings
from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.google_books import GoogleBooksProvider
from app.providers.metadata.hardcover import HardcoverProvider
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

    async def resolve_author_person_id(self, name: str | None) -> int | None:
        """prompts/28 Phase 2 — delegate to the Hardcover provider (if
        configured) for an author name's canonical "person id". None when
        there's no Hardcover provider or it can't resolve one; the caller
        then falls back to name-only author matching."""
        if not name:
            return None
        for provider in self._providers:
            if isinstance(provider, HardcoverProvider):
                return await provider.resolve_person_id(name)
        return None

    async def _query_all(self, call) -> list[MetadataCandidate]:
        # Providers are independent network calls (httpx.AsyncClient) — no
        # reason to wait for Google Books before even starting Open Library.
        results_per_provider = await asyncio.gather(*(call(provider) for provider in self._providers))
        return [candidate for results in results_per_provider for candidate in results]


def default_candidate_service() -> CandidateService:
    settings = get_settings()
    providers: list[BookMetadataProvider] = [
        GoogleBooksProvider(api_key=settings.google_books_api_key),
        OpenLibraryProvider(),
    ]
    # Hardcover is opt-in: only added when a token is configured, so an
    # install without one behaves exactly as before (no extra request,
    # no dependency on a beta API).
    if settings.hardcover_api_token:
        providers.append(HardcoverProvider(token=settings.hardcover_api_token))
    return CandidateService(providers=providers)
