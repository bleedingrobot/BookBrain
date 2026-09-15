"""Composition root for acquisition providers — mirrors where
candidate_service.default_candidate_service() sits relative to
providers/metadata/. Fixed list, concrete implementations, no plugin
system (same idiom as the metadata-provider stack)."""

from app.core.config import get_settings
from app.providers.acquisition.annas_archive import AnnasArchiveProvider
from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.libgen import LibgenProvider
from app.providers.acquisition.openbooks import OpenBooksProvider


def default_acquisition_providers() -> list[AcquisitionProvider]:
    settings = get_settings()
    providers: list[AcquisitionProvider] = [OpenBooksProvider()]
    if settings.annas_archive_enabled:
        providers.append(AnnasArchiveProvider())
    if settings.libgen_enabled:
        providers.append(LibgenProvider())
    return [p for p in providers if p.is_enabled()]
