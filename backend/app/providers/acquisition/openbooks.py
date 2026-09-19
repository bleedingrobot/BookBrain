from pathlib import Path

from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.types import AcquisitionResult
from app.services import openbooks_service


class OpenBooksProvider(AcquisitionProvider):
    """Thin adapter over openbooks_service's module-singleton client — the
    module itself keeps its own lock/reconnect state machine and its own
    test suite; this class doesn't reimplement any of that."""

    name = "openbooks"

    def is_enabled(self) -> bool:
        return openbooks_service.is_enabled()

    def is_busy(self) -> bool:
        """Not part of AcquisitionProvider — OpenBooks-only (single-connection
        lock). Callers that need this isinstance()-check for it, same as
        CandidateService.resolve_author_person_id does for HardcoverProvider."""
        return openbooks_service.is_busy()

    async def search(self, query: str) -> list[AcquisitionResult]:
        outcome = await openbooks_service.search(query)
        return outcome.results

    async def download(self, handle: str) -> Path:
        return await openbooks_service.download(handle)
