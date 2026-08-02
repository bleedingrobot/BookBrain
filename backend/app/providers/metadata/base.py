from abc import ABC, abstractmethod

from app.providers.metadata.types import MetadataCandidate


class BookMetadataProvider(ABC):
    """SPEC.md: build this interface now, implement only Google Books and
    Open Library behind it for v1 — no plugin system yet."""

    name: str

    @abstractmethod
    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]: ...

    @abstractmethod
    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]: ...
