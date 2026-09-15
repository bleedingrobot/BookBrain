from abc import ABC, abstractmethod
from pathlib import Path

from app.providers.acquisition.types import AcquisitionResult


class AcquisitionProvider(ABC):
    """Fixed interface, concrete implementations, no plugin system — same
    idiom as BookMetadataProvider (providers/metadata/base.py). Unlike a
    metadata provider, search()/download() here ARE allowed to raise
    AcquisitionError (and its subclasses) — callers need to tell "no
    results" apart from "the source is down" for 429/503 mapping."""

    name: str

    @abstractmethod
    def is_enabled(self) -> bool:
        """Sync, config-only — a composition-root-time decision (is this
        provider in the list at all), not a per-call liveness check. A
        provider that's enabled but temporarily unreachable should raise
        AcquisitionUnavailable from search()/download() instead."""
        ...

    @abstractmethod
    async def search(self, query: str) -> list[AcquisitionResult]: ...

    @abstractmethod
    async def download(self, handle: str) -> Path:
        """`handle` is whatever that provider's own AcquisitionResult.full
        contained — opaque outside the provider that issued it."""
        ...
